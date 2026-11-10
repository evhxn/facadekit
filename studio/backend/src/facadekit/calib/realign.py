from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from facadekit.errors import REALIGN_FAILED, FkError
from facadekit.geom.homography import apply_h, quad_homography
from facadekit.models.project import Mask, Mesh, Point, Project, Surface

MIN_INLIERS = 30
MAX_REPROJ_ERROR_PX = 4.0
RATIO = 0.75
MASK_ERODE_PX = 15
MAX_AREA_CHANGE = 2.0
_TOO_DIFFERENT = "The two scans look too different to line up automatically."


@dataclass
class RealignResult:
    h_old2new: np.ndarray  # (3, 3) float64, old projector px -> new projector px
    inliers: int
    reproj_error_px: float  # median reprojection error of inliers


def _prepare(view: np.ndarray, mask: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None]:
    gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY) if view.ndim == 3 else view
    gray = cv2.createCLAHE(2.0, (8, 8)).apply(gray)
    det: np.ndarray | None = None
    if mask is not None:
        kernel = np.ones((2 * MASK_ERODE_PX + 1, 2 * MASK_ERODE_PX + 1), np.uint8)
        det = cv2.erode((mask > 0).astype(np.uint8) * 255, kernel)
    return gray, det


def _polygon_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return float(0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _is_plausible(h: np.ndarray, width: int, height: int) -> bool:
    frame = np.array([(0, 0), (width, 0), (width, height), (0, height)], dtype=np.float64)
    if not np.all(np.isfinite(h)):
        return False
    # A point at infinity on the way means the frame is folded over the horizon.
    w = h[2, 0] * frame[:, 0] + h[2, 1] * frame[:, 1] + h[2, 2]
    if np.any(w <= 1e-9):
        return False
    out = apply_h(h, frame)
    area = _polygon_area(out)
    ratio = area / (width * height)
    if ratio < 1.0 / MAX_AREA_CHANGE or ratio > MAX_AREA_CHANGE:
        return False
    crosses = []
    for i in range(4):
        a, b, c = out[i], out[(i + 1) % 4], out[(i + 2) % 4]
        crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    return all(v > 0 for v in crosses)


def estimate_drift(
    old_view: np.ndarray,
    new_view: np.ndarray,
    *,
    old_mask: np.ndarray | None = None,
    new_mask: np.ndarray | None = None,
) -> RealignResult:
    old_gray, old_det = _prepare(old_view, old_mask)
    new_gray, new_det = _prepare(new_view, new_mask)
    akaze = cv2.AKAZE.create()
    kp_old, des_old = akaze.detectAndCompute(old_gray, old_det)
    kp_new, des_new = akaze.detectAndCompute(new_gray, new_det)
    if des_old is None or des_new is None or len(kp_old) < 4 or len(kp_new) < 4:
        raise FkError(REALIGN_FAILED, _TOO_DIFFERENT)
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des_old, des_new, k=2)
    good = [p[0] for p in matches if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
    if len(good) < MIN_INLIERS:
        raise FkError(REALIGN_FAILED, _TOO_DIFFERENT)
    src = np.array([kp_old[m.queryIdx].pt for m in good], dtype=np.float64)
    dst = np.array([kp_new[m.trainIdx].pt for m in good], dtype=np.float64)
    h, inlier_mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if h is None or inlier_mask is None:
        raise FkError(REALIGN_FAILED, _TOO_DIFFERENT)
    sel = inlier_mask.ravel().astype(bool)
    inliers = int(sel.sum())
    if inliers < MIN_INLIERS:
        raise FkError(REALIGN_FAILED, _TOO_DIFFERENT)
    h = np.asarray(h, dtype=np.float64)
    err = float(np.median(np.linalg.norm(apply_h(h, src[sel]) - dst[sel], axis=1)))
    height, width = old_gray.shape[:2]
    if err > MAX_REPROJ_ERROR_PX or not _is_plausible(h, width, height):
        raise FkError(REALIGN_FAILED, _TOO_DIFFERENT)
    return RealignResult(h_old2new=h, inliers=inliers, reproj_error_px=err)


def _map_points(h: np.ndarray, pts: list[Point]) -> list[Point]:
    out = apply_h(h, np.asarray(pts, dtype=np.float64).reshape(-1, 2))
    return [(float(x), float(y)) for x, y in out]


def _map_mesh(h: np.ndarray, surface: Surface, mesh: Mesh, new_quad: list[Point]) -> Mesh:
    u = np.tile(np.arange(mesh.cols + 1) / mesh.cols, mesh.rows + 1)
    v = np.repeat(np.arange(mesh.rows + 1) / mesh.rows, mesh.cols + 1)
    unit = np.stack([u, v], axis=1)
    old_base = apply_h(quad_homography(1, 1, surface.quad), unit)
    final = old_base + np.asarray(mesh.offsets, dtype=np.float64).reshape(-1, 2)
    new_final = apply_h(h, final)
    new_base = apply_h(quad_homography(1, 1, new_quad), unit)
    offsets = new_final - new_base
    return Mesh(
        cols=mesh.cols,
        rows=mesh.rows,
        offsets=[(float(x), float(y)) for x, y in offsets],
    )


def apply_drift(project: Project, h_old2new: np.ndarray) -> Project:
    result = project.model_copy(deep=True)
    surfaces: list[Surface] = []
    for surface in result.surfaces:
        quad = _map_points(h_old2new, list(surface.quad))
        update: dict[str, object] = {"quad": (quad[0], quad[1], quad[2], quad[3])}
        if surface.mask is not None:
            update["mask"] = Mask(
                outer=_map_points(h_old2new, surface.mask.outer),
                holes=[_map_points(h_old2new, hole) for hole in surface.mask.holes],
            )
        if surface.mesh is not None:
            update["mesh"] = _map_mesh(h_old2new, surface, surface.mesh, quad)
        surfaces.append(surface.model_copy(update=update))
    result.surfaces = surfaces
    return result
