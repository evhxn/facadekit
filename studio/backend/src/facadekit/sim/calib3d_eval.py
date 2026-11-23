"""Simulator evaluation of calib3d (spike S-11 Part A; docs/15 "Part A results", docs/18).

Every scene comes from ``sim.scene3d`` (seeded), is scanned with the real Gray-code patterns
and the real ``calib.decode``, and is solved with the real ``calib3d`` pipeline. Ground truth is
the projector pixel that really lights the surface point each camera pixel sees.

Mappings scored (all camera pixel -> projector pixel):

- ``plane``   one RANSAC homography, as ``calib.maps`` fits ``h_c2p`` (the plane mapping)
- ``dense``   the current dense map ``calib.maps.build_maps`` (``p2c``), inverted numerically
- ``model``   depth sensor surface + solved projector (no scan refinement)
- ``hybrid``  the 3D-aware mapping ``calib3d`` ships (per-plane homographies, scan-corrected
              surface + solved projector elsewhere)

Two regimes: ``covered`` (scored on every lit pixel, on a 4 px grid, all scan data used) and
``gaps`` (15 % of 96 px camera tiles have their scan data removed before fitting; scored only in
those tiles: what each mapping predicts where the scan has nothing).

Split: scenes with ``index % 3 == 0`` are held out; every constant in ``calib3d`` was chosen
looking at train scenes only. Results: ``calib3d_data/report.md`` and ``report.json``.

**Everything here is simulated. The depth-sensor model is ASSUMED, not measured.**

Run:  uv run python -m facadekit.sim.calib3d_eval run --out .calib3d_cache
      uv run python -m facadekit.sim.calib3d_eval report --data .calib3d_cache --out calib3d_data
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from facadekit.calib.decode import DecodeResult
from facadekit.calib.maps import build_maps
from facadekit.calib3d.capture_format import load_capture
from facadekit.calib3d.fusion import fuse
from facadekit.calib3d.projector import compare_to_truth
from facadekit.calib3d.solve import (
    SolveOptions,
    build_mapping,
    decode_capture,
    holdout_tiles,
    pool_blocks,
)
from facadekit.sim.scene3d import (
    SCENE_KINDS,
    DepthSensorModel,
    SceneKind,
    SessionOptions,
    make_scene,
    write_session,
)

BASE_SEED = 730_000
EVAL_STEP = 4
MAPPINGS = ("plane", "dense", "model", "hybrid")
OBJECT_OF_KIND = {"flat": "wall", "board": "board", "box": "box", "cylinder": "cylinder",
                  "stepped": "steps"}  # fmt: skip
SENS_KINDS: tuple[SceneKind, ...] = ("board", "box", "cylinder", "stepped")
RANGE_KINDS: tuple[SceneKind, ...] = ("board", "box", "cylinder")
RANGES_M = (1.0, 1.5, 2.5, 3.5, 5.0, 6.5, 8.0)


def held_out(index: int) -> bool:
    return index % 3 == 0


def variants() -> list[tuple[str, str, DepthSensorModel, SessionOptions, str]]:
    """(name, axis, sensor, session, object material) for the sensitivity runs."""
    base_s, base_p = DepthSensorModel(), SessionOptions()
    out = [("base", "base", base_s, base_p, "matte")]
    for k in (0.5, 2.0, 4.0):
        out.append(
            (f"noise x{k:g}", "depth noise", replace(base_s, noise_scale=k), base_p, "matte")
        )
    for rot, tr in ((0.05, 0.001), (0.5, 0.01), (1.0, 0.02)):
        session = replace(base_p, pose_rot_err_deg=rot, pose_trans_err_m=tr)
        out.append((f"pose {rot:g} deg / {tr * 1000:g} mm", "pose error", base_s, session, "matte"))
    for mat in ("dark", "glossy", "transparent"):
        out.append((f"object {mat}", "dropout", base_s, base_p, mat))
    out.append(("dropout x2, dark", "dropout", replace(base_s, dropout_scale=2.0), base_p, "dark"))
    out.append(("one shot only", "shots", base_s, replace(base_p, extra_shots=0), "matte"))
    return out


# ---------------------------------------------------------------- scoring


def dense_predict(p2c: np.ndarray, cam: np.ndarray, q0: np.ndarray, iters: int = 10) -> np.ndarray:
    """Invert the dense projector->camera map: q with p2c(q) = cam, Newton from q0.
    ``p2c`` is in pixel-corner coordinates; cam, q0 and the result in centre coordinates."""
    ph, pw = p2c.shape[:2]
    mu = np.ascontiguousarray(p2c[..., 0] - 0.5)
    mv = np.ascontiguousarray(p2c[..., 1] - 0.5)

    def f(q: np.ndarray) -> np.ndarray:
        n = len(q)
        cols = 1024
        rows = (n + cols - 1) // cols
        xf = np.zeros(rows * cols, np.float32)
        yf = np.zeros(rows * cols, np.float32)
        xf[:n], yf[:n] = q[:, 0], q[:, 1]
        x, y = xf.reshape(rows, cols), yf.reshape(rows, cols)
        u = cv2.remap(mu, x, y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()[:n]
        v = cv2.remap(mv, x, y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()[:n]
        return np.stack([u, v], axis=1).astype(np.float64)

    q = np.where(np.isfinite(q0), q0, [pw / 2, ph / 2]).astype(np.float64)
    lo, hi = np.array([0.0, 0.0]), np.array([pw - 1.0, ph - 1.0])
    q = np.clip(q, lo, hi)
    for _ in range(iters):
        r = f(q) - cam
        e = np.array([2.0, 0.0])
        jx = (f(np.clip(q + e, lo, hi)) - f(np.clip(q - e, lo, hi))) / 4.0
        jy = (f(np.clip(q + e[::-1], lo, hi)) - f(np.clip(q - e[::-1], lo, hi))) / 4.0
        det = jx[:, 0] * jy[:, 1] - jy[:, 0] * jx[:, 1]
        det = np.where(np.abs(det) < 1e-9, 1e-9, det)
        dx = (jy[:, 1] * r[:, 0] - jy[:, 0] * r[:, 1]) / det
        dy = (-jx[:, 1] * r[:, 0] + jx[:, 0] * r[:, 1]) / det
        step = np.stack([dx, dy], axis=1)
        step = np.clip(step, -200, 200)
        q = np.clip(q - step, lo, hi)
    return q


def _stats(err: np.ndarray) -> dict[str, float | int | None]:
    e = err[np.isfinite(err)]
    if len(e) == 0:
        return {"n": 0, "median": None, "p95": None}
    return {"n": int(len(e)), "median": float(np.median(e)), "p95": float(np.percentile(e, 95))}


def _mask_decode(dec: DecodeResult, keep: np.ndarray) -> DecodeResult:
    valid = dec.valid & keep
    c2p = dec.c2p.copy()
    c2p[~valid] = np.nan
    return replace(dec, c2p=c2p, valid=valid)


def score(
    mapping: Any, dense_p2c: np.ndarray | None, cam: np.ndarray, q_true: np.ndarray,
    groups: dict[str, np.ndarray], modes: tuple[str, ...],
) -> dict[str, dict[str, Any]]:  # fmt: skip
    out: dict[str, dict[str, Any]] = {}
    preds: dict[str, np.ndarray] = {}
    plane = mapping.predict(cam, "plane")
    for mode in modes:
        if mode == "dense":
            if dense_p2c is None:
                continue
            preds[mode] = dense_predict(dense_p2c, cam, plane)
        else:
            preds[mode] = plane if mode == "plane" else mapping.predict(cam, mode)
    for mode, pred in preds.items():
        err = np.linalg.norm(pred - q_true, axis=1)
        out[mode] = {g: _stats(err[m]) for g, m in groups.items()}
    return out


def eval_points(
    scene: Any, truth: dict[str, np.ndarray], step: int = EVAL_STEP
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Camera pixels (N, 2), true projector px (N, 2), object name (N,) of lit surface points."""
    lit = truth["lit"]
    grid = np.zeros_like(lit)
    grid[step // 2 :: step, step // 2 :: step] = True
    vs, us = np.nonzero(lit & grid)
    cam = np.stack([us, vs], axis=1).astype(np.float64)
    return cam, truth["q"][vs, us], scene.object_of(truth["prim"][vs, us])


def _groups(kind: str, obj: np.ndarray) -> dict[str, np.ndarray]:
    target = OBJECT_OF_KIND[kind]
    return {"object": obj == target, "all": np.ones(len(obj), bool)}


def run_scene(kind: SceneKind, index: int, *, sensitivity: bool, distance_m: float | None = None,
              tag: str = "main") -> list[dict[str, Any]]:  # fmt: skip
    seed = BASE_SEED + SCENE_KINDS.index(kind) * 10_000 + index
    rows: list[dict[str, Any]] = []
    vlist = variants() if sensitivity else variants()[:1]
    tmp = Path(tempfile.mkdtemp(prefix="fk_c3d_"))
    opts = SolveOptions()
    try:
        base_scene = make_scene(kind, seed, distance_m=distance_m)
        truth = base_scene.camera_truth()
        cam, q_true, obj = eval_points(base_scene, truth)
        groups = _groups(kind, obj)
        dec: DecodeResult | None = None
        images: list[np.ndarray] | None = None
        for name, axis, sensor, session, material in vlist:
            scene = base_scene
            if material != "matte":
                scene = make_scene(kind, seed, distance_m=distance_m, object_material=material)
            if images is None or material != "matte":
                from facadekit.calib.patterns import PatternSpec

                spec = PatternSpec(scene.proj_intr.width, scene.proj_intr.height, session.skip_lsb)
                t_truth = truth if material == "matte" else scene.camera_truth()
                imgs = scene.render_graycode(spec, t_truth, np.random.default_rng(seed))
                if material == "matte":
                    images = imgs
            else:
                imgs = images
            d = tmp / f"v{len(rows)}"
            write_session(scene, d, sensor=sensor, session=session, graycode_images=imgs)
            cap = load_capture(d)
            t0 = time.monotonic()
            if dec is None or material != "matte":
                dec_v, corr, _ = decode_capture(cap, opts)
                if material == "matte":
                    dec = dec_v
            else:
                dec_v = dec
                corr = pool_blocks(dec_v, 1 << cap.graycode.pattern.skip_lsb)  # type: ignore[union-attr]
            surface = fuse(cap, opts.fusion)
            size = (scene.proj_intr.width, scene.proj_intr.height)
            mapping = build_mapping(corr, surface, size, opts)
            solve_s = time.monotonic() - t0
            gt = cap.ground_truth or {}
            k_true = np.array(scene.proj_intr.k)
            row: dict[str, Any] = {
                "tag": tag, "kind": kind, "index": index, "seed": seed,
                "heldout": held_out(index), "variant": name, "axis": axis,
                "distance_m": float(scene.params["distance_m"]),
                "projector_distance_m": float(scene.params["projector_distance_m"]),
                "status": mapping.solution.status,
                "solve": mapping.solution.to_json(),
                "planes": len(surface.planes), "groups": len(mapping.groups),
                "depth_coverage": surface.stats.get("coverage", 0.0),
                "solve_s": solve_s,
            }  # fmt: skip
            if mapping.solution.usable:
                # truth in the frame of the reported anchor pose: compare intrinsics only,
                # pose via the anchor's true pose
                true_anchor = np.array(gt["camera_true_poses"][0])
                reported = cap.anchor.pose
                to_rep = reported @ np.linalg.inv(true_anchor)
                row["projector_error"] = compare_to_truth(
                    mapping.solution, k_true, to_rep @ scene.proj_pose
                )
            want_dense = name == "base"
            dense_p2c = None
            if want_dense:
                dense_p2c = build_maps(dec_v, cap.graycode.pattern).p2c  # type: ignore[arg-type]
            modes = MAPPINGS if want_dense else ("plane", "model", "hybrid")
            row["covered"] = score(mapping, dense_p2c, cam, q_true, groups, modes)
            if name == "base":
                tiles = holdout_tiles(dec_v.valid.shape, opts.tile_px, opts.holdout_fraction, seed)
                dec_g = _mask_decode(dec_v, ~tiles)
                corr_g = pool_blocks(dec_g, 1 << cap.graycode.pattern.skip_lsb)  # type: ignore[union-attr]
                m_g = build_mapping(corr_g, surface, size, opts)
                p2c_g = build_maps(dec_g, cap.graycode.pattern).p2c  # type: ignore[arg-type]
                ui, vi = cam[:, 0].astype(int), cam[:, 1].astype(int)
                in_t = tiles[vi, ui]
                g_groups = {g: m & in_t for g, m in groups.items()}
                row["gaps"] = score(m_g, p2c_g, cam, q_true, g_groups, MAPPINGS)
            rows.append(row)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


# ---------------------------------------------------------------- running


def jobs(
    n_per_kind: int, n_sens: int, n_range: int
) -> list[tuple[str, str, int, bool, float | None]]:
    out: list[tuple[str, str, int, bool, float | None]] = []
    for kind in SCENE_KINDS:
        for i in range(n_per_kind):
            sens = kind in SENS_KINDS and held_out(i) and i // 3 < n_sens
            out.append(("main", kind, i, sens, None))
    for kind in RANGE_KINDS:
        for i in range(n_range):
            for dist in RANGES_M:
                out.append(("range", kind, 3 * i, False, dist))  # held-out indices
    return out


def _run_job(args: tuple[str, str, int, bool, float | None, str]) -> list[dict[str, Any]]:
    tag, kind, index, sens, dist, out = args
    cv2.setNumThreads(1)
    name = f"{tag}-{kind}-{index:03d}" + (f"-{dist:g}m" if dist is not None else "") + ".json"
    path = Path(out) / name
    if path.is_file():
        data: list[dict[str, Any]] = json.loads(path.read_text())
        return data
    rows = run_scene(kind, index, sensitivity=sens, distance_m=dist, tag=tag)  # type: ignore[arg-type]
    path.write_text(json.dumps(rows))
    return rows


def run_all(out: Path, n_per_kind: int, n_sens: int, n_range: int, workers: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    js = [(*j, str(out)) for j in jobs(n_per_kind, n_sens, n_range)]
    js.sort(key=lambda j: -int(j[3]))  # long (sensitivity) jobs first
    t0 = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, rows in enumerate(pool.map(_run_job, js)):
            print(
                f"job {k + 1}/{len(js)}: {len(rows)} runs ({time.monotonic() - t0:.0f} s)",
                flush=True,
            )


def load_rows(data: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(data.glob("*.json")):
        rows.extend(json.loads(path.read_text()))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m facadekit.sim.calib3d_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", default=".calib3d_cache")
    r.add_argument("--n", type=int, default=24, help="scenes per kind (a third held out)")
    r.add_argument("--n-sens", type=int, default=6, help="held-out scenes per kind with sweeps")
    r.add_argument("--n-range", type=int, default=4, help="scenes per kind in the range sweep")
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    rep = sub.add_parser("report")
    rep.add_argument("--data", default=".calib3d_cache")
    rep.add_argument("--out", default="calib3d_data")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        run_all(Path(args.out), args.n, args.n_sens, args.n_range, args.workers)
        return 0
    from facadekit.sim.calib3d_report import write_report

    md = write_report(load_rows(Path(args.data)), Path(args.out))
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
