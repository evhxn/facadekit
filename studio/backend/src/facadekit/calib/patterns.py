from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


@dataclass(frozen=True)
class PatternSpec:
    width: int
    height: int
    skip_lsb: int = 2


@dataclass(frozen=True)
class PatternFrame:
    kind: Literal["black", "white", "pattern"]
    axis: Literal["x", "y"] | None = None
    bit: int | None = None  # bit index in the Gray code, 0 = least significant
    inverse: bool = False


def bits_for(n: int) -> int:
    """Number of Gray-code bits needed to encode indices 0..n-1."""
    return max(1, (n - 1).bit_length())


def _validate(spec: PatternSpec) -> None:
    limit = min(bits_for(spec.width), bits_for(spec.height))
    if not 0 <= spec.skip_lsb < limit:
        raise ValueError(f"skip_lsb must satisfy 0 <= skip_lsb < {limit}, got {spec.skip_lsb}")


def pattern_sequence(spec: PatternSpec) -> list[PatternFrame]:
    """Frames to project, in order: black, white, then x bits and y bits (MSB first)."""
    _validate(spec)
    frames: list[PatternFrame] = [PatternFrame("black"), PatternFrame("white")]
    axes: list[Literal["x", "y"]] = ["x", "y"]
    for axis in axes:
        size = spec.width if axis == "x" else spec.height
        bits = bits_for(size)
        for bit in range(bits - 1, spec.skip_lsb - 1, -1):
            frames.append(PatternFrame("pattern", axis, bit, False))
            frames.append(PatternFrame("pattern", axis, bit, True))
    return frames


def render_pattern(spec: PatternSpec, frame: PatternFrame) -> np.ndarray:
    """Render one frame as a contiguous uint8 array of shape (height, width), values 0 or 255."""
    _validate(spec)
    if frame.kind == "black":
        return np.zeros((spec.height, spec.width), dtype=np.uint8)
    if frame.kind == "white":
        return np.full((spec.height, spec.width), 255, dtype=np.uint8)

    if frame.axis not in ("x", "y") or frame.bit is None:
        raise ValueError("pattern frame needs axis 'x' or 'y' and a bit index")
    size = spec.width if frame.axis == "x" else spec.height
    if not 0 <= frame.bit < bits_for(size):
        raise ValueError(f"bit {frame.bit} out of range for axis {frame.axis}")

    index = np.arange(size, dtype=np.int64)
    gray = index ^ (index >> 1)
    line = ((gray >> frame.bit) & 1).astype(bool)
    if frame.inverse:
        line = ~line
    values = np.where(line, 255, 0).astype(np.uint8)

    if frame.axis == "x":
        image = np.tile(values[np.newaxis, :], (spec.height, 1))
    else:
        image = np.tile(values[:, np.newaxis], (1, spec.width))
    return np.ascontiguousarray(image, dtype=np.uint8)


def gray_to_binary(g: np.ndarray) -> np.ndarray:
    """Convert Gray-code integers to binary, elementwise. Expects an integer array."""
    b = g.copy()
    shift = 1
    while shift < 32:
        b ^= b >> shift
        shift *= 2
    return b
