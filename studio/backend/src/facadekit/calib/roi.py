"""Camera crop helpers: mapping crop-coordinate results back to the full frame, and suggesting
a crop from the lit blob. See docs/08 section 2. Offsets and the bin factor are integers, so no
half-pixel term appears anywhere in this module."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from facadekit.calib.quality import LitBlob
from facadekit.models.calibration import CameraRoi

if TYPE_CHECKING:  # maps imports decode, which imports capture, which imports this module
    from facadekit.calib.maps import ProjectorMaps

MIN_CROP = 64
BORDER_PX = 2
BORDER_FRACTION = 0.02


def roi_affine(roi: CameraRoi) -> np.ndarray:
    """Crop pixel-corner coordinates -> full-frame pixel-corner coordinates."""
    return np.array(
        [[roi.bin, 0.0, roi.x], [0.0, roi.bin, roi.y], [0.0, 0.0, 1.0]], dtype=np.float64
    )


def maps_to_full(maps: ProjectorMaps, roi: CameraRoi) -> ProjectorMaps:
    from facadekit.calib.maps import ProjectorMaps

    p2c = (np.float32(roi.bin) * maps.p2c + np.array([roi.x, roi.y], dtype=np.float32)).astype(
        np.float32
    )
    h_c2p = None if maps.h_c2p is None else maps.h_c2p @ np.linalg.inv(roi_affine(roi))
    return ProjectorMaps(
        p2c=p2c,
        measured=maps.measured,
        h_c2p=h_c2p,
        coverage=maps.coverage,
        planarity=maps.planarity,
    )


def _grow_to_min(lo: int, hi: int, limit: int) -> tuple[int, int]:
    """Grow [lo, hi) about its centre to MIN_CROP, then shift it back inside [0, limit)."""
    size = hi - lo
    if size < MIN_CROP:
        lo -= (MIN_CROP - size) // 2
        hi = lo + MIN_CROP
    if lo < 0:
        lo, hi = 0, hi - lo
    if hi > limit:
        lo, hi = lo - (hi - limit), limit
    return max(lo, 0), hi


def suggest_roi(
    blob: LitBlob, frame_w: int, frame_h: int, *, max_area_fraction: float = 0.6
) -> CameraRoi | None:
    bx0, by0, bx1, by1 = blob.box
    margin = max(16, math.ceil(0.04 * max(bx1 - bx0, by1 - by0)))
    x0 = max(0, (bx0 - margin) // 2 * 2)  # floor to even
    y0 = max(0, (by0 - margin) // 2 * 2)
    x1 = min(frame_w, -(-(bx1 + margin) // 2) * 2)  # ceil to even
    y1 = min(frame_h, -(-(by1 + margin) // 2) * 2)
    x0, x1 = _grow_to_min(x0, x1, frame_w)
    y0, y1 = _grow_to_min(y0, y1, frame_h)
    if (x1 - x0) * (y1 - y0) > max_area_fraction * frame_w * frame_h:
        return None
    return CameraRoi(x=x0, y=y0, w=x1 - x0, h=y1 - y0, frame_width=frame_w, frame_height=frame_h)


def crop_too_tight(blob_mask: np.ndarray) -> bool:
    """True if more than 2 % of the blob's pixels lie within 2 px of the crop border."""
    total = int(blob_mask.sum())
    if total == 0:
        return False
    inner = np.zeros_like(blob_mask, dtype=bool)
    inner[BORDER_PX:-BORDER_PX, BORDER_PX:-BORDER_PX] = True
    near = int((blob_mask & ~inner).sum())
    return near > BORDER_FRACTION * total
