from __future__ import annotations

import numpy as np
import pytest

from facadekit.calib.patterns import (
    PatternFrame,
    PatternSpec,
    bits_for,
    gray_to_binary,
    pattern_sequence,
    render_pattern,
)


def test_bits_for() -> None:
    assert bits_for(1920) == 11
    assert bits_for(1080) == 11
    assert bits_for(1024) == 10
    assert bits_for(1025) == 11


def test_sequence_length_and_ends() -> None:
    seq = pattern_sequence(PatternSpec(1920, 1080))
    assert len(seq) == 38
    assert seq[0] == PatternFrame("black")
    assert seq[1] == PatternFrame("white")
    assert seq[2] == PatternFrame("pattern", "x", 10, False)
    assert seq[3] == PatternFrame("pattern", "x", 10, True)
    assert seq[-1] == PatternFrame("pattern", "y", 2, True)


def test_render_shape_dtype_and_inverse_sum() -> None:
    spec = PatternSpec(1920, 1080)
    for axis in ("x", "y"):
        frame = PatternFrame("pattern", axis, 5, False)
        inv = PatternFrame("pattern", axis, 5, True)
        img = render_pattern(spec, frame)
        inv_img = render_pattern(spec, inv)
        assert img.shape == (1080, 1920)
        assert img.dtype == np.uint8
        assert img.flags["C_CONTIGUOUS"]
        assert set(np.unique(img).tolist()) <= {0, 255}
        assert np.all(img.astype(np.int32) + inv_img.astype(np.int32) == 255)


def test_render_black_white() -> None:
    spec = PatternSpec(16, 8)
    assert np.all(render_pattern(spec, PatternFrame("black")) == 0)
    assert np.all(render_pattern(spec, PatternFrame("white")) == 255)


def test_round_trip_x_and_y() -> None:
    spec = PatternSpec(64, 48, skip_lsb=0)
    seq = pattern_sequence(spec)
    for axis, size in (("x", 64), ("y", 48)):
        frames = [f for f in seq if f.kind == "pattern" and f.axis == axis and not f.inverse]
        g = np.zeros((48, 64), dtype=np.int64)
        for frame in frames:  # MSB first
            bit_img = render_pattern(spec, frame) == 255
            g = (g << 1) | bit_img.astype(np.int64)
        decoded = gray_to_binary(g)
        expected_line = np.arange(size, dtype=np.int64)
        if axis == "x":
            expected = np.tile(expected_line[np.newaxis, :], (48, 1))
        else:
            expected = np.tile(expected_line[:, np.newaxis], (1, 64))
        assert np.array_equal(decoded, expected)


def test_gray_to_binary_known_values() -> None:
    out = gray_to_binary(np.array([0, 1, 3, 2, 6, 7, 5, 4]))
    assert out.tolist() == [0, 1, 2, 3, 4, 5, 6, 7]


def test_invalid_skip_lsb_raises() -> None:
    with pytest.raises(ValueError):
        pattern_sequence(PatternSpec(64, 48, skip_lsb=6))
    with pytest.raises(ValueError):
        pattern_sequence(PatternSpec(64, 48, skip_lsb=-1))
