from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from facadekit.models.project import Generator
from facadekit.render.generators import (
    CLAMPED_TYPES,
    GENERATOR_SPECS,
    MAX_FLICKER_HZ,
    cell_hash,
    flicker_level,
    param,
    parse_color,
    render_generator,
    rnd,
)


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


# --- Effects added after T-109 -------------------------------------------------------------

NEW_TYPES = ("chase", "rings", "stars", "snow", "sweep", "cycle", "plasma", "flicker")
WEB_PARAMS = Path(__file__).resolve().parents[2] / "web" / "src" / "gl" / "generatorParams.json"


def test_hash_golden_values() -> None:
    # The same numbers are asserted in web/src/gl/generators.test.ts: the hash must agree
    # bit for bit between numpy, TypeScript and GLSL.
    h = cell_hash(1, 2, 3)
    assert int(h) == 72785788
    assert float(rnd(h, 0)) == 4807689 / 16777216
    h2 = cell_hash(-5, 7, 42)
    assert int(h2) == 2305073543
    assert abs(float(rnd(h2, 2)) - 0.8092036843299866) < 1e-12


def test_specs_match_web_json() -> None:
    web = json.loads(WEB_PARAMS.read_text())
    assert set(web) == set(GENERATOR_SPECS)
    for kind, specs in GENERATOR_SPECS.items():
        assert web[kind]["clamp"] == (kind in CLAMPED_TYPES), kind
        ours = [(p.key, p.kind, p.default, p.min, p.max) for p in specs]
        theirs = [
            (p["key"], p["kind"], p["def"], p.get("min"), p.get("max")) for p in web[kind]["params"]
        ]
        assert ours == theirs, kind


@pytest.mark.parametrize("kind", NEW_TYPES)
def test_new_types_render_and_are_deterministic(kind: str) -> None:
    gen = _gen(kind)
    a = render_generator(gen, 1.25, aspect=1.6)
    b = render_generator(gen, 1.25, aspect=1.6)
    assert a.shape[2] == 4 and a.dtype == np.uint8
    assert (a == b).all()
    later = render_generator(gen, 2.0, aspect=1.6)
    assert not (a == later).all(), "the effect should move over time"
    assert a[:, :, 3].max() > 0


@pytest.mark.parametrize("kind", ["stars", "snow", "flicker"])
def test_seed_changes_the_pattern(kind: str) -> None:
    one = render_generator(_gen(kind, seed=1), 0.7, aspect=1.5)
    two = render_generator(_gen(kind, seed=2), 0.7, aspect=1.5)
    assert not (one == two).all()


def test_numbers_are_clamped_and_bad_values_fall_back() -> None:
    assert param(_gen("flicker", rate_hz=50), "rate_hz") == MAX_FLICKER_HZ
    assert param(_gen("stars", density=1000), "density") == 40
    assert param(_gen("chase", width="wide"), "width") == 0.05
    assert param(_gen("rings", count=float("nan")), "count") == 4.0


def test_flicker_is_slow_and_smooth() -> None:
    # Photosensitivity: even asked for 20 Hz, the level changes direction at most 3 times a
    # second, and moves smoothly between frames at 30 fps.
    gen = _gen("flicker", rate_hz=20, depth=1.0, seed=9)
    levels = np.array([flicker_level(gen, n / 30.0) for n in range(30 * 10)])
    steps = np.diff(levels)
    turns = int(np.sum(np.sign(steps[1:]) * np.sign(steps[:-1]) < 0))
    assert turns <= 3 * 10
    assert np.abs(steps).max() < 0.25
    assert levels.min() >= 0.0 and levels.max() <= 1.0


def test_chase_lights_only_the_border() -> None:
    img = render_generator(_gen("chase", width=0.05, dashes=8, speed=0), 0.0, (256, 256))
    assert img[128, 128, 3] == 0
    top = img[2, :, 3]
    assert top.max() > 240 and top.min() == 0  # dashes and gaps along the top edge


def test_rings_centre_and_sweep_position() -> None:
    img = render_generator(_gen("rings", color_a="#000000", color_b="#ffffff"), 0.0, (65, 65))
    assert (img[32, 32, :3] > 250).all()  # r = 0 -> m = 1 -> colour B
    sweep = _gen("sweep", width=0.2, period_s=4, angle_deg=0)
    img = render_generator(sweep, 2.0, (256, 256))  # half a period: bar centred
    assert img[100, 128, 3] > 250
    assert img[100, 10, 3] == 0 and img[100, 245, 3] == 0


def test_cycle_without_saturation_is_grey() -> None:
    img = render_generator(_gen("cycle", saturation=0, brightness=0.5), 3.0, (16, 16))
    assert (img[:, :, :3] == 128).all() and (img[:, :, 3] == 255).all()


def test_stars_stay_round_on_a_wide_surface() -> None:
    # Dots are laid out in aspect-corrected units: on a 2:1 surface the raster (square)
    # squeezes them horizontally by 2, so a star is half as wide (in texels) as it is tall.
    gen = _gen("stars", density=4, size=0.45, twinkle_hz=0, seed=5)
    img = render_generator(gen, 0.0, (512, 512), aspect=2.0)[:, :, 3]
    n, labels, stats, _ = cv2.connectedComponentsWithStats((img > 20).astype(np.uint8))
    assert n > 2
    widths = stats[1:, cv2.CC_STAT_WIDTH]
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    ratio = float(np.median(heights / widths))
    assert 1.7 < ratio < 2.3
