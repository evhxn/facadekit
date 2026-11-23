"""calib3d end to end: capture folder -> surface model -> projector -> 3D-aware mapping -> report.

Mappings compared (all map a Gray-code camera pixel to the projector pixel that lights it):

- ``plane``: one RANSAC homography through every decoded pixel, exactly as ``calib.maps``
  fits ``h_c2p``. This is what FacadeKit's plane-based mapping assumes.
- ``model``: the surface point from the depth sensor alone, projected with the solved
  projector (pure "LiDAR + projector model").
- ``hybrid`` (the 3D-aware mapping this module ships): on each plane found in the depth whose
  scan data agrees it is a plane, a homography fitted to the decoded pixels of that plane only
  (exact for a plane, like the plane mapping, but per plane); elsewhere the solved projector
  applied to the scan-corrected surface (``refine.scan_corrected``) plus the scan's smoothed
  residual inside the same surface (so it follows the scan where the scan has data and never
  smooths across a depth edge); ``plane`` where there is neither depth nor a solved projector.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from facadekit.calib.decode import DecodeResult, decode
from facadekit.calib3d.capture_format import Capture3d
from facadekit.calib3d.fusion import FusionOptions, SurfaceModel, fuse
from facadekit.calib3d.geometry import project
from facadekit.calib3d.projector import ProjectorSolution, solve_projector
from facadekit.calib3d.refine import scan_corrected, spread_by_label
from facadekit.errors import SCAN_FAILED, FkError

# Planar groups of the scan inside each depth plane (plane_groups)
PLANE_H_PX = 1.5  # a decoded block within this of a group's homography belongs to it
PLANE_H_MEDIAN_PX = 0.8  # a group's inliers must have a median residual below this
MIN_GROUP_BLOCKS = 100
MIN_GROUP_SHARE = 0.05  # of the plane's decoded blocks
MAX_GROUPS_PER_PLANE = 4
RESIDUAL_SIGMA_PX = 8.0  # camera px
RESIDUAL_MAX_PX = 5.0
SCAN_PLANAR_SHARE = 0.97  # share of blocks the global homography must explain to call it a plane
MAX_RANSAC_PAIRS = 20000
MAPPINGS = ("plane", "model", "hybrid")


@dataclass(frozen=True)
class SolveOptions:
    fusion: FusionOptions = field(default_factory=FusionOptions)
    min_conf: float = 0.10  # Gray-code decode, as CalibSettings.min_conf
    ransac_px: float = 6.0
    inlier_px: float = 3.0
    distortion: bool = False
    holdout_fraction: float = 0.15  # tiles of scan data held out for the comparison
    tile_px: int = 96
    seed: int = 0


@dataclass
class Correspondences:
    """Decoded pairs, one per decoded projector block (OpenCV centre convention)."""

    cam: np.ndarray  # (N, 2) camera centroid of the block's pixels
    proj: np.ndarray  # (N, 2) projector block centre
    count: np.ndarray  # (N,) camera pixels in the block


def pool_blocks(dec: DecodeResult, block: int) -> Correspondences:
    vs, us = np.nonzero(dec.valid)
    if len(us) == 0:
        return Correspondences(np.zeros((0, 2)), np.zeros((0, 2)), np.zeros(0))
    px = dec.c2p[vs, us, 0].astype(np.float64)
    py = dec.c2p[vs, us, 1].astype(np.float64)
    bx = np.floor(px / block).astype(np.int64)
    by = np.floor(py / block).astype(np.int64)
    key = by * 100_000 + bx
    _, inv, count = np.unique(key, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)

    def mean(v: np.ndarray) -> np.ndarray:
        return np.asarray(np.bincount(inv, weights=v) / count)

    cam = np.stack([mean(us.astype(np.float64)), mean(vs.astype(np.float64))], axis=1)
    proj = np.stack([mean(px), mean(py)], axis=1) - 0.5  # corner -> centre convention
    return Correspondences(cam, proj, count.astype(np.float64))


def correspondences_from_calibration(folder: Path, step: int = 4) -> Correspondences:
    """Pairs from a stored FacadeKit calibration (p2c.npy, measured_mask.png)."""
    p2c = np.load(folder / "p2c.npy", allow_pickle=False)
    h, w = p2c.shape[:2]
    measured = np.ones((h, w), bool)
    mpath = folder / "measured_mask.png"
    if mpath.is_file():
        m = cv2.imread(str(mpath), cv2.IMREAD_GRAYSCALE)
        if m is not None and m.shape == (h, w):
            measured = m > 127
    ys, xs = np.mgrid[step // 2 : h : step, step // 2 : w : step]
    ok = measured[ys, xs] & np.isfinite(p2c[ys, xs]).all(axis=-1)
    xs, ys = xs[ok], ys[ok]
    cam = p2c[ys, xs].astype(np.float64) - 0.5
    proj = np.stack([xs, ys], axis=1).astype(np.float64)
    return Correspondences(cam, proj, np.ones(len(xs)))


def fit_homography(
    cam: np.ndarray, proj: np.ndarray, thr: float = 6.0
) -> tuple[np.ndarray | None, float]:
    """Camera -> projector homography (centre convention): RANSAC for the inliers (as
    calib.maps fits h_c2p), then a least-squares refit on all inliers. The refit makes the
    result stable; cv2's RANSAC alone can land 0.2 px off on clean data. Returns (H, inlier
    share)."""
    if len(cam) < 8:
        return None, 0.0
    rng = np.random.default_rng(0)
    sub_c, sub_p = cam, proj
    if len(cam) > MAX_RANSAC_PAIRS:
        pick = rng.choice(len(cam), MAX_RANSAC_PAIRS, replace=False)
        sub_c, sub_p = cam[pick], proj[pick]
    h, _ = cv2.findHomography(sub_c.astype(np.float64), sub_p.astype(np.float64), cv2.RANSAC, thr)
    if h is None:
        return None, 0.0
    h = np.asarray(h, dtype=np.float64)
    for _ in range(2):
        inl = np.linalg.norm(apply_homography(h, cam) - proj, axis=1) < thr
        if inl.sum() < 8:
            return None, 0.0
        h2, _ = cv2.findHomography(cam[inl].astype(np.float64), proj[inl].astype(np.float64), 0)
        if h2 is None:
            break
        h = np.asarray(h2, dtype=np.float64)
    inl = np.linalg.norm(apply_homography(h, cam) - proj, axis=1) < thr
    return h, float(inl.mean())


def apply_homography(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.hstack([pts, np.ones((len(pts), 1))]) @ h.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(p[:, :2] / p[:, 2:3])


@dataclass
class Mapping3d:
    surface: SurfaceModel  # scan-corrected (refine.scan_corrected) when the projector solved
    solution: ProjectorSolution
    h_plane: np.ndarray | None
    groups: list[np.ndarray]  # homographies of the scan's planar groups (see plane_groups)
    group_label: np.ndarray  # int16 (H, W): group used at each camera pixel, -1 = none
    planarity: float  # inlier share of h_plane
    sensor_surface: SurfaceModel | None = None  # the depth sensor's surface, before correction
    # hybrid only: smooth residual (scan minus model, projector px) per surface label, added
    # where the model is used, so the mapping follows the scan where it has data
    residual: np.ndarray | None = None

    def predict(self, cam: np.ndarray, mode: str = "hybrid") -> np.ndarray:
        """Projector pixels (N, 2) for camera pixels (N, 2); NaN where the mapping has none."""
        cam = np.asarray(cam, dtype=np.float64).reshape(-1, 2)
        out = np.full((len(cam), 2), np.nan)
        plane = apply_homography(self.h_plane, cam) if self.h_plane is not None else out.copy()
        if mode == "plane":
            return plane
        if mode not in ("model", "hybrid"):
            raise ValueError(mode)
        model = out.copy()
        surf = self.surface
        if mode == "model" and self.sensor_surface is not None:
            surf = self.sensor_surface
        if self.solution.usable:
            pts = surf.world_points_at(cam)
            ok = np.isfinite(pts).all(axis=1)
            if ok.any():
                model[ok] = self.solution.project(pts[ok])
        if mode == "model":
            return model
        h, w = self.surface.label.shape
        ui = np.clip(np.rint(cam[:, 0]).astype(np.int64), 0, w - 1)
        vi = np.clip(np.rint(cam[:, 1]).astype(np.int64), 0, h - 1)
        if self.residual is not None:
            model = model + self.residual[vi, ui]
        out = np.where(np.isfinite(model).all(axis=1, keepdims=True), model, plane)
        grp = self.group_label[vi, ui]
        for g, hg in enumerate(self.groups):
            m = grp == g
            if m.any():
                out[m] = apply_homography(hg, cam[m])
        return out


def plane_groups(
    corr: Correspondences, surface: SurfaceModel
) -> tuple[list[np.ndarray], np.ndarray]:
    """Planar groups from the scan, inside each plane the depth sensor found.

    Within a depth plane, homographies are found one after another by RANSAC on the decoded
    blocks (``PLANE_H_PX``); each takes its inliers. This splits what the depth sensor merged
    (surfaces a centimetre or two apart give several pixels of parallax in the scan) and leaves
    out what is not on the plane at all (a box's side face next to its front). Each pixel of the
    depth plane then uses the group of its nearest decoded block, or none (-1) when that block
    belongs to no group. Returns (homographies, group label image)."""
    h, w = surface.label.shape
    group_label = np.full((h, w), -1, np.int16)
    groups: list[np.ndarray] = []
    if len(corr.cam) == 0:
        return groups, group_label
    ui = np.clip(np.rint(corr.cam[:, 0]).astype(np.int64), 0, w - 1)
    vi = np.clip(np.rint(corr.cam[:, 1]).astype(np.int64), 0, h - 1)
    lab = surface.label[vi, ui]
    for k in range(len(surface.planes)):
        idx = np.nonzero(lab == k)[0]
        if len(idx) < MIN_GROUP_BLOCKS:
            continue
        cls = np.full(len(idx), -1, np.int32)
        rem = np.arange(len(idx))
        for _ in range(MAX_GROUPS_PER_PLANE):
            if len(rem) < MIN_GROUP_BLOCKS:
                break
            hg, _ = fit_homography(corr.cam[idx[rem]], corr.proj[idx[rem]], thr=PLANE_H_PX)
            if hg is None:
                break
            resid = np.linalg.norm(
                apply_homography(hg, corr.cam[idx[rem]]) - corr.proj[idx[rem]], axis=1
            )
            inl = resid < PLANE_H_PX
            if inl.sum() < max(MIN_GROUP_BLOCKS, MIN_GROUP_SHARE * len(idx)):
                break
            if float(np.median(resid[inl])) > PLANE_H_MEDIAN_PX:
                break
            cls[rem[inl]] = len(groups)
            groups.append(hg)
            rem = rem[~inl]
        if not (cls >= 0).any():
            continue
        # every pixel of the plane takes the class of its nearest decoded block
        region = surface.label == k
        seeds = np.full((h, w), -2, np.int32)
        seeds[vi[idx], ui[idx]] = cls
        known = seeds > -2
        filled = _nearest_class(seeds, known)
        group_label[region] = filled[region].astype(np.int16)
    return groups, group_label


def _nearest_class(values: np.ndarray, known: np.ndarray) -> np.ndarray:
    src = (~known).astype(np.uint8)
    _, lab = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ky, kx = np.nonzero(known)
    order = np.zeros(int(lab.max()) + 1, dtype=np.int64)
    order[lab[ky, kx]] = np.arange(len(ky))
    idx = order[lab]
    return np.asarray(values[ky[idx], kx[idx]])


def build_mapping(
    corr: Correspondences,
    surface: SurfaceModel,
    proj_size: tuple[int, int],
    opts: SolveOptions,
) -> Mapping3d:
    h_plane, planarity = fit_homography(corr.cam, corr.proj)
    pts = surface.world_points_at(corr.cam) if len(corr.cam) else np.zeros((0, 3))
    ok = np.isfinite(pts).all(axis=1)
    x3, x2 = pts[ok], corr.proj[ok]
    if len(x3) > MAX_RANSAC_PAIRS:
        pick = np.random.default_rng(opts.seed).choice(len(x3), MAX_RANSAC_PAIRS, replace=False)
        x3, x2 = x3[pick], x2[pick]
    sol = solve_projector(
        x3, x2, proj_size, ransac_px=opts.ransac_px, inlier_px=opts.inlier_px,
        distortion=opts.distortion, seed=opts.seed,
    )  # fmt: skip
    if sol.usable and h_plane is not None:
        resid = np.linalg.norm(apply_homography(h_plane, corr.cam) - corr.proj, axis=1)
        share = float(np.mean(resid < PLANE_H_PX))
        if share >= SCAN_PLANAR_SHARE:
            # the scan is far more precise than the depth: if one homography explains it, the
            # surface is one plane for this projector, whatever the depth's noise suggests
            sol = sol.rejected(
                "planar",
                f"the Gray-code scan fits one homography ({share * 100:.0f} % of blocks within "
                f"{PLANE_H_PX:g} px): one plane for the projector, which is then under-"
                "constrained. The plane mapping is used instead.",
            )
    sensor_surface = surface
    if sol.usable:
        surface, _ = scan_corrected(surface, sol, corr.cam, corr.proj)
    groups, group_label = plane_groups(corr, surface)
    mapping = Mapping3d(surface, sol, h_plane, groups, group_label, planarity, sensor_surface)
    if sol.usable:
        pred = mapping.predict(corr.cam, "hybrid")  # residual still None: plain model there
        res = corr.proj - pred
        ok = np.isfinite(res).all(axis=1) & (np.linalg.norm(res, axis=1) < RESIDUAL_MAX_PX)
        h, w = surface.label.shape
        ui = np.clip(np.rint(corr.cam[:, 0]).astype(np.int64), 0, w - 1)
        vi = np.clip(np.rint(corr.cam[:, 1]).astype(np.int64), 0, h - 1)
        lab = surface.label[vi, ui]
        mapping.residual = spread_by_label(
            res[ok], ui[ok], vi[ok], lab[ok], surface.label, RESIDUAL_SIGMA_PX
        ).astype(np.float32)
    return mapping


def holdout_tiles(shape: tuple[int, int], tile: int, fraction: float, seed: int) -> np.ndarray:
    """Boolean (H, W): True inside the held-out tiles."""
    h, w = shape
    th, tw = (h + tile - 1) // tile, (w + tile - 1) // tile
    rng = np.random.default_rng(seed + 991)
    pick = rng.uniform(size=(th, tw)) < fraction
    return np.asarray(np.kron(pick, np.ones((tile, tile), bool))[:h, :w])


def _stats(err: np.ndarray) -> dict[str, float | int | None]:
    e = err[np.isfinite(err)]
    if len(e) == 0:
        return {"n": 0, "median_px": None, "p95_px": None}
    return {"n": int(len(e)), "median_px": round(float(np.median(e)), 4),
            "p95_px": round(float(np.percentile(e, 95)), 4)}  # fmt: skip


@dataclass
class Calib3dResult:
    capture: Capture3d
    decoded: DecodeResult
    corr: Correspondences
    mapping: Mapping3d
    holdout: dict[str, Any]
    elapsed_s: float = 0.0

    def to_json(self) -> dict[str, Any]:
        sol = self.mapping.solution
        return {
            "format_version": 1,
            "capture": str(self.capture.root),
            "simulated": self.capture.ground_truth is not None,
            "scan": {
                "decoded_pixels": int(self.decoded.valid.sum()),
                "correspondences": int(len(self.corr.cam)),
                "plane_inliers": round(self.mapping.planarity, 4),
            },
            "surface": self.mapping.surface.stats,
            "projector": sol.to_json(),
            "plane_groups": len(self.mapping.groups),
            "holdout": self.holdout,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def decode_capture(
    capture: Capture3d, opts: SolveOptions
) -> tuple[DecodeResult, Correspondences, int]:
    g = capture.graycode
    if g.kind == "calibration":
        corr = correspondences_from_calibration(capture.root / g.path)
        h, w = capture.anchor.intrinsics.height, capture.anchor.intrinsics.width
        valid = np.zeros((h, w), bool)
        ui = np.clip(np.rint(corr.cam[:, 0]).astype(int), 0, w - 1)
        vi = np.clip(np.rint(corr.cam[:, 1]).astype(int), 0, h - 1)
        valid[vi, ui] = True
        c2p = np.full((h, w, 2), np.nan, np.float32)
        dec = DecodeResult(c2p, valid, np.zeros((h, w), np.float32), 0.0, 0.0)
        return dec, corr, 1
    cs = capture.load_graycode()
    dec = decode(cs, min_conf=opts.min_conf)
    block = 1 << cs.spec.skip_lsb
    return dec, pool_blocks(dec, block), block


def projector_size(capture: Capture3d) -> tuple[int, int]:
    g = capture.graycode
    if g.pattern is not None:
        return g.pattern.width, g.pattern.height
    p2c = np.load(capture.root / g.path / "p2c.npy", mmap_mode="r", allow_pickle=False)
    return int(p2c.shape[1]), int(p2c.shape[0])


def solve_capture(capture: Capture3d, opts: SolveOptions | None = None) -> Calib3dResult:
    import time

    t0 = time.monotonic()
    opts = opts or SolveOptions()
    dec, corr, _ = decode_capture(capture, opts)
    if len(corr.cam) < 50:
        raise FkError(SCAN_FAILED, f"only {len(corr.cam)} decoded projector blocks")
    surface = fuse(capture, opts.fusion)
    size = projector_size(capture)

    # agreement with the scan on held-out tiles: fit without them, compare on them
    tiles = holdout_tiles(dec.valid.shape, opts.tile_px, opts.holdout_fraction, opts.seed)
    h, w = tiles.shape
    ui = np.clip(np.rint(corr.cam[:, 0]).astype(int), 0, w - 1)
    vi = np.clip(np.rint(corr.cam[:, 1]).astype(int), 0, h - 1)
    held = tiles[vi, ui]
    holdout: dict[str, Any] = {"tiles_px": opts.tile_px, "fraction": opts.holdout_fraction}
    if held.sum() >= 20 and (~held).sum() >= 50:
        train = Correspondences(corr.cam[~held], corr.proj[~held], corr.count[~held])
        m_train = build_mapping(train, surface, size, opts)
        for mode in MAPPINGS:
            pred = m_train.predict(corr.cam[held], mode)
            holdout[mode] = _stats(np.linalg.norm(pred - corr.proj[held], axis=1))
        holdout["note"] = (
            "projector px against the decoded scan in tiles left out of the fit; the scan's "
            "own error (block quantisation, decode noise) is included"
        )
    mapping = build_mapping(corr, surface, size, opts)
    return Calib3dResult(capture, dec, corr, mapping, holdout, time.monotonic() - t0)


# ---------------------------------------------------------------- per-pixel warp and report


def projector_warp(
    mapping: Mapping3d, proj_size: tuple[int, int], mode: str = "hybrid"
) -> np.ndarray:
    """Projector -> camera map (float32 (Hp, Wp, 2), OpenCV centre convention, NaN where no
    surface): for each projector pixel, the camera pixel of the surface point it lights.

    Built by pushing every camera pixel through the 3D-aware mapping and keeping, per projector
    pixel, the point nearest the projector (a z-buffer), then filling 1-2 px gaps.
    """
    surf = mapping.surface
    h, w = surf.depth.shape
    vs, us = np.mgrid[0:h, 0:w]
    cam = np.stack([us.ravel(), vs.ravel()], axis=1).astype(np.float64)
    known = np.isfinite(surf.depth.ravel())
    cam = cam[known]
    q = mapping.predict(cam, mode)
    pw, ph = proj_size
    if mapping.solution.usable:
        assert mapping.solution.pose is not None and mapping.solution.intrinsics is not None
        _, zp = project(
            mapping.solution.intrinsics, mapping.solution.pose, surf.world_points_at(cam)
        )
    else:
        zp = surf.depth.ravel()[known].astype(np.float64)
    ok = np.isfinite(q).all(axis=1) & np.isfinite(zp)
    qi = np.rint(q[:, 0]).astype(np.int64)
    qj = np.rint(q[:, 1]).astype(np.int64)
    ok &= (qi >= 0) & (qi < pw) & (qj >= 0) & (qj < ph)
    idx = qj[ok] * pw + qi[ok]
    order = np.lexsort((zp[ok], idx))
    first = np.unique(idx[order], return_index=True)[1]
    sel = order[first]
    flat_out = np.full((ph * pw, 2), np.nan, np.float32)
    flat_out[idx[sel]] = cam[ok][sel]
    out = flat_out.reshape(ph, pw, 2)
    have = np.isfinite(out[..., 0])
    for _ in range(2):  # close gaps between splatted pixels
        filled = (cv2.dilate(have.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & ~have
        if not filled.any():
            break
        for c in range(2):
            ch = np.where(have, out[..., c], 0).astype(np.float32)
            s = cv2.blur(ch, (3, 3))
            n = cv2.blur(have.astype(np.float32), (3, 3))
            out[..., c] = np.where(filled, s / np.maximum(n, 1e-6), out[..., c])
        have = have | filled
    return out


def _fmt(v: Any, nd: int = 2) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


STD_KEYS = ("fx", "fy", "cx", "cy")


def summary_table(result: Calib3dResult) -> str:
    j = result.to_json()
    p = j["projector"]
    rows: list[tuple[str, str]] = [
        ("capture", j["capture"] + ("  [SIMULATED]" if j["simulated"] else "")),
        (
            "decoded pixels / blocks",
            f"{j['scan']['decoded_pixels']} / {j['scan']['correspondences']}",
        ),
        ("depth coverage (Gray-code view)", f"{j['surface'].get('coverage', 0) * 100:.0f} %"),
        ("planes in the depth", str(len(j["surface"].get("planes", [])))),
        ("projector status", f"{p['status']}: {p['message']}"),
    ]
    if "intrinsics" in p:
        k = p["intrinsics"]
        rows += [
            ("projector fx, fy", f"{k['fx']:.1f}, {k['fy']:.1f} px"),
            ("projector cx, cy", f"{k['cx']:.1f}, {k['cy']:.1f} px"),
            ("1-sigma fx, fy, cx, cy", ", ".join(_fmt(p["std"].get(n), 1) for n in STD_KEYS)),
            ("reprojection median / p95", f"{_fmt(p['reprojection_median_px'])} / "
             f"{_fmt(p['reprojection_p95_px'])} projector px"),
            ("inlier fraction", f"{p['inlier_fraction'] * 100:.1f} %"),
        ]  # fmt: skip
    hold = j["holdout"]
    for mode in MAPPINGS:
        if mode in hold:
            s = hold[mode]
            rows.append(
                (
                    f"held-out tiles: {mode}",
                    f"median {_fmt(s['median_px'])}, p95 {_fmt(s['p95_px'])} px (n={s['n']})",
                )
            )
    width = max(len(a) for a, _ in rows)
    return "\n".join(f"{a.ljust(width)}  {b}" for a, b in rows)


def write_report(result: Calib3dResult, out_dir: Path) -> list[Path]:
    """report.json, report.md, overlay.png (camera view: planes, residuals), proj_view_3d.png."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    j = result.to_json()
    (out_dir / "report.json").write_text(json.dumps(j, indent=1), encoding="utf-8")
    written.append(out_dir / "report.json")
    (out_dir / "report.md").write_text(
        "# calib3d solve\n\n```\n" + summary_table(result) + "\n```\n"
        + ("\nSIMULATED capture: numbers describe the simulator, not a device.\n"
           if j["simulated"] else ""),
        encoding="utf-8",
    )  # fmt: skip
    written.append(out_dir / "report.md")

    cap = result.capture
    photo = cap.load_image(cap.anchor)
    if photo.shape[:2] != result.mapping.surface.depth.shape:
        photo = cv2.resize(photo, result.mapping.surface.depth.shape[::-1])
    overlay = (photo.astype(np.float32) * 0.6).astype(np.uint8)
    lab = result.mapping.surface.label
    palette = np.array(
        [[230, 159, 0], [86, 180, 233], [0, 158, 115], [240, 228, 66], [0, 114, 178],
         [213, 94, 0], [204, 121, 167], [120, 120, 120]], np.uint8,
    )  # fmt: skip
    for k in range(len(result.mapping.surface.planes)):
        m = lab == k
        overlay[m] = (0.6 * overlay[m] + 0.4 * palette[k % len(palette)]).astype(np.uint8)
    nonplanar = lab == -1
    overlay[nonplanar] = (0.6 * overlay[nonplanar] + 0.4 * np.array([255, 0, 255])).astype(np.uint8)
    pred = result.mapping.predict(result.corr.cam, "hybrid")
    err = np.linalg.norm(pred - result.corr.proj, axis=1)
    for (u, v), e in zip(result.corr.cam[::7], err[::7], strict=True):
        if not math.isfinite(e):
            continue
        colour = (0, 200, 0) if e < 1 else (0, 200, 255) if e < 3 else (0, 0, 255)
        cv2.circle(overlay, (int(round(u)), int(round(v))), 1, colour, -1)
    legend = "planes: colours, curved: magenta, dots: scan residual <1/<3/>=3 px"
    white = (255, 255, 255)
    cv2.putText(overlay, legend, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, white, 1, cv2.LINE_AA)
    cv2.imwrite(str(out_dir / "overlay.png"), overlay)
    written.append(out_dir / "overlay.png")

    size = projector_size(cap)
    warp = projector_warp(result.mapping, size)
    view = cv2.remap(
        photo,
        np.nan_to_num(warp[..., 0], nan=-1).astype(np.float32),
        np.nan_to_num(warp[..., 1], nan=-1).astype(np.float32),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )
    cv2.imwrite(str(out_dir / "proj_view_3d.png"), view)
    written.append(out_dir / "proj_view_3d.png")
    return written
