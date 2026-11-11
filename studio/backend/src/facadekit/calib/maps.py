from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from facadekit.calib.decode import DecodeResult
from facadekit.calib.patterns import PatternSpec
from facadekit.errors import FkError
from facadekit.geom.homography import apply_h

MIN_VALID_PIXELS = 1000
MAX_HOMOGRAPHY_SAMPLES = 20000


@dataclass
class ProjectorMaps:
    p2c: (
        np.ndarray
    )  # float32 (H, W, 2): camera (x, y), pixel-corner coords, of each projector pixel centre
    measured: np.ndarray  # bool (H, W): True where backed by real measurements
    h_c2p: np.ndarray | None  # (3, 3) float64 global camera->projector homography
    coverage: float  # fraction of grid cells measured
    planarity: float  # RANSAC inlier fraction of h_c2p (0 if h_c2p is None)


def build_maps(decoded: DecodeResult, spec: PatternSpec, grid_step: int = 8) -> ProjectorMaps:
    if grid_step % (1 << spec.skip_lsb) != 0:
        raise ValueError(f"grid_step {grid_step} must be a multiple of {1 << spec.skip_lsb}")
    w_px, h_px = spec.width, spec.height
    g = grid_step
    vs, us = np.nonzero(decoded.valid)
    if len(us) < MIN_VALID_PIXELS:
        raise FkError("SCAN_FAILED", f"Only {len(us)} valid camera pixels were decoded")
    # Camera pixel (u, v) is centred at (u + 0.5, v + 0.5) in pixel-corner coordinates.
    cam_u = us + 0.5
    cam_v = vs + 0.5
    px = decoded.c2p[vs, us, 0].astype(np.float64)
    py = decoded.c2p[vs, us, 1].astype(np.float64)

    # 1. accumulate camera coordinates into a coarse projector grid
    gw, gh = w_px // g + 1, h_px // g + 1
    j = np.clip(np.round(px / g), 0, gw - 1).astype(np.int64)
    i = np.clip(np.round(py / g), 0, gh - 1).astype(np.int64)
    flat = i * gw + j
    n = gw * gh
    counts = np.bincount(flat, minlength=n).reshape(gh, gw)
    sum_u = np.bincount(flat, weights=cam_u, minlength=n).reshape(gh, gw)
    sum_v = np.bincount(flat, weights=cam_v, minlength=n).reshape(gh, gw)
    sum_px = np.bincount(flat, weights=px, minlength=n).reshape(gh, gw)
    sum_py = np.bincount(flat, weights=py, minlength=n).reshape(gh, gw)
    measured_grid = counts > 0
    grid_u = np.divide(sum_u, counts, out=np.zeros((gh, gw), np.float64), where=measured_grid)
    grid_v = np.divide(sum_v, counts, out=np.zeros((gh, gw), np.float64), where=measured_grid)
    grid_u = grid_u.astype(np.float32)
    grid_v = grid_v.astype(np.float32)
    coverage = float(measured_grid.mean())

    # 2. global homography camera -> projector
    rng = np.random.default_rng(0)
    if len(us) > MAX_HOMOGRAPHY_SAMPLES:
        pick = rng.choice(len(us), MAX_HOMOGRAPHY_SAMPLES, replace=False)
    else:
        pick = np.arange(len(us))
    cam_pts = np.stack([cam_u[pick], cam_v[pick]], axis=1).astype(np.float64)
    proj_pts = np.stack([px[pick], py[pick]], axis=1)
    h_c2p, inlier_mask = cv2.findHomography(cam_pts, proj_pts, cv2.RANSAC, 6.0)
    if h_c2p is not None and inlier_mask is not None:
        h_c2p = np.asarray(h_c2p, dtype=np.float64)
        planarity = float(inlier_mask.astype(np.float64).mean())
    else:
        h_c2p = None
        planarity = 0.0

    # A node's pixels are centred on its projector position only away from the edge of the
    # measured region. Move each node's mean camera position to the node's exact projector
    # position (j * g, i * g) using the global homography as the local model.
    if h_c2p is not None:
        mi, mj = np.nonzero(measured_grid)
        inv_h = np.linalg.inv(h_c2p)
        mean_p = np.stack([sum_px[mi, mj], sum_py[mi, mj]], axis=1) / counts[mi, mj][:, None]
        node_p = np.stack([mj * g, mi * g], axis=1).astype(np.float64)
        delta = apply_h(inv_h, node_p) - apply_h(inv_h, mean_p)
        grid_u[mi, mj] += delta[:, 0].astype(np.float32)
        grid_v[mi, mj] += delta[:, 1].astype(np.float32)

    # 3. fill holes
    wt = measured_grid.astype(np.float32)
    blur_u = cv2.GaussianBlur(grid_u * wt, (0, 0), 2.0)
    blur_v = cv2.GaussianBlur(grid_v * wt, (0, 0), 2.0)
    blur_w = cv2.GaussianBlur(wt, (0, 0), 2.0)
    near = (~measured_grid) & (blur_w > 0.05)
    grid_u[near] = blur_u[near] / blur_w[near]
    grid_v[near] = blur_v[near] / blur_w[near]
    far = (~measured_grid) & (~near)
    if far.any():
        if h_c2p is not None:
            fi, fj = np.nonzero(far)
            centres = np.stack([fj * g, fi * g], axis=1).astype(np.float64).reshape(1, -1, 2)
            cam = cv2.perspectiveTransform(centres, np.linalg.inv(h_c2p)).reshape(-1, 2)
            grid_u[far] = cam[:, 0].astype(np.float32)
            grid_v[far] = cam[:, 1].astype(np.float32)
        else:
            big_u = cv2.GaussianBlur(grid_u * wt, (0, 0), 8.0)
            big_v = cv2.GaussianBlur(grid_v * wt, (0, 0), 8.0)
            big_w = np.maximum(cv2.GaussianBlur(wt, (0, 0), 8.0), 1e-6)
            grid_u[far] = big_u[far] / big_w[far]
            grid_v[far] = big_v[far] / big_w[far]

    # 4. smooth and upsample. Node (i, j) sits at projector corner-coordinate (j * g, i * g)
    # and stores a camera position in pixel-corner coordinates. The value for projector pixel
    # (x, y) is taken at its centre (x + 0.5, y + 0.5); cv2.remap indexes the grid by node
    # index, so the sample position is (x + 0.5) / g (not cv2.resize, which assumes
    # pixel-centre alignment).
    grid_u = cv2.medianBlur(grid_u, 3)
    grid_v = cv2.medianBlur(grid_v, 3)
    xs, ys = np.meshgrid(np.arange(w_px), np.arange(h_px))
    gx = ((xs + 0.5) / g).astype(np.float32)
    gy = ((ys + 0.5) / g).astype(np.float32)
    p2c_u = cv2.remap(grid_u, gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    p2c_v = cv2.remap(grid_v, gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    p2c = np.stack([p2c_u, p2c_v], axis=-1).astype(np.float32)
    # nearest node, consistent with the node-centred convention above
    nj = np.clip(np.round((xs + 0.5) / g), 0, gw - 1).astype(np.int64)
    ni = np.clip(np.round((ys + 0.5) / g), 0, gh - 1).astype(np.int64)
    measured = measured_grid[ni, nj]

    return ProjectorMaps(
        p2c=p2c, measured=measured, h_c2p=h_c2p, coverage=coverage, planarity=planarity
    )


def make_proj_view(white_bgr: np.ndarray, maps: ProjectorMaps) -> np.ndarray:
    """Resample the camera photo into projector pixels.

    ``maps.p2c`` holds pixel-corner coordinates; cv2.remap wants pixel-index coordinates
    (integer = pixel centre), hence the -0.5.
    """
    out = cv2.remap(
        white_bgr,
        np.ascontiguousarray(maps.p2c[..., 0] - np.float32(0.5)),
        np.ascontiguousarray(maps.p2c[..., 1] - np.float32(0.5)),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )
    return np.asarray(out, dtype=np.uint8)
