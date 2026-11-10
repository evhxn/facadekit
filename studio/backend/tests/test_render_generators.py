from __future__ import annotations

import math

import numpy as np

from facadekit.models.project import Generator
from facadekit.render.generators import parse_color, render_generator


def _gen(kind: str, **params: float | str) -> Generator:
    return Generator.model_validate({"type": kind, "params": params})


def test_parse_color() -> None:
    assert parse_color("#ff8000") == (0, 128, 255)
    assert parse_color("red") == (255, 255, 255)
    assert parse_color("#12345") == (255, 255, 255)
    assert parse_color("#gggggg") == (255, 255, 255)
    assert parse_color(5) == (255, 255, 255)


def test_solid_red() -> None:
    img = render_generator(_gen("solid", color="#ff0000"), 0.0, (2, 2))
    assert img.shape == (2, 2, 4) and img.dtype == np.uint8
    assert (img == np.array([0, 0, 255, 255], np.uint8)).all()


def test_wash_formula() -> None:
    img = render_generator(_gen("wash", period_s=4, angle_deg=0), 0.0, (256, 256))
    for x in (0, 255):
        u = (x + 0.5) / 256
        m = 0.5 + 0.5 * math.sin(2 * math.pi * u)
        exp_r = 255 * (1 - m)
        exp_b = 255 * m
        px = img[100, x]
        assert abs(int(px[2]) - exp_r) <= 2 and abs(int(px[0]) - exp_b) <= 2
        assert px[1] == 0 and px[3] == 255
    # Rows are identical at angle 0.
    assert (img[0] == img[200]).all()


def test_strobe() -> None:
    gen = _gen("strobe", rate_hz=1, duty=0.5, color="#ffffff")
    assert render_generator(gen, 0.25)[0, 0, 3] == 255
    assert render_generator(gen, 0.75)[0, 0, 3] == 0


def test_outline() -> None:
    img = render_generator(_gen("outline", speed=0.5), 0.0, (256, 256))
    # Normative formula: behind = fract(head - s), so the head sits just *before* the
    # top-left corner (left edge, s just under 1); the top edge just after it is the far tail end.
    assert img[8, 3, 3] > 240  # left border just below the top-left corner (s ~ 0.99)
    assert img[3, 8, 3] == 0
    assert img[128, 128, 3] == 0  # centre
    assert img[255 - 2, 128, 3] == 0  # bottom border, s = 0.5
    assert (img[8, 3, :3] == 255).all()


def test_outline_moves() -> None:
    gen = _gen("outline", speed=0.5, tail=0.35)
    img = render_generator(gen, 1.0, (256, 256))  # head at s = 0.5
    assert img[248, 253, 3] > 240  # right edge near bottom-right corner, s ~ 0.49
    assert img[8, 3, 3] == 0
