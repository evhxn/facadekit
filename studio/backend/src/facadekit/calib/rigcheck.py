"""Has the camera or the projector moved since the scan? (docs/13-surface-verification.md)

Two independent measurements:

* **Dots.** A sparse, jittered constellation of dots is projected. Each dot should appear in
  the camera where the scan's map says. Their displacement is the combined effect of a camera
  move and a projector move.
* **Wall texture.** The surface pattern of the wall (brick, siding, the door frame) under the
  white picture moves in the camera picture only when the *camera* moves. It is compared with
  the scan's own white picture (high-pass of the log reflectance, so brightness and exposure
  do not matter).

Removing the camera motion from the dot displacement leaves the projector motion. When the
wall has too little texture the camera motion is unknown, and the check says so instead of
guessing which device moved.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from facadekit.calib.p2cmap import ProjCamMap
from facadekit.geom.homography import apply_h

SPACING_FRACTION = 1.0 / 11.0  # dot spacing, fraction of the projector's longer side
JITTER = 0.3  # of the spacing; breaks the grid's periodicity so matching cannot alias
DOT_MIN_CAM_RADIUS = 3.0  # camera px
DOT_MIN_PROJ_RADIUS = 3.0  # projector px
LAYOUT_SEED = 20261122

# Decision thresholds. Set from the simulator evaluation (docs/13 section "Thresholds"):
# the smallest values whose false-alarm rate on stable train scenes stayed at 0.
PROJ_MOVED_PX = 0.75  # projector px, median dot displacement after removing camera motion
CAM_MOVED_PX = 0.7  # camera px, median texture displacement over the lit area
NOISE_K = 3.0  # a move must also exceed this many standard errors of the dots' fit
MIN_DOTS = 8  # fewer matched dots: the check cannot judge
MIN_TEXTURE_INLIERS = 20
MIN_ECC_SCORE = 0.3  # texture correlation needed to trust the camera-motion estimate

TEX_SIGMA = 6.0
TEX_ERODE_PX = 20  # > 3 sigma: the lit border's own edge must not count as texture
TEX_CLIP = 0.8  # log units; keeps a few very dark or clipped pixels from dominating
TEX_MIN_LEVEL = 10.0  # gray level; darker pixels carry no usable texture


@dataclass
class DotLayout:
    points: np.ndarray  # (N, 2) projector px (pixel-corner coordinates)
    radius: float  # projector px
    spacing: float  # projector px


def dot_layout(pmap: ProjCamMap, scale: float, *, seed: int = LAYOUT_SEED) -> DotLayout:
    w, h = pmap.proj_size
    spacing = max(w, h) * SPACING_FRACTION
    radius = max(DOT_MIN_PROJ_RADIUS, float(np.ceil(DOT_MIN_CAM_RADIUS / max(scale, 1e-3))))
    radius = min(radius, spacing / 5)
    rng = np.random.default_rng(seed)
    xs = np.arange(spacing / 2, w, spacing)
    ys = np.arange(spacing / 2, h, spacing)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    pts = pts + rng.uniform(-JITTER, JITTER, pts.shape) * spacing
    margin = 2 * radius + 2
    inside = (
        (pts[:, 0] > margin)
        & (pts[:, 0] < w - margin)
        & (pts[:, 1] > margin)
        & (pts[:, 1] < h - margin)
    )
    pts = pts[inside]
    # keep dots whose whole disc was measured by the scan
    ring = np.array(
        [[0.0, 0.0]]
        + [[radius * np.cos(a), radius * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 8, False)]
    )
    _, centre_ok = pmap.to_cam(pts)
    count = np.zeros(len(pts), int)
    for off in ring[1:]:
        _, valid = pmap.to_cam(pts + off)
        count += valid
    ok = centre_ok & (count >= 6)  # a speckled measured mask must not empty the layout
    return DotLayout(points=pts[ok], radius=radius, spacing=spacing)


def render_dots(size: tuple[int, int], points: np.ndarray, radius: float) -> np.ndarray:
    w, h = size
    img = np.zeros((h, w), np.uint8)
    shift = 4
    for x, y in points:
        centre = (int(round((x - 0.5) * 16)), int(round((y - 0.5) * 16)))
        cv2.circle(img, centre, int(round(radius * 16)), 255, -1, cv2.LINE_AA, shift)
    return img


def find_dots(q: np.ndarray, valid: np.ndarray, radius_cam: float) -> np.ndarray:
    """Centroids (pixel-corner coordinates) of the lit blobs in a ratio image."""
    lit = ((q > 0.5) & valid).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(lit, connectivity=8)
    area_lo = max(3.0, 0.25 * np.pi * radius_cam**2)
    area_hi = 4.0 * np.pi * (radius_cam + 2) ** 2
    weights = np.clip(q, 0.0, 1.5)
    out: list[tuple[float, float]] = []
    hq, wq = q.shape
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < area_lo or area > area_hi:
            continue
        x0 = max(int(stats[i, cv2.CC_STAT_LEFT]) - 2, 0)
        y0 = max(int(stats[i, cv2.CC_STAT_TOP]) - 2, 0)
        x1 = min(x0 + int(stats[i, cv2.CC_STAT_WIDTH]) + 4, wq)
        y1 = min(y0 + int(stats[i, cv2.CC_STAT_HEIGHT]) + 4, hq)
        win = weights[y0:y1, x0:x1] * (weights[y0:y1, x0:x1] > 0.15)
        own = cv2.dilate((labels[y0:y1, x0:x1] == i).astype(np.uint8), np.ones((5, 5), np.uint8))
        win = win * own
        total = float(win.sum())
        if total <= 0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        out.append(
            (float((win * (xx + 0.5)).sum() / total), float((win * (yy + 0.5)).sum() / total))
        )
    return np.array(out, dtype=np.float64).reshape(-1, 2)


def _blob_image(pts: np.ndarray, size: tuple[int, int], factor: int) -> np.ndarray:
    w, h = size
    img = np.zeros((h // factor + 1, w // factor + 1), np.float32)
    for x, y in pts:
        ix, iy = int(x / factor), int(y / factor)
        if 0 <= ix < img.shape[1] and 0 <= iy < img.shape[0]:
            img[iy, ix] = 1.0
    return np.asarray(cv2.GaussianBlur(img, (0, 0), 1.5))


def _nearest(a: np.ndarray, b: np.ndarray, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Mutual nearest neighbours between point sets a and b within `tol`."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros(0, int), np.zeros(0, int)
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    ab = np.argmin(d, axis=1)
    ba = np.argmin(d, axis=0)
    ia = np.arange(len(a))
    keep = (ba[ab] == ia) & (d[ia, ab] < tol)
    return ia[keep], ab[keep]


@dataclass
class DotMatch:
    idx_expected: np.ndarray
    observed: np.ndarray  # (K, 2) camera px, matched to expected[idx_expected]
    h: np.ndarray | None  # expected -> observed, camera px


def match_dots(
    expected: np.ndarray, observed: np.ndarray, cam_size: tuple[int, int], spacing_cam: float
) -> DotMatch:
    empty = DotMatch(np.zeros(0, int), np.zeros((0, 2)), None)
    if len(expected) < 4 or len(observed) < 4:
        return empty
    factor = 2
    a = _blob_image(expected, cam_size, factor)
    b = _blob_image(observed, cam_size, factor)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), _resp = cv2.phaseCorrelate(a, b, window)
    shift = np.array([dx, dy]) * factor
    ie, io = _nearest(expected + shift, observed, 0.4 * spacing_cam)
    if len(ie) < 4:
        return empty
    h: np.ndarray | None = None
    src, dst = expected[ie], observed[io]
    for _ in range(2):
        if len(src) >= 8:
            hh, inl = cv2.findHomography(src, dst, cv2.RANSAC, 2.0)
        else:
            hh, inl = None, None
        if hh is None:
            t = np.median(dst - src, axis=0)
            hh = np.array([[1, 0, t[0]], [0, 1, t[1]], [0, 0, 1]], dtype=np.float64)
        h = np.asarray(hh, dtype=np.float64)
        pred = apply_h(h, expected)
        ie, io = _nearest(pred, observed, max(3.0, 0.15 * spacing_cam))
        src, dst = expected[ie], observed[io]
        if len(src) < 4:
            return empty
        if inl is not None and len(src) >= 8:
            refit, _ = cv2.findHomography(src, dst, 0)
            if refit is not None:
                h = np.asarray(refit, dtype=np.float64)
    return DotMatch(idx_expected=ie, observed=dst, h=h)


# --- wall texture ------------------------------------------------------------------------


def texture(image: np.ndarray, lit: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """High-pass of the log brightness of a white picture (scale-free: exposure and projector
    level cancel) and where it is trusted: bright enough to carry texture, and away from the
    edge of the projected light, which moves with the projector, not with the camera."""
    img = image.astype(np.float32)
    lg = np.log(np.maximum(img, 1.0))
    hp = np.clip(lg - cv2.GaussianBlur(lg, (0, 0), TEX_SIGMA), -TEX_CLIP, TEX_CLIP)
    k = np.ones((2 * TEX_ERODE_PX + 1, 2 * TEX_ERODE_PX + 1), np.uint8)
    lit8 = lit.astype(np.uint8)
    border = (cv2.dilate(lit8, k) - cv2.erode(lit8, k)) > 0
    signal = cv2.erode((img > TEX_MIN_LEVEL).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    return np.asarray(hp, dtype=np.float32), np.asarray(signal & ~border)


def _to_u8(hp: np.ndarray, mask: np.ndarray) -> np.ndarray:
    sd = float(np.std(hp[mask])) if mask.any() else 1.0
    return np.asarray(np.clip(128 + 40 * hp / max(sd, 1e-6), 0, 255).astype(np.uint8))


@dataclass
class CameraMotion:
    h: np.ndarray  # reference camera px -> current camera px
    inliers: int
    method: str  # "features" | "ecc" | "phase" | "dots": how the search was started
    score: float  # ECC correlation after refinement (0..1)


def camera_motion(
    ref_hp: np.ndarray,
    ref_mask: np.ndarray,
    cur_hp: np.ndarray,
    cur_mask: np.ndarray,
    *,
    extra_start: np.ndarray | None = None,
) -> CameraMotion | None:
    """How the wall moved in the camera picture since the scan; None when the wall has too
    little texture to tell."""
    # Compare only where both pictures carry texture (a camera move of a few percent of the
    # picture keeps nearly all of it).
    common = ref_mask & cur_mask
    if int(common.sum()) < 5000:
        return None
    ref_mask = cur_mask = common
    a, b = _to_u8(ref_hp, ref_mask), _to_u8(cur_hp, cur_mask)
    akaze = cv2.AKAZE.create(threshold=0.0005)
    kp_a, des_a = akaze.detectAndCompute(a, ref_mask.astype(np.uint8) * 255)
    kp_b, des_b = akaze.detectAndCompute(b, cur_mask.astype(np.uint8) * 255)
    h0: np.ndarray | None = None
    inliers = 0
    if des_a is not None and des_b is not None and len(kp_a) >= 8 and len(kp_b) >= 8:
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des_a, des_b, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.8 * p[1].distance]
        if len(good) >= MIN_TEXTURE_INLIERS:
            src = np.array([kp_a[m.queryIdx].pt for m in good], dtype=np.float64)
            dst = np.array([kp_b[m.trainIdx].pt for m in good], dtype=np.float64)
            hh, inl = cv2.findHomography(src, dst, cv2.RANSAC, 2.0)
            if (
                hh is not None
                and inl is not None
                and int(inl.sum()) >= MIN_TEXTURE_INLIERS
                and plausible(np.asarray(hh, dtype=np.float64))
            ):
                h0 = np.asarray(hh, dtype=np.float64)
                inliers = int(inl.sum())
    # Start the sub-pixel search from the feature match, from "nothing moved" (the usual
    # case) and from the best overall shift; keep the best-correlated answer.
    starts: list[tuple[str, np.ndarray]] = [("ecc", np.eye(3))]
    if h0 is not None:
        starts.insert(0, ("features", h0))
    else:
        both = common.astype(np.float32)
        win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
        (dx, dy), resp = cv2.phaseCorrelate(ref_hp * both, cur_hp * both, win)
        if resp >= 0.02 and np.hypot(dx, dy) > 1.0:
            starts.append(("phase", np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], dtype=float)))
    if extra_start is not None and plausible(extra_start):
        # given in pixel-corner coordinates; ECC works in pixel-index coordinates
        starts.append(("dots", _HALF_OUT @ extra_start @ _HALF_OUT_INV))
    best: CameraMotion | None = None
    for name, start in starts:
        found = _refine(ref_hp, cur_hp, ref_mask, start, inliers, name)
        if found is not None and (best is None or found.score > best.score):
            best = found
    return best


def _refine(
    ref_hp: np.ndarray,
    cur_hp: np.ndarray,
    mask: np.ndarray,
    h0: np.ndarray,
    inliers: int,
    method: str,
) -> CameraMotion | None:
    # Sub-pixel refinement. ECC (enhanced correlation coefficient) is invariant to brightness
    # and contrast. findTransformECC(template, input) finds W with input(W x) ~ template(x);
    # with template = current and input = reference, W maps current -> reference.
    # An affine model (6 numbers): a bumped camera turns and shifts a little, and the texture
    # often covers only part of the picture, where a full homography's perspective terms are
    # poorly pinned down and extrapolate badly to the rest of the wall.
    inv = np.linalg.inv(h0)
    inv = inv / inv[2, 2]
    warp = inv[:2].astype(np.float32)
    try:
        score, warp = cv2.findTransformECC(
            cur_hp,
            ref_hp,
            warp,
            cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-6),
            mask.astype(np.uint8) * 255,
            3,
        )
    except cv2.error:
        return None  # no reliable sub-pixel answer: say "unknown" rather than guess
    if float(score) < MIN_ECC_SCORE:
        return None
    h_index = np.linalg.inv(np.vstack([np.asarray(warp, dtype=np.float64), [0.0, 0.0, 1.0]]))
    # OpenCV works in pixel-index coordinates (integer = pixel centre); this module uses
    # pixel-corner coordinates.
    h = _HALF_OUT_INV @ h_index @ _HALF_OUT
    h = h / h[2, 2]
    if not plausible(h):
        return None
    return CameraMotion(h=h, inliers=inliers, method=method, score=float(score))


_HALF_OUT = np.array([[1.0, 0.0, -0.5], [0.0, 1.0, -0.5], [0.0, 0.0, 1.0]])
_HALF_OUT_INV = np.array([[1.0, 0.0, 0.5], [0.0, 1.0, 0.5], [0.0, 0.0, 1.0]])


def plausible(h: np.ndarray) -> bool:
    """A camera that was nudged, not replaced: finite, orientation kept, scale within 2x."""
    if not np.all(np.isfinite(h)) or abs(h[2, 2]) < 1e-12:
        return False
    a = h[:2, :2] / h[2, 2]
    det = float(np.linalg.det(a))
    return 0.5 < det < 2.0 and float(np.max(np.abs(h[2, :2] / h[2, 2]))) < 1e-2


def fit_h(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    """One homography through all pairs (RANSAC at 1.5 px, then least squares on the inliers).
    Averaging over every dot removes most of the per-dot noise."""
    if len(src) < 8:
        return None
    h, inl = cv2.findHomography(src, dst, cv2.RANSAC, 1.5)
    if h is None or inl is None or int(inl.sum()) < 8:
        return None
    sel = inl.ravel().astype(bool)
    refit, _ = cv2.findHomography(src[sel], dst[sel], 0)
    return np.asarray(refit, dtype=np.float64) if refit is not None else None


def shift_of(h: np.ndarray | None, src: np.ndarray, dst: np.ndarray, scale: np.ndarray) -> float:
    """Median displacement of the points under `h`, divided by `scale` (camera px -> projector
    px). Without a fit, the median of the single displacements (biased upwards by noise)."""
    moved = apply_h(h, src) if h is not None else dst
    return float(np.median(np.linalg.norm(moved - src, axis=1) / scale))


def mean_shift(h: np.ndarray, mask: np.ndarray, step: int = 16) -> float:
    """Median displacement |h(x) - x| over a grid of points in `mask`."""
    ys, xs = np.nonzero(mask[::step, ::step])
    if len(xs) == 0:
        return 0.0
    pts = np.stack([xs * step + 0.5, ys * step + 0.5], axis=1).astype(np.float64)
    return float(np.median(np.linalg.norm(apply_h(h, pts) - pts, axis=1)))


def splat_to_camera(
    image: np.ndarray, pmap: ProjCamMap, cam_size: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Resample a projector-space gray picture into the camera picture (nearest splat, then
    small holes filled). Used for scans made before the camera's own pictures were stored."""
    w, h = cam_size
    hp, wp = pmap.measured.shape
    cam = pmap.p2c.reshape(-1, 2)
    vals = image.reshape(-1).astype(np.float64)
    sel = pmap.measured.reshape(-1)
    ix = np.floor(cam[:, 0]).astype(np.int64)
    iy = np.floor(cam[:, 1]).astype(np.int64)
    ok = sel & (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    flat = iy[ok] * w + ix[ok]
    acc = np.bincount(flat, weights=vals[ok], minlength=w * h)
    cnt = np.bincount(flat, minlength=w * h).astype(np.float64)
    out = (acc / np.maximum(cnt, 1)).reshape(h, w).astype(np.float32)
    have = (cnt > 0).reshape(h, w).astype(np.float32)
    blur_v = cv2.GaussianBlur(out * have, (0, 0), 1.0)
    blur_w = cv2.GaussianBlur(have, (0, 0), 1.0)
    filled = np.where(have > 0, out, blur_v / np.maximum(blur_w, 1e-6))
    covered = blur_w > 0.3
    del hp, wp
    return np.asarray(filled, dtype=np.float32), np.asarray(covered)
