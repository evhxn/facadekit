"""Scan-corrected surface: sharpen the depth sensor's surface with the Gray-code scan.

Once the projector is solved, every decoded camera pixel is a projector-camera triangulation,
which is far more precise than a phone depth sensor (at 2 m with a 0.5 m baseline, half a
projector pixel is about a millimetre of depth; the sensor is about a centimetre). This module

1. triangulates every decoded block with the solved projector,
2. refits each plane on the triangulated points that lie on it,
3. adds a smooth correction field (triangulated minus sensor depth, spread with a Gaussian
   inside each surface label) to the sensor depth, so the surface agrees with the scan where
   the scan has data and falls back to the sensor's depth where it has none (gaps, shadows,
   dark paint),
4. drops the plane label where the scan says the pixel is not on that plane (two surfaces a
   centimetre or two apart that the sensor could not tell apart, a box side the sensor merged
   with the wall),
5. off the planes and within a few pixels of scan data, uses the triangulated depth itself
   (interpolated between blocks).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import cv2
import numpy as np

from facadekit.calib3d.fusion import LABEL_NONPLANAR, SurfaceModel
from facadekit.calib3d.geometry import pose_inverse, transform
from facadekit.calib3d.planes import Plane, fit_plane
from facadekit.calib3d.projector import ProjectorSolution

SIGMA_PX = 12.0  # spread of the correction field, camera px
TRUST_DENSITY = 0.05  # samples per pixel at which a spread field is fully trusted
MAX_JUMP_M = 0.15  # triangulated points further than this from the sensor surface are dropped
OFF_PLANE_M = 0.006  # a plane pixel whose correction exceeds this is taken off the plane
SCAN_SIGMA_PX = 2.0  # off the planes, pixels near decoded blocks take their interpolated depth
SCAN_MIN_WEIGHT = 0.02  # ... where the blocks' Gaussian weight exceeds this
SCAN_MAX_JUMP_M = 0.6  # ... unless it is further than this from the sensor's depth
PLANE_TOL_M = 0.01


def spread_by_label(
    values: np.ndarray,
    ui: np.ndarray,
    vi: np.ndarray,
    sample_label: np.ndarray,
    label: np.ndarray,
    sigma: float,
) -> np.ndarray:
    """Spread sparse samples (N, C) at pixels (ui, vi) into a smooth (H, W, C) field, separately
    inside every surface label (so nothing leaks across a depth edge). The field fades to zero
    where samples are sparse (normalised convolution times a trust factor)."""
    h, w = label.shape
    c = values.shape[1]
    out = np.zeros((h, w, c))
    for k in np.unique(label[label > -2]):
        region = (label == k).astype(np.float32)
        sel = sample_label == k
        if not sel.any():
            continue
        cnt = np.zeros((h, w), np.float32)
        np.add.at(cnt, (vi[sel], ui[sel]), 1.0)
        cnt_b = cv2.GaussianBlur(cnt * region, (0, 0), sigma)
        wsum = cv2.GaussianBlur(region, (0, 0), sigma)
        with np.errstate(invalid="ignore", divide="ignore"):
            trust = np.clip(cnt_b / np.maximum(wsum, 1e-6) / TRUST_DENSITY, 0.0, 1.0)
        inside = region > 0
        for ch in range(c):
            acc = np.zeros((h, w), np.float32)
            np.add.at(acc, (vi[sel], ui[sel]), values[sel, ch].astype(np.float32))
            acc_b = cv2.GaussianBlur(acc * region, (0, 0), sigma)
            with np.errstate(invalid="ignore", divide="ignore"):
                mean = acc_b / np.maximum(cnt_b, 1e-6)
            out[..., ch][inside] = (mean * trust)[inside]
    return out


def triangulate(
    surface: SurfaceModel, sol: ProjectorSolution, cam: np.ndarray, proj: np.ndarray
) -> np.ndarray:
    """World points (N, 3) from camera pixels and projector pixels (linear triangulation)."""
    assert sol.intrinsics is not None and sol.pose is not None
    p_cam = surface.intrinsics.k @ pose_inverse(surface.pose)[:3]
    p_proj = sol.intrinsics.k @ pose_inverse(sol.pose)[:3]
    xh = cv2.triangulatePoints(p_cam, p_proj, cam.T.astype(np.float64), proj.T.astype(np.float64))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray((xh[:3] / xh[3]).T)


def scan_corrected(
    surface: SurfaceModel, sol: ProjectorSolution, cam: np.ndarray, proj: np.ndarray
) -> tuple[SurfaceModel, dict[str, Any]]:
    if not sol.usable or len(cam) == 0:
        return surface, {"applied": False}
    xw = triangulate(surface, sol, cam, proj)
    zc = transform(pose_inverse(surface.pose), xw)[:, 2]
    h, w = surface.depth.shape
    ui = np.clip(np.rint(cam[:, 0]).astype(np.int64), 0, w - 1)
    vi = np.clip(np.rint(cam[:, 1]).astype(np.int64), 0, h - 1)
    z_sensor = surface.depth[vi, ui].astype(np.float64)
    lab = surface.label[vi, ui]
    d = zc - z_sensor
    ok = np.isfinite(d) & (np.abs(d) < MAX_JUMP_M) & (zc > 0)

    # 2. planes refitted on the triangulated points that lie on them
    planes: list[Plane] = []
    for k, plane in enumerate(surface.planes):
        m = ok & (lab == k)
        m[m] = np.abs(plane.distance(xw[m])) < PLANE_TOL_M
        if m.sum() >= 50:
            n, dd = fit_plane(xw[m])
            r = xw[m] @ n + dd
            planes.append(Plane(n, dd, int(m.sum()), float(np.sqrt(np.mean(r**2)))))
        else:
            planes.append(plane)

    # 3. correction field per label, relative to the (refitted) surface
    base = surface.depth.astype(np.float64).copy()
    label = surface.label.copy()
    refit = replace(surface, planes=planes)
    for k in range(len(planes)):
        m = label == k
        if m.any():
            ys, xs = np.nonzero(m)
            base[ys, xs] = transform(
                pose_inverse(surface.pose),
                refit.world_points_at(np.stack([xs, ys], axis=1).astype(np.float64)),
            )[:, 2]
    d = zc - base[vi, ui]
    ok &= np.isfinite(d) & (np.abs(d) < MAX_JUMP_M)
    field = spread_by_label(d[ok, None], ui[ok], vi[ok], lab[ok], label, SIGMA_PX)[..., 0]
    depth = np.where(np.isfinite(base), base + field, np.nan)
    off = (label >= 0) & (np.abs(field) > OFF_PLANE_M)

    # 5. where the scan has data, its own triangulated depth (nearest block within SCAN_REACH_PX)
    near_ok = np.isfinite(zc) & (zc > 0) & ~(np.abs(zc - z_sensor) > SCAN_MAX_JUMP_M)
    zimg = np.full((h, w), np.nan)
    zimg[vi[near_ok], ui[near_ok]] = zc[near_ok]
    known = np.isfinite(zimg)
    if known.any():
        # smooth interpolation between blocks (normalised Gaussian), not nearest: a block's
        # depth held constant over its pixels would be a staircase of +-half a block
        kz = cv2.GaussianBlur(np.where(known, zimg, 0).astype(np.float32), (0, 0), SCAN_SIGMA_PX)
        kw = cv2.GaussianBlur(known.astype(np.float32), (0, 0), SCAN_SIGMA_PX)
        reach = kw > SCAN_MIN_WEIGHT
        with np.errstate(invalid="ignore", divide="ignore"):
            nearest = np.where(reach, kz / np.maximum(kw, 1e-9), np.nan)
        disagree = reach & (label >= 0) & (np.abs(nearest - depth) > OFF_PLANE_M)
        off |= disagree
        use = reach & ((label < 0) | off)
        depth = np.where(use, nearest, depth)
    label[off] = LABEL_NONPLANAR
    depth = depth.astype(np.float32)
    stats = {
        "applied": True,
        "triangulated": int(ok.sum()),
        "median_abs_correction_m": round(float(np.median(np.abs(d[ok]))) if ok.any() else 0.0, 4),
        "pixels_taken_off_planes": int(off.sum()),
    }
    out = replace(
        surface,
        depth=depth,
        label=label,
        planes=planes,
        stats={**surface.stats, "scan_correction": stats},
    )
    return out, stats
