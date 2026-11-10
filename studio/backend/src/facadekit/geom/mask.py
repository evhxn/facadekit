from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from facadekit.errors import FkError

Point = tuple[float, float]

# OpenCV fixed-point shift: coordinates are passed in 1/16 pixel units for sub-pixel edges.
_SHIFT = 4
_SCALE = float(1 << _SHIFT)


def _fill_points(points: Sequence[Point]) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return np.round(pts * _SCALE).astype(np.int32)


def rasterize_mask(
    width: int,
    height: int,
    outer: Sequence[Point],
    holes: Sequence[Sequence[Point]] = (),
    feather_px: float = 0.0,
) -> np.ndarray:
    """Rasterise a polygon with optional holes into a soft float32 mask in 0..1.

    Points are projector pixels (x, y). Points may lie outside the image.
    Returns an array shaped (height, width).
    """
    if len(outer) < 3:
        raise FkError("BAD_REQUEST", f"mask outline needs at least 3 points, got {len(outer)}")

    canvas = np.zeros((height, width), np.uint8)
    cv2.fillPoly(canvas, [_fill_points(outer)], 255, lineType=cv2.LINE_AA, shift=_SHIFT)
    for hole in holes:
        cv2.fillPoly(canvas, [_fill_points(hole)], 0, lineType=cv2.LINE_AA, shift=_SHIFT)

    out: np.ndarray = canvas.astype(np.float32) / np.float32(255.0)
    if feather_px > 0:
        out = np.asarray(cv2.GaussianBlur(out, (0, 0), sigmaX=feather_px / 2), dtype=np.float32)
    return out
