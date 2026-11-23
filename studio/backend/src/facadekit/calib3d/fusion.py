"""Depth fusion: every depth map of a capture -> one surface model seen from the Gray-code camera.

Steps (docs/18, "Processing"):

1. Per depth map: drop pixels below the confidence floor, beyond the range limit, and
   "flying pixels" at depth edges (3x3 depth spread above ``flying_rel`` of the depth).
2. Back-project with the depth intrinsics and the shot's pose into world points.
3. Splat every point into the Gray-code camera on a grid of ``cell_px`` cells; per cell keep the
   nearest surface (points within ``occlusion_m`` of the cell's 20th-percentile depth) and take
   its median depth. Edge-aware smoothing, small holes filled.
4. Planes: smooth connected patches of the cell grid, each tested with RANSAC
   (``_segment_grid``), then refitted on the raw points; the rest is non-planar.
5. Per Gray-code camera pixel: depth upsampled from the cells (nearest where the cells disagree,
   so depth edges stay sharp); on a plane the depth is the exact ray-plane intersection, which
   averages the sensor noise over the whole plane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from facadekit.calib3d.capture_format import Capture3d
from facadekit.calib3d.geometry import (
    Intrinsics,
    backproject,
    depth_image_points,
    pixel_rays,
    project,
    transform,
)
from facadekit.calib3d.planes import Plane, fit_plane, ransac_plane, ray_plane_depth

LABEL_NONPLANAR = -1
LABEL_UNKNOWN = -2
# Plane segmentation on the cell grid (_segment_grid). Chosen on train scenes only.
NORMAL_DEG = 15.0
TANGENT_TOL_M = 0.008
TANGENT_TOL_REL = 0.003  # per metre of depth
ROUGH_FACTOR = 2.5  # a cell is rough above this many times the typical local-plane RMS ...
ROUGH_M = 0.003  # ... and above this + ROUGH_REL * depth
ROUGH_REL = 0.0015
PLANE_SHARE = 0.7  # share of a smooth component that must lie on one plane
MIN_PLANE_CELLS = 40
MAX_PLANES = 12
SMOOTH_SIGMA_M = 0.012  # edge-aware smoothing of cell depths: depth differences it keeps


@dataclass(frozen=True)
class FusionOptions:
    min_confidence: int = 2  # ARKit levels: 0 low, 1 medium, 2 high
    max_range_m: float = 5.0
    flying_rel: float = 0.04  # reject if the 3x3 depth spread exceeds this fraction of depth
    voxel_m: float = 0.02
    plane_threshold_m: float = 0.02
    cell_px: int = 8
    occlusion_m: float = 0.08
    fill_cells: int = 2  # holes up to about this many cells are filled
    seed: int = 0


@dataclass
class SurfaceModel:
    intrinsics: Intrinsics  # Gray-code camera
    pose: np.ndarray  # world_from_camera of the Gray-code camera
    depth: np.ndarray  # float32 (H, W) z-depth, NaN = unknown
    label: np.ndarray  # int16 (H, W): plane index, -1 non-planar, -2 unknown
    planes: list[Plane]
    points: np.ndarray  # (N, 3) voxel-averaged world points
    point_labels: np.ndarray  # (N,) plane index or -1
    stats: dict[str, Any] = field(default_factory=dict)

    def world_points(self, us: np.ndarray, vs: np.ndarray) -> np.ndarray:
        """World points of Gray-code camera pixels (integer indices); NaN where unknown."""
        z = self.depth[vs, us].astype(np.float64)
        pc = pixel_rays(self.intrinsics, us, vs) * z[:, None]
        return transform(self.pose, pc)

    def world_points_at(self, uv: np.ndarray) -> np.ndarray:
        """World points at sub-pixel camera positions (nearest depth pixel, exact ray)."""
        h, w = self.depth.shape
        ui = np.clip(np.rint(uv[:, 0]).astype(np.int64), 0, w - 1)
        vi = np.clip(np.rint(uv[:, 1]).astype(np.int64), 0, h - 1)
        z = self.depth[vi, ui].astype(np.float64)
        lab = self.label[vi, ui]
        rays_c = pixel_rays(self.intrinsics, uv[:, 0], uv[:, 1])
        out_z = z.copy()
        for k, plane in enumerate(self.planes):
            m = lab == k
            if m.any():
                out_z[m] = _plane_z(rays_c[m], self.pose, plane)
        return transform(self.pose, rays_c * out_z[:, None])


def _plane_z(rays_c: np.ndarray, pose: np.ndarray, plane: Plane) -> np.ndarray:
    rays_w = rays_c @ pose[:3, :3].T
    return ray_plane_depth(rays_w, pose[:3, 3], plane)  # rays have z = 1: s is z-depth


def _clean_depth(
    depth: np.ndarray, conf: np.ndarray | None, opts: FusionOptions
) -> tuple[np.ndarray, dict[str, int]]:
    finite = np.isfinite(depth)
    total = int(finite.sum())
    ok = finite.copy()
    low_conf = 0
    if conf is not None:
        bad = ok & (conf < opts.min_confidence)
        low_conf = int(bad.sum())
        ok &= ~bad
    far = ok & (depth > opts.max_range_m)
    ok &= ~far
    d0 = np.where(ok, depth, 0).astype(np.float32)
    dmax = cv2.dilate(d0, np.ones((3, 3), np.uint8))
    dmin = cv2.erode(np.where(ok, depth, 1e6).astype(np.float32), np.ones((3, 3), np.uint8))
    flying = ok & ((dmax - dmin) > opts.flying_rel * np.where(ok, depth, 1))
    ok &= ~flying
    stats = {
        "pixels": total,
        "low_confidence": low_conf,
        "beyond_range": int(far.sum()),
        "flying": int(flying.sum()),
        "kept": int(ok.sum()),
    }
    return ok, stats


def _voxel_average(pts: np.ndarray, voxel: float) -> tuple[np.ndarray, np.ndarray]:
    keys = np.floor(pts / voxel).astype(np.int64)
    _, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    out = np.empty((len(counts), 3))
    for a in range(3):
        out[:, a] = np.bincount(inv, weights=pts[:, a]) / counts
    return out, inv


def _nearest_fill(values: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Copy each unknown cell from its nearest known cell."""
    if known.all() or not known.any():
        return values
    src = (~known).astype(np.uint8)
    _, lab = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ky, kx = np.nonzero(known)
    # labels number the zero pixels (known cells) in scan order, starting at 1
    order = np.zeros(int(lab.max()) + 1, dtype=np.int64)
    zero_lab = lab[ky, kx]
    order[zero_lab] = np.arange(len(ky))
    idx = order[lab]
    out = values.copy()
    out[~known] = values[ky[idx[~known]], kx[idx[~known]]]
    return out


def _tangent_tol(z: np.ndarray) -> np.ndarray:
    return np.asarray(TANGENT_TOL_M + TANGENT_TOL_REL * z)


def _local_planes(x: np.ndarray, valid: np.ndarray, win: int) -> tuple[np.ndarray, np.ndarray]:
    """Per cell: normal (gh, gw, 3) and RMS residual (gh, gw) of the least-squares plane
    through the valid cells in a win x win window (box-filtered moments)."""
    w = valid.astype(np.float64)
    xs = np.where(valid[..., None], x, 0.0)

    def box(a: np.ndarray) -> np.ndarray:
        return np.asarray(cv2.boxFilter(a, cv2.CV_64F, (win, win), normalize=False,
                                        borderType=cv2.BORDER_CONSTANT))  # fmt: skip

    n = box(w)
    safe = np.maximum(n, 1.0)
    mean = np.stack([box(xs[..., a]) for a in range(3)], axis=-1) / safe[..., None]
    cov = np.empty(x.shape[:2] + (3, 3))
    for a in range(3):
        for b in range(a, 3):
            m = box(xs[..., a] * xs[..., b]) / safe - mean[..., a] * mean[..., b]
            cov[..., a, b] = cov[..., b, a] = m
    evals, evecs = np.linalg.eigh(cov)
    normal = evecs[..., :, 0]
    rms = np.sqrt(np.maximum(evals[..., 0], 0.0))
    poor = (n < 0.6 * win * win) | ~valid
    rms[poor] = np.inf
    return normal, rms


def _segment_grid(
    grid_z: np.ndarray, intr: Intrinsics, pose: np.ndarray, cell: int, opts: FusionOptions
) -> tuple[list[Plane], np.ndarray]:
    """Planes from the cell depth grid of the Gray-code view.

    1. A local plane through every 5x5 window of cells gives a normal and a flatness (RMS
       residual). Cells whose window is much rougher than the typical cell (depth edges,
       creases, step risers) are not used to connect anything.
    2. Neighbouring smooth cells are joined when their normals agree (within ``NORMAL_DEG``)
       and each lies on the other's local plane (within ``_tangent_tol``). Connected
       components are smooth surfaces.
    3. A component (or its largest part) is a plane when RANSAC puts most of its cells within
       ``plane_threshold_m`` of one plane; the rest stays non-planar (curved). A curved surface
       is not cut into planes this way, and parallel surfaces a few centimetres apart (steps, a
       board before a wall) stay apart.
    4. Every plane then claims the rough cells next to it that lie on it (its edges).
    """
    gh, gw = grid_z.shape
    jj, ii = np.meshgrid(np.arange(gw), np.arange(gh))
    us = jj * cell + (cell - 1) / 2
    vs = ii * cell + (cell - 1) / 2
    x = backproject(intr, us.ravel(), vs.ravel(), np.nan_to_num(grid_z.ravel(), nan=0.0))
    x = x.reshape(gh, gw, 3)
    have = np.isfinite(grid_z)
    n, rms = _local_planes(x, have, 5)
    flip = np.einsum("ijk,ijk->ij", n, x) > 0
    n[flip] *= -1
    zz = np.nan_to_num(grid_z, nan=0.0)
    finite = np.isfinite(rms)
    typical = float(np.median(rms[finite])) if finite.any() else 0.0
    smooth = finite & (rms < np.maximum(ROUGH_FACTOR * typical, ROUGH_M + ROUGH_REL * zz))
    cos_lim = math.cos(math.radians(NORMAL_DEG))

    def joined(a: tuple[slice, slice], b: tuple[slice, slice]) -> np.ndarray:
        na, nb, xa, xb = n[a], n[b], x[a], x[b]
        cosang = np.einsum("ijk,ijk->ij", na, nb)
        d = xb - xa
        tol = _tangent_tol(np.maximum(zz[a], zz[b]))
        ta = np.abs(np.einsum("ijk,ijk->ij", na, d))
        tb = np.abs(np.einsum("ijk,ijk->ij", nb, d))
        return np.asarray(smooth[a] & smooth[b] & (cosang > cos_lim) & (ta < tol) & (tb < tol))

    eh = joined((slice(None), slice(0, -1)), (slice(None), slice(1, None)))
    ev = joined((slice(0, -1), slice(None)), (slice(1, None), slice(None)))
    big = np.zeros((2 * gh - 1, 2 * gw - 1), np.uint8)
    big[0::2, 0::2] = smooth
    big[0::2, 1::2] = eh
    big[1::2, 0::2] = ev
    ncomp, comp = cv2.connectedComponents(big, connectivity=4)
    comp = comp[0::2, 0::2]
    labels = np.full((gh, gw), LABEL_NONPLANAR, np.int16)
    planes: list[Plane] = []
    sizes = np.bincount(comp[smooth].ravel(), minlength=ncomp)
    rng = np.random.default_rng(opts.seed)
    xw = transform(pose, x.reshape(-1, 3)).reshape(gh, gw, 3)
    thr = opts.plane_threshold_m
    for c in np.argsort(-sizes):
        if c == 0 or sizes[c] < MIN_PLANE_CELLS or len(planes) >= MAX_PLANES:
            continue
        mask = (comp == c) & smooth
        found = ransac_plane(xw[mask], thr, rng)
        if found is None:
            continue
        _, _, inl = found
        if inl.sum() < max(MIN_PLANE_CELLS, PLANE_SHARE * mask.sum()):
            continue
        sub = np.zeros((gh, gw), np.uint8)
        idx = np.argwhere(mask)[inl]
        sub[idx[:, 0], idx[:, 1]] = 1
        nsub, subc = cv2.connectedComponents(sub, connectivity=8)
        if nsub > 2:
            keep = int(np.bincount(subc[sub > 0].astype(np.int64)).argmax())
            sub = (subc == keep).astype(np.uint8)
        if int(sub.sum()) < MIN_PLANE_CELLS:
            continue
        sel = sub > 0
        nrm, d = fit_plane(xw[sel])
        resid = xw[sel] @ nrm + d
        plane = Plane(nrm, d, int(sel.sum()), float(np.sqrt(np.mean(resid**2))))
        # claim the rough cells along its border that lie on it
        ring = (cv2.dilate(sub, np.ones((5, 5), np.uint8)) > 0) & ~sel & have
        ring &= labels == LABEL_NONPLANAR
        ring &= ~smooth | (comp == c)
        on = np.zeros_like(ring)
        on[ring] = np.abs(xw[ring] @ nrm + d) < thr
        labels[sel | on] = len(planes)
        planes.append(plane)
    return planes, labels


def fuse(capture: Capture3d, opts: FusionOptions | None = None) -> SurfaceModel:
    opts = opts or FusionOptions()
    anchor = capture.anchor
    intr = anchor.intrinsics
    all_pts: list[np.ndarray] = []
    per_shot: dict[str, dict[str, int]] = {}
    for shot in capture.shots:
        if shot.depth is None:
            continue
        depth, conf, dintr = capture.load_depth(shot)
        ok, st = _clean_depth(depth, conf, opts)
        per_shot[shot.id] = st
        pc, _, _ = depth_image_points(dintr, depth, ok)
        if len(pc):
            all_pts.append(transform(shot.pose, pc))
    pts = np.concatenate(all_pts) if all_pts else np.zeros((0, 3))
    h, w = intr.height, intr.width
    empty = SurfaceModel(
        intr,
        anchor.pose,
        np.full((h, w), np.nan, np.float32),
        np.full((h, w), LABEL_UNKNOWN, np.int16),
        [],
        np.zeros((0, 3)),
        np.zeros(0, np.int32),
        {"shots": per_shot, "points": 0, "planes": [], "coverage": 0.0},
    )
    if len(pts) < 50:
        return empty

    vox, _ = _voxel_average(pts, opts.voxel_m)

    # splat into the Gray-code camera, cell grid
    cell = opts.cell_px
    gw, gh = (w + cell - 1) // cell, (h + cell - 1) // cell
    uv, z = project(intr, anchor.pose, pts)
    inside = (z > 0) & (uv[:, 0] > -0.5) & (uv[:, 0] < w - 0.5) & (uv[:, 1] > -0.5)
    inside &= uv[:, 1] < h - 0.5
    uv, z = uv[inside], z[inside]
    cu = np.clip(((uv[:, 0] + 0.5) // cell).astype(np.int64), 0, gw - 1)
    cv_ = np.clip(((uv[:, 1] + 0.5) // cell).astype(np.int64), 0, gh - 1)
    cid = cv_ * gw + cu
    order = np.lexsort((z, cid))
    cid, z = cid[order], z[order]
    cells, start, count = np.unique(cid, return_index=True, return_counts=True)
    z20 = z[start + (0.2 * (count - 1)).astype(np.int64)]
    near = z <= np.repeat(z20, count) + opts.occlusion_m
    cid, z = cid[near], z[near]
    cells, start, count = np.unique(cid, return_index=True, return_counts=True)
    zmed = z[start + (count - 1) // 2]

    flat_z = np.full(gh * gw, np.nan, np.float32)
    flat_z[cells] = zmed
    grid_z = flat_z.reshape(gh, gw)
    known = np.isfinite(grid_z)

    # edge-aware smoothing of the cell depths, then fill small holes
    filled0 = _nearest_fill(np.where(known, grid_z, 0).astype(np.float32), known)
    smooth = cv2.bilateralFilter(filled0, 5, SMOOTH_SIGMA_M, 1.0)
    grid_z = np.where(known, smooth, np.nan).astype(np.float32)
    reach = cv2.dilate(known.astype(np.uint8), np.ones((2 * opts.fill_cells + 1,) * 2, np.uint8))
    fillable = (reach > 0) & ~known
    grid_z = np.where(fillable, _nearest_fill(np.nan_to_num(grid_z), known), grid_z)
    have = np.isfinite(grid_z)

    # planes: smooth patches of the cell grid, each tested for flatness
    planes, grid_l = _segment_grid(grid_z, intr, anchor.pose, cell, opts)
    grid_l[~have] = LABEL_UNKNOWN
    # refit every plane on the raw points of its cells (more points than cells)
    if planes:
        pts_kept = pts[inside][order][near]
        lab_pts = grid_l.reshape(-1)[cid]
        refined: list[Plane] = []
        for k, plane in enumerate(planes):
            pw = pts_kept[lab_pts == k]
            close = np.abs(plane.distance(pw)) < opts.plane_threshold_m
            if close.sum() >= 50:
                n, d = fit_plane(pw[close])
                r = pw[close] @ n + d
                refined.append(Plane(n, d, int(close.sum()), float(np.sqrt(np.mean(r**2)))))
            else:
                refined.append(plane)
        planes = refined
    vox_labels = np.full(len(vox), LABEL_NONPLANAR, np.int32)
    for k, plane in enumerate(planes):
        vox_labels[(vox_labels < 0) & (np.abs(plane.distance(vox)) < opts.plane_threshold_m)] = k

    # upsample to pixels: bilinear where the 4 cells agree, nearest at depth edges
    xs = ((np.arange(w) + 0.5) / cell - 0.5).astype(np.float32)
    ys = ((np.arange(h) + 0.5) / cell - 0.5).astype(np.float32)
    mx = np.tile(xs, (h, 1))
    my = np.repeat(ys[:, None], w, axis=1)
    zf = np.nan_to_num(grid_z).astype(np.float32)
    lin = cv2.remap(zf, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    nearest = cv2.remap(zf, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE)
    hv = cv2.remap(
        have.astype(np.uint8), mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE
    )
    lab_px = cv2.remap(
        grid_l.astype(np.float32), mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE
    ).astype(np.int16)
    zmin = cv2.erode(np.where(have, zf, 1e6).astype(np.float32), np.ones((2, 2), np.uint8))
    zmax = cv2.dilate(zf, np.ones((2, 2), np.uint8))
    spread = cv2.remap(zmax - zmin, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE)
    depth = np.where(spread < opts.occlusion_m / 2, lin, nearest).astype(np.float32)
    depth[hv == 0] = np.nan
    lab_px[hv == 0] = LABEL_UNKNOWN

    # snap plane pixels onto their plane
    vs, us = np.mgrid[0:h, 0:w]
    rays_c = pixel_rays(intr, us.ravel(), vs.ravel())
    flat_depth = depth.ravel()
    flat_lab = lab_px.ravel()
    for k, plane in enumerate(planes):
        m = flat_lab == k
        if not m.any():
            continue
        zp = _plane_z(rays_c[m], anchor.pose, plane)
        agree = np.abs(zp - flat_depth[m]) < 2 * opts.plane_threshold_m + 0.02
        sel = np.nonzero(m)[0]
        flat_depth[sel[agree]] = zp[agree]
        flat_lab[sel[~agree]] = LABEL_NONPLANAR
    depth = flat_depth.reshape(h, w)
    label = flat_lab.reshape(h, w)

    stats: dict[str, Any] = {
        "shots": per_shot,
        "points": int(len(pts)),
        "voxels": int(len(vox)),
        "planes": [
            {
                "normal": [round(float(v), 4) for v in p.normal],
                "d": round(p.d, 4),
                "voxels": p.inliers,
                "rms_m": round(p.rms, 4),
            }
            for p in planes
        ],  # fmt: skip
        "coverage": float(np.isfinite(depth).mean()),
        "planar_fraction": float((label >= 0).mean()),
    }
    return SurfaceModel(intr, anchor.pose, depth, label, planes, vox, vox_labels, stats)
