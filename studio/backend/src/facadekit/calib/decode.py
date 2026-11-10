from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from facadekit.calib.capture import CaptureSet
from facadekit.calib.patterns import bits_for, gray_to_binary
from facadekit.errors import FkError


@dataclass
class DecodeResult:
    c2p: np.ndarray  # float32 (Hc, Wc, 2); NaN where invalid; [...,0]=proj x, [...,1]=proj y
    valid: np.ndarray  # bool (Hc, Wc)
    contrast: np.ndarray  # float32 (Hc, Wc) = white - black
    lit_fraction: float
    median_contrast: float


def decode(capture: CaptureSet, *, min_conf: float = 0.10, outlier_px: float = 8.0) -> DecodeResult:
    spec = capture.spec
    index: dict[tuple[str, str | None, int | None, bool], int] = {
        (f.kind, f.axis, f.bit, f.inverse): i for i, f in enumerate(capture.frames)
    }
    black_i = index.get(("black", None, None, False))
    white_i = index.get(("white", None, None, False))
    if black_i is None or white_i is None:
        raise FkError("SCAN_FAILED", "Capture set is incomplete")

    images = capture.images
    contrast = (images[white_i] - images[black_i]).astype(np.float32)
    lit = contrast > max(12.0, 0.15 * float(np.percentile(contrast, 95)))
    denom = np.maximum(contrast, 1.0)

    coords: dict[str, np.ndarray] = {}
    confs: dict[str, np.ndarray] = {}
    for axis in ("x", "y"):
        size = spec.width if axis == "x" else spec.height
        bits = bits_for(size)
        value_gray = np.zeros(contrast.shape, dtype=np.int32)
        conf = np.full(contrast.shape, np.inf, dtype=np.float32)
        for b in range(bits - 1, spec.skip_lsb - 1, -1):
            pos = index.get(("pattern", axis, b, False))
            neg = index.get(("pattern", axis, b, True))
            if pos is None or neg is None:
                raise FkError("SCAN_FAILED", "Capture set is incomplete")
            d = images[pos] - images[neg]
            conf = np.minimum(conf, np.abs(d) / denom)
            value_gray = (value_gray << 1) | (d > 0).astype(np.int32)
        value = gray_to_binary(value_gray)
        coords[axis] = (value << spec.skip_lsb).astype(np.float32) + np.float32(
            (1 << spec.skip_lsb) / 2.0
        )
        confs[axis] = conf

    cx, cy = coords["x"], coords["y"]
    valid = (
        lit
        & (confs["x"] > min_conf)
        & (confs["y"] > min_conf)
        & (cx < spec.width)
        & (cy < spec.height)
    )

    base = valid.copy()
    keep = np.ones_like(valid)
    for coord in (cx, cy):
        tmp = coord.astype(np.float32).copy()
        tmp[~base] = -1e6
        med = cv2.medianBlur(tmp, 5)
        keep &= (med >= -1e5) & (np.abs(coord - med) <= outlier_px)
    valid &= keep

    c2p = np.stack([cx, cy], axis=-1).astype(np.float32)
    c2p[~valid] = np.nan
    lit_fraction = float(lit.mean())
    median_contrast = float(np.median(contrast[lit])) if lit.any() else 0.0
    return DecodeResult(c2p, valid, contrast, lit_fraction, median_contrast)
