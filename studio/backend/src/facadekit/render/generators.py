"""Built-in procedural effects (T-109). The WebGL preview mirrors this math (gl/generators.ts)."""

from __future__ import annotations

import math

import numpy as np

from facadekit.models.project import Generator

_WHITE = (255, 255, 255)


def parse_color(value: object) -> tuple[int, int, int]:
    """'#rrggbb' -> (b, g, r). Anything else falls back to white."""
    if (
        isinstance(value, str)
        and len(value) == 7
        and value[0] == "#"
        and all(c in "0123456789abcdefABCDEF" for c in value[1:])
    ):
        return (int(value[5:7], 16), int(value[3:5], 16), int(value[1:3], 16))
    return _WHITE


def _num(gen: Generator, key: str, default: float) -> float:
    value = gen.params.get(key, default)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return default
    value = float(value)
    return value if math.isfinite(value) else default


def _color(gen: Generator, key: str, default: str) -> tuple[int, int, int]:
    return parse_color(gen.params.get(key, default))


def generator_size(gen: Generator) -> tuple[int, int]:
    return (256, 256) if gen.type in ("wash", "outline") else (2, 2)


def render_generator(gen: Generator, tc: float, size: tuple[int, int] | None = None) -> np.ndarray:
    """BGRA uint8 (h, w, 4) on the pixel-centre grid of the surface unit square."""
    w, h = size if size is not None else generator_size(gen)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    u = ((np.arange(w, dtype=np.float64) + 0.5) / w)[None, :]
    v = ((np.arange(h, dtype=np.float64) + 0.5) / h)[:, None]

    if gen.type == "solid":
        out[:, :, :3] = _color(gen, "color", "#ffffff")
        out[:, :, 3] = 255
    elif gen.type == "wash":
        a = math.radians(_num(gen, "angle_deg", 0.0))
        period = _num(gen, "period_s", 4.0) or 4.0
        p = u * math.cos(a) + v * math.sin(a)
        m = 0.5 + 0.5 * np.sin(2.0 * math.pi * (p - tc / period))
        ca = np.array(_color(gen, "color_a", "#ff0000"), dtype=np.float64)
        cb = np.array(_color(gen, "color_b", "#0000ff"), dtype=np.float64)
        rgb = ca + (cb - ca) * m[:, :, None]
        out[:, :, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
        out[:, :, 3] = 255
    elif gen.type == "outline":
        width = _num(gen, "width", 0.03)
        speed = _num(gen, "speed", 0.5)
        tail = _num(gen, "tail", 0.35)
        out[:, :, :3] = _color(gen, "color", "#ffffff")
        uu = np.broadcast_to(u, (h, w))
        vv = np.broadcast_to(v, (h, w))
        dists = np.stack([vv, 1.0 - uu, 1.0 - vv, uu])  # top, right, bottom, left
        edge = np.argmin(dists, axis=0)  # first minimum wins ties
        s = np.choose(
            edge,
            [0.25 * uu, 0.25 + 0.25 * vv, 0.5 + 0.25 * (1.0 - uu), 0.75 + 0.25 * (1.0 - vv)],
        )
        head = (tc * speed) % 1.0
        x = head - s
        behind = x - np.floor(x)
        if tail > 0:
            a_ = np.where(behind < tail, 1.0 - behind / tail, 0.0)
        else:
            a_ = np.zeros_like(behind)
        a_ = np.where(dists.min(axis=0) < width, a_, 0.0)
        out[:, :, 3] = np.clip(np.rint(a_ * 255.0), 0, 255).astype(np.uint8)
    else:  # strobe
        rate = _num(gen, "rate_hz", 8.0)
        duty = _num(gen, "duty", 0.5)
        out[:, :, :3] = _color(gen, "color", "#ffffff")
        phase = tc * rate
        out[:, :, 3] = 255 if (phase - math.floor(phase)) < duty else 0
    return out
