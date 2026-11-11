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


# Albedo-edge weighting (docs/07-scan-memory.md, "Albedo-edge bias in the projector map").
# Next to a strong reflectance edge every decoded coordinate is biased: the camera blurs the
# product albedo x stripe, so a stripe edge's zero crossing (pattern = inverse) moves towards
# the darker side, by about sigma_psf^2 * |grad log(albedo)| to first order. No per-pixel
# threshold (raw or normalised difference) can undo that. The white-minus-black image is the
# blurred albedo times the smooth projector light, so |grad log(contrast)| shows where the bias
# is, without knowing the blur, the camera gamma or the wall. A pixel's weight halves at
# EDGE_GRAD_SCALE.
EDGE_GRAD_SCALE = 0.05  # |d log(contrast) / d camera px|
# Local fit: nodes within FIT_RADIUS cells, Gaussian weights with sigma FIT_SIGMA cells. The
# slopes are pulled towards the global homography's with strength FIT_RIDGE (in units of
# summed sample weight x cells^2), which only matters where local data cannot fix them.
FIT_RADIUS = 2
FIT_SIGMA = 1.0
FIT_RIDGE = 1.0
_MIN_SUPPORT = 1e-6


def albedo_edge_weight(contrast: np.ndarray) -> np.ndarray:
    """Per camera pixel trust in (0, 1]: low next to strong reflectance edges, 1 on flat walls.

    Uses log(contrast), so the weight does not depend on brightness or exposure.
    """
    lc = np.log(np.maximum(contrast, 1.0)).astype(np.float32)
    gx = cv2.Sobel(lc, cv2.CV_32F, 1, 0, ksize=3, scale=1 / 8)
    gy = cv2.Sobel(lc, cv2.CV_32F, 0, 1, ksize=3, scale=1 / 8)
    g2 = (gx * gx + gy * gy) * np.float32(1.0 / EDGE_GRAD_SCALE**2)
    return np.asarray(1.0 / (1.0 + g2), dtype=np.float32)


def _block_measurements(
    px: np.ndarray, py: np.ndarray, block: int, *values: np.ndarray
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Pool samples by decoded block: (pixel count, [sum of each value]) per non-empty block.

    The pixels of one decoded block share one projector coordinate (the block centre), so the
    block's camera centroid is the measurement. Weighting pixels unevenly inside a block would
    move that centroid away from an edge and bias the map the other way, so a block's weight is
    the sum of its pixels' weights and it enters the fit once, at its centroid.
    """
    bx = np.floor(px / block).astype(np.int64)
    by = np.floor(py / block).astype(np.int64)
    bx -= bx.min()
    by -= by.min()
    bid = by * (int(bx.max()) + 1) + bx
    count = np.bincount(bid)
    keep = count > 0
    sums = [np.bincount(bid, weights=v, minlength=len(count))[keep] for v in values]
    return count[keep].astype(np.float64), sums


def _local_fit(
    flat: np.ndarray,
    shape: tuple[int, int],
    dx: np.ndarray,
    dy: np.ndarray,
    w: np.ndarray,
    cam_u: np.ndarray,
    cam_v: np.ndarray,
    prior_slopes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted local affine fit camera = c + J (p - node) around every node.

    Samples are binned into their nearest node; each node pools the moments of the nodes
    within FIT_RADIUS, re-centred on itself, and solves the 3x3 normal equations. `dx`, `dy`
    are each sample's projector offset from its own node, in cells. `prior_slopes`
    (gh, gw, 2, 2) holds d(camera u, v)/d(cell x, y) from the global homography, the ridge
    prior. Returns (grid (gh, gw, 2) float64, support (gh, gw) = pooled sample weight).
    """
    gh, gw = shape
    n = gh * gw

    def acc(x: np.ndarray) -> np.ndarray:
        return np.bincount(flat, weights=x, minlength=n).reshape(gh, gw)

    wx, wy = w * dx, w * dy
    s1, sx, sy = acc(w), acc(wx), acc(wy)
    sxx, sxy, syy = acc(wx * dx), acc(wx * dy), acc(wy * dy)
    su = [(acc(w * c), acc(wx * c), acc(wy * c)) for c in (cam_u, cam_v)]

    # Node (i + di, j + dj)'s samples sit at offset (dx + dj, dy + di) from node (i, j), so the
    # pooled moments are correlations of the per-node moments with K, K*dj, K*di, ...
    # (cv2.filter2D correlates: out[i, j] = sum K[di, dj] * src[i + di, j + dj]).
    r = FIT_RADIUS
    off = np.arange(-r, r + 1, dtype=np.float64)
    kern = np.exp(-0.5 * (off[:, None] ** 2 + off[None, :] ** 2) / FIT_SIGMA**2)
    kj = kern * off[None, :]
    ki = kern * off[:, None]

    def pool(src: np.ndarray, k: np.ndarray) -> np.ndarray:
        return np.asarray(
            cv2.filter2D(src, cv2.CV_64F, k, borderType=cv2.BORDER_CONSTANT), dtype=np.float64
        )

    a1 = pool(s1, kern)
    ax = pool(sx, kern) + pool(s1, kj)
    ay = pool(sy, kern) + pool(s1, ki)
    axx = pool(sxx, kern) + 2 * pool(sx, kj) + pool(s1, kj * off[None, :])
    ayy = pool(syy, kern) + 2 * pool(sy, ki) + pool(s1, ki * off[:, None])
    axy = pool(sxy, kern) + pool(sy, kj) + pool(sx, ki) + pool(s1, kj * off[:, None])

    # Normal matrix [[a, b, c], [b, d, e], [c, e, f]], positive definite thanks to the ridge
    # and _MIN_SUPPORT. Only the intercept is needed: first row of the inverse (cofactors).
    a, b, c = a1 + _MIN_SUPPORT, ax, ay
    d, e, f = axx + FIT_RIDGE, axy, ayy + FIT_RIDGE
    co0, co1, co2 = d * f - e * e, c * e - b * f, b * e - c * d
    det = a * co0 + b * co1 + c * co2
    grid = np.empty((gh, gw, 2))
    for ci, (m0, mx, my) in enumerate(su):
        r0 = pool(m0, kern)
        r1 = pool(mx, kern) + pool(m0, kj) + FIT_RIDGE * prior_slopes[..., ci, 0]
        r2 = pool(my, kern) + pool(m0, ki) + FIT_RIDGE * prior_slopes[..., ci, 1]
        grid[..., ci] = (co0 * r0 + co1 * r1 + co2 * r2) / det
    return grid, a1


def build_maps(
    decoded: DecodeResult, spec: PatternSpec, grid_step: int | None = None
) -> ProjectorMaps:
    """Projector -> camera map from decoded camera pixels.

    Node (i, j) of the coarse grid sits at projector corner-coordinate (j * g, i * g) and holds
    the camera position (pixel-corner coordinates) of that projector point. It is a weighted
    local affine fit to the decoded blocks of the surrounding nodes, so it is exact for any
    smooth map (lens distortion included) and does not depend on the global homography except
    as a weak prior on the slopes. Pixels next to reflectance edges get little weight (see
    `albedo_edge_weight`), which removes the decode bias there.
    """
    # Decoded positions are the centres of blocks of 2**skip_lsb projector pixels. The grid
    # must be at least two blocks wide: at exactly one block every centre falls halfway
    # between two nodes and rounding leaves three nodes in four empty.
    block = 1 << spec.skip_lsb
    if grid_step is None:
        grid_step = max(8, 2 * block)
    if grid_step % (2 * block) != 0:
        raise ValueError(f"grid_step {grid_step} must be a multiple of {2 * block}")
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
    weight = albedo_edge_weight(decoded.contrast)[vs, us].astype(np.float64)

    # 1. one measurement per decoded block (camera centroid, summed weight), each binned into
    # its nearest node of a coarse projector grid
    n_b, (w_b, su_b, sv_b, spx_b, spy_b) = _block_measurements(
        px, py, block, weight, cam_u, cam_v, px, py
    )
    u_b, v_b, px_b, py_b = su_b / n_b, sv_b / n_b, spx_b / n_b, spy_b / n_b
    gw, gh = w_px // g + 1, h_px // g + 1
    j = np.clip(np.round(px_b / g), 0, gw - 1).astype(np.int64)
    i = np.clip(np.round(py_b / g), 0, gh - 1).astype(np.int64)
    flat = i * gw + j
    counts = np.bincount(flat, weights=n_b, minlength=gw * gh).reshape(gh, gw)
    measured_grid = counts > 0
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

    # 3. local fit at every node with data within FIT_RADIUS
    ii, jj = np.mgrid[0:gh, 0:gw]
    node_p = np.stack([jj.ravel() * g, ii.ravel() * g], axis=1).astype(np.float64)
    slopes = np.zeros((gh, gw, 2, 2))
    if h_c2p is not None:
        inv_h = np.linalg.inv(h_c2p)
        for axis in (0, 1):
            step = np.zeros(2)
            step[axis] = 0.5 * g
            d = apply_h(inv_h, node_p + step) - apply_h(inv_h, node_p - step)
            slopes[..., :, axis] = d.reshape(gh, gw, 2)
    grid, support = _local_fit(flat, (gh, gw), px_b / g - j, py_b / g - i, w_b, u_b, v_b, slopes)
    grid_u: np.ndarray = grid[..., 0].astype(np.float32)
    grid_v: np.ndarray = grid[..., 1].astype(np.float32)
    fitted = support > _MIN_SUPPORT

    # 4. fill nodes with no data nearby
    wt = fitted.astype(np.float32)
    blur_u = cv2.GaussianBlur(grid_u * wt, (0, 0), 2.0)
    blur_v = cv2.GaussianBlur(grid_v * wt, (0, 0), 2.0)
    blur_w = cv2.GaussianBlur(wt, (0, 0), 2.0)
    near = (~fitted) & (blur_w > 0.05)
    grid_u[near] = blur_u[near] / blur_w[near]
    grid_v[near] = blur_v[near] / blur_w[near]
    far = (~fitted) & (~near)
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

    # 5. smooth and upsample. Node (i, j) sits at projector corner-coordinate (j * g, i * g)
    # and stores a camera position in pixel-corner coordinates. The value for projector pixel
    # (x, y) is taken at its centre (x + 0.5, y + 0.5); cv2.remap indexes the grid by node
    # index, so the sample position is (x + 0.5) / g (not cv2.resize, which assumes
    # pixel-centre alignment).
    grid_u = cv2.medianBlur(grid_u, 3)
    grid_v = cv2.medianBlur(grid_v, 3)
    col = (np.arange(w_px) + 0.5) / g
    row = (np.arange(h_px) + 0.5) / g
    gx = np.tile(col.astype(np.float32), (h_px, 1))
    gy = np.repeat(row.astype(np.float32)[:, None], w_px, axis=1)
    p2c_u = cv2.remap(grid_u, gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    p2c_v = cv2.remap(grid_v, gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    p2c = np.stack([p2c_u, p2c_v], axis=-1).astype(np.float32)
    # nearest node, consistent with the node-centred convention above
    nj = np.clip(np.round(col), 0, gw - 1).astype(np.int64)
    ni = np.clip(np.round(row), 0, gh - 1).astype(np.int64)
    measured = measured_grid[ni[:, None], nj[None, :]]

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
