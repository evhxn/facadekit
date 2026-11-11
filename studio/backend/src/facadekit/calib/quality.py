"""Camera quality measurements: how much detail does the camera see of the projection?

The governing number is `s`, camera pixels per projector pixel (linear). The finest Gray-code
stripe projected at `skip_lsb` is 2**(skip_lsb + 1) projector px wide and must span at least
STRIPE_MIN_CAM_PX camera pixels. See docs/08 section 3.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from facadekit.calib.patterns import PatternFrame, PatternSpec, render_pattern
from facadekit.devices.base import Camera, ProjectorDisplay
from facadekit.models.calibration import CalibSettings

STRIPE_MIN_CAM_PX = 3.0  # finest stripe must span this many camera px
MOD_OK = 0.30  # stripe modulation needed to call a bit readable (tuned: docs/08 section 6)
SNR_LOW = 8.0
MIN_LIT_PX = 4000
CLIP_LEVEL = 250.0

Verdict = Literal["good", "marginal", "too_coarse"]
Reason = Literal["SMALL_IN_FRAME", "BLURRY", "NOISY", "CLIPPED", "NO_EXPOSURE_CONTROL"]


@dataclass
class LitBlob:
    mask: np.ndarray  # bool (Hc, Wc): largest component after 3x3 opening
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 (pixel-corner, x1/y1 exclusive)
    area_px: int  # pixels in the blob
    hull_area_px: float  # convex hull area of the blob
    total_lit_px: int  # all lit pixels before taking the largest component


def lit_threshold(contrast: np.ndarray) -> float:
    """The one lit rule, shared by precheck, recordings and decode."""
    return max(12.0, 0.15 * float(np.percentile(contrast, 95)))


def lit_blob(contrast: np.ndarray) -> LitBlob | None:
    lit = contrast > lit_threshold(contrast)
    total = int(lit.sum())
    if total == 0:
        return None
    opened = cv2.morphologyEx(lit.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    if n < 2:
        return None  # only isolated noisy pixels
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = labels == best
    x0 = int(stats[best, cv2.CC_STAT_LEFT])
    y0 = int(stats[best, cv2.CC_STAT_TOP])
    x1 = x0 + int(stats[best, cv2.CC_STAT_WIDTH])
    y1 = y0 + int(stats[best, cv2.CC_STAT_HEIGHT])
    contours, _h = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pts = np.concatenate(contours, axis=0)
    hull = cv2.convexHull(pts)
    return LitBlob(
        mask=mask,
        box=(x0, y0, x1, y1),
        area_px=int(stats[best, cv2.CC_STAT_AREA]),
        hull_area_px=float(cv2.contourArea(hull)),
        total_lit_px=total,
    )


def projection_found(blob: LitBlob | None) -> bool:
    return (
        blob is not None and blob.area_px >= MIN_LIT_PX and blob.area_px >= 0.5 * blob.total_lit_px
    )


def px_per_proj_px(hull_area_px: float, proj_w: int, proj_h: int) -> float:
    return math.sqrt(max(hull_area_px, 0.0) / (proj_w * proj_h))


def px_per_proj_px_from_h(h_c2p: np.ndarray, proj_w: int, proj_h: int) -> float:
    """sqrt(|det J|) of the projector -> camera map (inverse of h_c2p) at the projector centre."""
    h = np.linalg.inv(np.asarray(h_c2p, dtype=np.float64))
    step = 0.5

    def at(x: float, y: float) -> np.ndarray:
        v = h @ np.array([x, y, 1.0])
        return np.asarray(v[:2] / v[2])

    cx, cy = proj_w / 2, proj_h / 2
    dx = (at(cx + step, cy) - at(cx - step, cy)) / (2 * step)
    dy = (at(cx, cy + step) - at(cx, cy - step)) / (2 * step)
    return float(math.sqrt(abs(dx[0] * dy[1] - dx[1] * dy[0])))


def finest_readable_skip(s: float) -> int:
    """Smallest skip_lsb whose finest stripe spans STRIPE_MIN_CAM_PX camera pixels."""
    if s <= 0:
        return 6
    raw = math.ceil(math.log2(STRIPE_MIN_CAM_PX / s) - 1e-9) - 1
    return int(min(max(raw, 0), 6))


def stripe_modulation(
    pattern: np.ndarray, inverse: np.ndarray, contrast: np.ndarray, lit: np.ndarray
) -> float:
    """Median over lit pixels of |pattern - inverse| / max(contrast, 1); 1.0 is perfectly sharp."""
    if not lit.any():
        return 0.0
    d = np.abs(pattern - inverse) / np.maximum(contrast, 1.0)
    return float(np.median(d[lit]))


def noise_sigma(frame_a: np.ndarray, frame_b: np.ndarray, lit: np.ndarray) -> float:
    """Per-frame noise from two single frames of the same static scene."""
    if not lit.any():
        return 0.0
    return float(np.std((frame_a - frame_b)[lit]) / math.sqrt(2.0))


def probe_stripes(
    display: ProjectorDisplay,
    camera: Camera,
    spec: PatternSpec,
    settings: CalibSettings,
    *,
    bits: tuple[int, ...] = (2, 3, 4),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[int, float]:
    """Project the finest x-axis stripes and measure how much of them survives."""
    from facadekit.calib.capture import _shoot  # capture imports this module

    black, _ = _shoot(
        display, camera, np.zeros((spec.height, spec.width), np.uint8), settings, sleep
    )
    white, _ = _shoot(
        display, camera, np.full((spec.height, spec.width), 255, np.uint8), settings, sleep
    )
    contrast = white - black
    blob = lit_blob(contrast)
    lit = blob.mask if blob is not None else contrast > lit_threshold(contrast)
    out: dict[int, float] = {}
    for bit in bits:
        pos, _ = _shoot(
            display,
            camera,
            render_pattern(spec, PatternFrame("pattern", "x", bit, False)),
            settings,
            sleep,
        )
        neg, _ = _shoot(
            display,
            camera,
            render_pattern(spec, PatternFrame("pattern", "x", bit, True)),
            settings,
            sleep,
        )
        out[bit] = stripe_modulation(pos, neg, contrast, lit)
    return out


def verdict(
    s: float,
    snr: float,
    *,
    modulation: dict[int, float] | None = None,
    p99_white: float | None = None,
    controls: dict[str, bool] | None = None,
) -> tuple[Verdict, list[Reason]]:
    skip_scale = finest_readable_skip(s)
    skip_blur: int | None = None
    if modulation is not None:
        skip_blur = min((b for b in sorted(modulation) if modulation[b] >= MOD_OK), default=5)
    need = max(skip_scale, skip_blur or 0)
    result: Verdict = "good" if need <= 2 else "marginal" if need == 3 else "too_coarse"
    reasons: list[Reason] = []
    if skip_scale >= 3:
        reasons.append("SMALL_IN_FRAME")
    if skip_blur is not None and skip_blur >= 3:
        reasons.append("BLURRY")
    if snr < SNR_LOW:
        reasons.append("NOISY")
        if result == "good":
            result = "marginal"
    if p99_white is not None and p99_white >= CLIP_LEVEL:
        reasons.append("CLIPPED")
    if controls and controls.get("auto_exposure") is False:
        reasons.append("NO_EXPOSURE_CONTROL")
    return result, reasons
