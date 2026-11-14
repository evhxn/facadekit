"""Why a projector pixel was not measured, in words a user can act on.

The scan result used to paint every unmeasured area the same red. The camera pictures
already say more: whether the spot was outside the picture, got no light the camera could
see, was washed out, or was lit but its stripes could not be read.
"""

from __future__ import annotations

import cv2
import numpy as np

from facadekit.calib.quality import lit_threshold

MEASURED = 0
DARK = 1  # the camera saw (almost) no projector light there
BRIGHT = 2  # the camera picture is washed out there
OUTSIDE = 3  # the spot is not in the camera picture
UNCLEAR = 4  # lit, but the stripes could not be read (steep angle, blur, movement)

NAMES = {DARK: "dark", BRIGHT: "bright", OUTSIDE: "outside", UNCLEAR: "unclear"}
PNG_STEP = 50  # reasons.png stores code * 50, so the picture is readable by eye too
SATURATED = 250.0


def unmeasured_reasons(
    p2c: np.ndarray, measured: np.ndarray, contrast: np.ndarray, white: np.ndarray
) -> np.ndarray:
    """uint8 (H, W) of reason codes for every projector pixel.

    `p2c` gives each projector pixel's place in the camera picture (extrapolated where
    nothing was measured); `contrast` (white minus black) and `white` are camera pictures.
    """
    cam_h, cam_w = contrast.shape
    x = p2c[..., 0].astype(np.float32) - np.float32(0.5)  # pixel-corner -> pixel-index
    y = p2c[..., 1].astype(np.float32) - np.float32(0.5)
    inside = np.isfinite(x) & np.isfinite(y) & (x >= -0.5) & (y >= -0.5)
    inside &= (x <= cam_w - 0.5) & (y <= cam_h - 0.5)
    safe_x = np.ascontiguousarray(np.where(inside, x, 0.0).astype(np.float32))
    safe_y = np.ascontiguousarray(np.where(inside, y, 0.0).astype(np.float32))
    # A little blur: one noisy camera pixel should not decide the colour of a region.
    smooth_contrast = cv2.blur(contrast.astype(np.float32), (5, 5))
    smooth_white = cv2.blur(white.astype(np.float32), (5, 5))
    at_contrast = cv2.remap(smooth_contrast, safe_x, safe_y, cv2.INTER_LINEAR)
    at_white = cv2.remap(smooth_white, safe_x, safe_y, cv2.INTER_LINEAR)

    out = np.full(measured.shape, UNCLEAR, np.uint8)
    out[at_contrast <= lit_threshold(contrast)] = DARK
    out[at_white >= SATURATED] = BRIGHT
    out[~inside] = OUTSIDE
    out[measured] = MEASURED
    return out


def reason_fractions(reasons: np.ndarray) -> dict[str, float]:
    """Share of all projector pixels per reason, e.g. {"dark": 0.12, ...}; zeros included."""
    total = float(reasons.size)
    return {name: float((reasons == code).sum()) / total for code, name in NAMES.items()}
