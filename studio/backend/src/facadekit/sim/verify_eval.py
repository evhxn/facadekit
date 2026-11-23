"""Simulator evaluation of "Verify surface" (docs/13-surface-verification.md).

Every base scene is scanned once through the real pipeline. Then the scene is changed in known
ways (camera moved, projector moved, both, the surface outline drawn too big / too small /
shifted / turned, a door that stands out from the wall, an unlockable camera, dim and bright
light) and the real `run_verify` is run against the scan. Each run is stored with its ground
truth, so tables and thresholds come from the same records.

Split: scenes with ``index % 3 == 0`` are held out. Thresholds are chosen on the others.

Run:  uv run python -m facadekit.sim.verify_eval run --n 60 --out verify_data
      uv run python -m facadekit.sim.verify_eval report --data verify_data
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from facadekit.calib.pipeline import run_calibration
from facadekit.calib.surfacecheck import outward_normals, side_name
from facadekit.calib.verify import run_verify
from facadekit.errors import FkError
from facadekit.geom.homography import apply_h, estimate_aspect
from facadekit.models.calibration import CalibSettings
from facadekit.models.project import Surface
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.e2e import new_project_dir
from facadekit.sim.lag import LaggyCamera, LaggyDisplay, VirtualClock
from facadekit.sim.scene import SimScene, far_scene
from facadekit.store.project_store import load_project_dir, save_project_dir

DOOR = "garage_door"
BASE_SEED = 500_000
Quad = np.ndarray


def held_out(index: int) -> bool:
    return index % 3 == 0


def _log_uniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(math.exp(rng.uniform(math.log(lo), math.log(hi))))


def scene_params(index: int) -> dict[str, Any]:
    rng = np.random.default_rng(BASE_SEED + index)
    return {
        "seed": BASE_SEED + index,
        "k": _log_uniform(rng, 0.55, 1.0),
        "cx": float(rng.uniform(0.42, 0.58)),
        "cy": float(rng.uniform(0.42, 0.55)),
        "blur": _log_uniform(rng, 0.5, 2.0),
        "noise": _log_uniform(rng, 1.0, 6.0),
        "ambient": float(rng.uniform(0.02, 0.25)),
        "gain": float(rng.uniform(0.45, 1.0)),
        "gamma": float(rng.uniform(1.8, 2.6)),
        "door": [float(rng.uniform(-80, 80)), float(rng.uniform(-50, 40))],
        "mortar": bool(rng.uniform() < 0.8),
        "texture": float(rng.choice([0.0, 0.0, 0.5])),
        "raised": (
            [float(rng.uniform(4, 12)) * float(rng.choice([-1, 1])), float(rng.uniform(-6, 6))]
            if rng.uniform() < 0.25
            else None
        ),
    }


def build_scene(p: dict[str, Any]) -> SimScene:
    scene = far_scene(
        float(p["k"]),
        centre=(float(p["cx"]), float(p["cy"])),
        blur_sigma=float(p["blur"]),
        noise_sigma=float(p["noise"]),
        ambient=float(p["ambient"]),
        gain=float(p["gain"]),
        gamma=float(p["gamma"]),
        seed=int(p["seed"]),
        mortar=bool(p["mortar"]),
        feature_texture=float(p["texture"]),
    )
    dx, dy = p["door"]
    features = dict(scene.features_world)
    features[DOOR] = (500 + dx, 480 + dy, 1100 + dx, 800 + dy)
    raised = {DOOR: (float(p["raised"][0]), float(p["raised"][1]))} if p["raised"] else {}
    return replace(scene, features_world=features, raised=raised)


# --- changes ------------------------------------------------------------------------------


def _move(corners: Any, shift: np.ndarray, rot_deg: float = 0.0, zoom: float = 1.0) -> Any:
    pts = np.asarray(corners, dtype=np.float64)
    c = pts.mean(axis=0)
    t = math.radians(rot_deg)
    r = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]]) * zoom
    out = (pts - c) @ r.T + c + shift
    return tuple((float(x), float(y)) for x, y in out)


def _unit(rng: np.random.Generator) -> np.ndarray:
    a = rng.uniform(0, 2 * math.pi)
    return np.array([math.cos(a), math.sin(a)])


def case_list(index: int) -> list[dict[str, Any]]:
    """The changes tried on one base scene (deterministic per scene)."""
    rng = np.random.default_rng(BASE_SEED + index + 77)
    cases: list[dict[str, Any]] = []

    def add(kind: str, **kw: Any) -> None:
        cases.append({"id": f"{kind}-{len(cases)}", "kind": kind, **kw})

    for _ in range(3):
        add("stable", ambient_change=float(rng.uniform(-0.3, 0.3)))
    for m in (0.5, 1.0, 2.0, 4.0, 10.0):
        add("camera", cam_shift=list(_unit(rng) * m))
    add("camera", cam_shift=[0.0, 0.0], cam_rot=0.3, cam_zoom=1.004)
    for m in (0.35, 0.7, 1.4, 2.8, 7.0):  # world px; about 1.4 projector px each
        add("projector", proj_shift=list(_unit(rng) * m))
    add("projector", proj_shift=[0.0, 0.0], proj_rot=0.15)
    for m in (1.4, 4.0):
        add("both", cam_shift=list(_unit(rng) * 3.0), proj_shift=list(_unit(rng) * m))
    for g in (-8.0, -4.0, -2.0, 0.0, 2.0, 4.0, 8.0, 16.0):
        add("mask_grow", grow=g)
    for m in (2.0, 4.0, 8.0):
        add("mask_shift", shift=list(_unit(rng) * m))
    for deg in (1.0, 2.0):
        add("mask_rotate", rot=float(deg * rng.choice([-1, 1])))
    add("autoexposure", grow=0.0)
    add("autoexposure", grow=8.0)
    add("dim", grow=4.0, ambient_abs=0.35)
    add("bright", grow=-4.0, gain_abs=1.6)
    return cases


def drawn_quad(truth: Quad, case: dict[str, Any]) -> Quad:
    q = truth.copy()
    if "grow" in case and case["grow"]:
        from facadekit.calib.verify import grow_polygon

        q = grow_polygon(q, float(case["grow"]))
    if "shift" in case:
        q = q + np.asarray(case["shift"])
    if "rot" in case:
        c = q.mean(axis=0)
        t = math.radians(float(case["rot"]))
        r = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        q = (q - c) @ r.T + c
    return q


def changed_scene(base: SimScene, case: dict[str, Any], run_seed: int) -> SimScene:
    scene = replace(base, seed=run_seed)
    if "cam_shift" in case:
        scene = replace(
            scene,
            world_corners_cam=_move(
                scene.world_corners_cam,
                np.asarray(case["cam_shift"]),
                case.get("cam_rot", 0.0),
                case.get("cam_zoom", 1.0),
            ),
        )
    if "proj_shift" in case:
        shift = np.asarray(case["proj_shift"])
        scene = replace(
            scene,
            proj_corners_world=_move(scene.proj_corners_world, shift, case.get("proj_rot", 0.0)),
        )
        if scene.raised:  # a raised door moves differently from the wall (parallax)
            scene = replace(
                scene,
                raised={
                    k: (v[0] - 0.3 * shift[0], v[1] - 0.3 * shift[1])
                    for k, v in scene.raised.items()
                },
            )
    if "ambient_change" in case:
        scene = replace(scene, ambient=max(0.0, scene.ambient * (1 + case["ambient_change"])))
    if "ambient_abs" in case:
        scene = replace(scene, ambient=float(case["ambient_abs"]))
    if "gain_abs" in case:
        scene = replace(scene, gain=float(case["gain_abs"]))
    return scene


# --- ground truth -------------------------------------------------------------------------


def true_rig(before: SimScene, after: SimScene, dots: np.ndarray) -> dict[str, float]:
    # camera: how far wall points moved in the camera picture
    w, h = before.world_size
    gx, gy = np.meshgrid(np.linspace(0.1 * w, 0.9 * w, 12), np.linspace(0.1 * h, 0.9 * h, 8))
    world = np.stack([gx.ravel(), gy.ravel()], axis=1)
    cam = float(
        np.median(
            np.linalg.norm(apply_h(after.h_w2c, world) - apply_h(before.h_w2c, world), axis=1)
        )
    )
    # projector: where the dots land now, in the scan's projector pixels
    landed = apply_h(np.linalg.inv(before.h_p2w), apply_h(after.h_p2w, dots))
    proj = float(np.median(np.linalg.norm(landed - dots, axis=1))) if len(dots) else 0.0
    return {"cam_shift": cam, "proj_shift": proj}


def _ray_distance(p: np.ndarray, n: np.ndarray, poly: Quad) -> float | None:
    """Signed distance from p to the polygon's boundary along n (+ when p is outside)."""
    best: float | None = None
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        d = b - a
        m = np.array([[n[0], -d[0]], [n[1], -d[1]]])
        if abs(np.linalg.det(m)) < 1e-9:
            continue
        t, u = np.linalg.solve(m, a - p)
        if -0.05 <= u <= 1.05 and (best is None or abs(t) < abs(best)):
            best = float(t)
    if best is None:
        return None
    inside = cv2.pointPolygonTest(
        poly.astype(np.float32).reshape(-1, 1, 2), (float(p[0]), float(p[1])), False
    )
    return abs(best) if inside < 0 else -abs(best)


def true_edges(
    drawn: Quad, light_shift: Callable[[np.ndarray], np.ndarray], obj: Quad
) -> dict[str, dict[str, float]]:
    """Per side of the drawn outline: where its light lands against the object's edge."""
    normals = outward_normals(drawn)
    out: dict[str, dict[str, float]] = {}
    for i in range(len(drawn)):
        a, b = drawn[i], drawn[(i + 1) % len(drawn)]
        ts = np.linspace(0.15, 0.85, 15)
        pts = light_shift(a[None, :] + ts[:, None] * (b - a)[None, :])
        vals = [v for v in (_ray_distance(p, normals[i], obj) for p in pts) if v is not None]
        if not vals:
            continue
        lo, med, hi = np.percentile(vals, [10, 50, 90])
        out[side_name(normals[i])] = {"median": float(med), "lo": float(lo), "hi": float(hi)}
    return out


# --- one scene ----------------------------------------------------------------------------


def run_scene(index: int, workdir: Path | None = None) -> list[dict[str, Any]]:
    params = scene_params(index)
    base = build_scene(params)
    tmp = Path(tempfile.mkdtemp(prefix="fkverify-", dir=workdir))
    rows: list[dict[str, Any]] = []
    try:
        pd = new_project_dir(tmp / "e2e.fkproj")
        display = SimDisplay(base)
        try:
            cal = run_calibration(
                pd,
                display,
                SimCamera(base, display),
                "sim",
                CalibSettings(discard_frames=0, average_frames=1),
                sleep=lambda s: None,
            )
        except FkError as exc:
            return [{"index": index, "held_out": held_out(index), "skipped": exc.code}]
        truth_quad = np.asarray(base.feature_quad_proj(DOOR), dtype=np.float64)
        project = load_project_dir(pd).model_copy(update={"calibration_id": cal.id})
        save_project_dir(pd, project)
        from facadekit.calib.p2cmap import load_map
        from facadekit.calib.rigcheck import dot_layout

        pmap = load_map(pd, cal)
        scale = float(cal.quality.px_per_proj_px or 1.0) if cal.quality else 1.0
        dots = dot_layout(pmap, scale).points
        for j, case in enumerate(case_list(index)):
            after = changed_scene(base, case, base.seed + 1000 + j)
            drawn = drawn_quad(truth_quad, case)
            surface = Surface(
                id="srf_door",
                name="Door",
                quad=tuple((float(x), float(y)) for x, y in drawn),  # type: ignore[arg-type]
                aspect=estimate_aspect([tuple(p) for p in drawn]),
            )
            proj = project.model_copy(update={"surfaces": [surface]})
            if case["kind"] == "autoexposure":
                clock = VirtualClock()
                ldisp = LaggyDisplay(after, clock)
                cam: Any = LaggyCamera(after, ldisp, clock, lockable=False)
                disp: Any = ldisp
                sleep: Callable[[float], None] = clock.sleep
                clk: Callable[[], float] = clock
            else:
                disp = SimDisplay(after)
                cam = SimCamera(after, disp)
                sleep, clk = (lambda s: None), time.monotonic
            t0 = time.monotonic()
            err: str | None = None
            try:
                out = run_verify(pd, proj, disp, cam, surface_id="srf_door", sleep=sleep, clock=clk)
                rec = out.record
            except FkError as exc:
                err = exc.code
            obj_now = np.asarray(after.feature_quad_proj(DOOR), dtype=np.float64)
            landed = lambda pts, a=after: apply_h(np.linalg.inv(base.h_p2w), apply_h(a.h_p2w, pts))  # noqa: E731
            # where light drawn at projector pixel p lands, in the scan's projector px; the object
            # in the same frame is base-frame coordinates of the object's current position
            obj_in_old = apply_h(np.linalg.inv(base.h_p2w), apply_h(after.h_p2w, obj_now))
            row: dict[str, Any] = {
                "index": index,
                "held_out": held_out(index),
                "params": params,
                "case": case,
                "seconds": round(time.monotonic() - t0, 3),
                "truth": {
                    **true_rig(base, after, dots),
                    "edges": true_edges(drawn, landed, obj_in_old),
                    "scale": scale,
                    "quad_now": obj_now.tolist(),
                    "drawn": drawn.tolist(),
                },
                "error": err,
            }
            if err is None:
                row["rig"] = rec.rig.model_dump(
                    mode="json", exclude={"message", "recommendation", "drift"}
                )
                s = rec.surface
                assert s is not None
                row["surface"] = s.model_dump(
                    mode="json", exclude={"message", "suggestion_note", "suggested_mask"}
                )
                row["sim_seconds"] = rec.duration_s
                row["shots"] = rec.shots
                row["level"] = rec.level
            rows.append(row)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


def _run_one(args: tuple[int, str]) -> list[dict[str, Any]]:
    index, out = args
    cv2.setNumThreads(1)
    path = Path(out) / f"scene-{index:04d}.json"
    if path.is_file():
        data: list[dict[str, Any]] = json.loads(path.read_text())
        return data
    rows = run_scene(index)
    path.write_text(json.dumps(rows))
    return rows


def run_all(n: int, out: Path, workers: int, start: int = 0) -> None:
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(i, str(out)) for i in range(start, start + n)]
    t0 = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, rows in enumerate(pool.map(_run_one, jobs)):
            print(
                f"scene {k + 1}/{n}: {len(rows)} runs ({time.monotonic() - t0:.0f} s)", flush=True
            )


def load_rows(data: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(data.glob("scene-*.json")):
        rows.extend(json.loads(path.read_text()))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m facadekit.sim.verify_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--n", type=int, default=60)
    r.add_argument("--start", type=int, default=0)
    r.add_argument("--out", default="verify_data")
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    rep = sub.add_parser("report")
    rep.add_argument("--data", default="verify_data")
    rep.add_argument("--json", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        run_all(args.n, Path(args.out), args.workers, args.start)
        return 0
    from facadekit.sim.verify_report import report

    print(report(load_rows(Path(args.data)), json_out=Path(args.json) if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
