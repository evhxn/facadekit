"""Homographies, aspect estimation and fit rules for quad surfaces."""

from __future__ import annotations

import math
from collections.abc import Sequence

import cv2
import numpy as np

from facadekit.errors import FkError
from facadekit.models.project import ProjectorSpec, Surface

Point = tuple[float, float]
Rect = tuple[float, float, float, float]


def quad_homography(src_w: float, src_h: float, quad: Sequence[Point]) -> np.ndarray:
    """3x3 float64 mapping the source rectangle corners onto quad[0..3]."""
    if len(quad) != 4:
        raise FkError("BAD_REQUEST", "quad must have exactly 4 points")
    for i in range(4):
        a, b, c = quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) <= 1e-6:
            raise FkError("BAD_REQUEST", "quad is degenerate")
    src = np.array([(0, 0), (src_w, 0), (src_w, src_h), (0, src_h)], dtype=np.float32)
    dst = np.array(quad, dtype=np.float32)
    return np.asarray(cv2.getPerspectiveTransform(src, dst), dtype=np.float64)


def apply_h(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply homography to (N, 2) points with perspective divide."""
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    homog = np.hstack([p, np.ones((p.shape[0], 1))])
    out = homog @ np.asarray(h, dtype=np.float64).T
    return np.asarray(out[:, :2] / out[:, 2:3])


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def estimate_aspect(quad: Sequence[Point]) -> float:
    tl, tr, br, bl = quad
    return (_dist(tl, tr) + _dist(bl, br)) / (_dist(tl, bl) + _dist(tr, br))


def fit_rects(src_w: float, src_h: float, surface_aspect: float, fit: str) -> tuple[Rect, Rect]:
    """Return (src_rect, dst_unit_rect) for the given fit mode."""
    sa = src_w / src_h
    ta = surface_aspect
    unit: Rect = (0.0, 0.0, 1.0, 1.0)
    if fit == "stretch":
        return (0.0, 0.0, src_w, src_h), unit
    if fit == "fill":
        if sa > ta:
            w = src_h * ta
            x0 = (src_w - w) / 2
            return (x0, 0.0, x0 + w, src_h), unit
        h = src_w / ta
        y0 = (src_h - h) / 2
        return (0.0, y0, src_w, y0 + h), unit
    if fit == "fit":
        whole: Rect = (0.0, 0.0, src_w, src_h)
        if sa > ta:
            dh = ta / sa
            y0 = (1 - dh) / 2
            return whole, (0.0, y0, 1.0, y0 + dh)
        dw = sa / ta
        x0 = (1 - dw) / 2
        return whole, (x0, 0.0, x0 + dw, 1.0)
    raise FkError("BAD_REQUEST", f"unknown fit mode: {fit}")


def clip_homography(
    src_w: float, src_h: float, surface: Surface, fit: str
) -> tuple[np.ndarray, Rect]:
    """H mapping source pixels to projector pixels, plus the used src_rect."""
    src_rect, dst = fit_rects(src_w, src_h, surface.aspect, fit)
    x0, y0, x1, y1 = src_rect
    u0, v0, u1, v1 = dst
    sx = (u1 - u0) / (x1 - x0)
    sy = (v1 - v0) / (y1 - y0)
    m = np.array(
        [[sx, 0.0, u0 - x0 * sx], [0.0, sy, v0 - y0 * sy], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return quad_homography(1, 1, surface.quad) @ m, src_rect


def canvas_surface(projector: ProjectorSpec) -> Surface:
    w, h = projector.width, projector.height
    return Surface(
        id="canvas",
        name="Canvas",
        quad=((0, 0), (w, 0), (w, h), (0, h)),
        aspect=w / h,
    )
