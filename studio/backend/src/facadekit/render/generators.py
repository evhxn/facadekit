"""Built-in procedural effects (T-109). The WebGL preview mirrors this math (gl/generators.ts).

The effects added after T-109 (`chase` ... `flicker`) clamp every number to the range in
`GENERATOR_SPECS`, which must match `studio/web/src/gl/generatorParams.json` (a test checks).
Their randomness is a 32-bit integer hash of (cell, seed), the same bit for bit in numpy, in
TypeScript and in GLSL, so a show looks the same on every render and in the preview.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from facadekit.models.project import Generator

_WHITE = (255, 255, 255)

# Photosensitivity: `flicker` never changes level more than this many times a second
# (WCAG 2.3.1 uses three flashes per second as its limit).
MAX_FLICKER_HZ = 3.0


@dataclass(frozen=True)
class ParamSpec:
    key: str
    kind: str  # "color" | "number"
    default: float | str
    min: float | None = None
    max: float | None = None


def _c(key: str, default: str) -> ParamSpec:
    return ParamSpec(key, "color", default)


def _n(key: str, default: float, lo: float | None = None, hi: float | None = None) -> ParamSpec:
    return ParamSpec(key, "number", default, lo, hi)


# Types whose numbers are clamped to [min, max] when rendering (the T-109 four are not, so
# existing projects render exactly as before).
CLAMPED_TYPES = frozenset(
    {"chase", "rings", "stars", "snow", "sweep", "cycle", "plasma", "flicker"}
)

GENERATOR_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "solid": (_c("color", "#ffffff"),),
    "wash": (
        _c("color_a", "#ff0000"), _c("color_b", "#0000ff"),
        _n("period_s", 4.0, 0.1), _n("angle_deg", 0.0),
    ),
    "outline": (
        _c("color", "#ffffff"), _n("width", 0.03, 0.0), _n("speed", 0.5), _n("tail", 0.35, 0.0),
    ),
    "strobe": (_c("color", "#ffffff"), _n("rate_hz", 8.0, 0.0), _n("duty", 0.5, 0.0)),
    "chase": (
        _c("color", "#ffd040"), _n("width", 0.05, 0.01, 0.25), _n("dashes", 16, 2, 64),
        _n("speed", 2.0, -10, 10),
    ),
    "rings": (
        _c("color_a", "#ff6a00"), _c("color_b", "#2a0050"), _n("count", 4.0, 0.5, 20),
        _n("speed", 0.5, -5, 5),
    ),
    "stars": (
        _c("color", "#ffffff"), _n("density", 12, 4, 40), _n("size", 0.3, 0.1, 0.45),
        _n("twinkle_hz", 0.5, 0, 2), _n("seed", 1, 0, 999999),
    ),
    "snow": (
        _c("color", "#ffffff"), _n("density", 10, 4, 30), _n("size", 0.3, 0.1, 0.45),
        _n("speed", 0.15, 0, 1), _n("drift", 0.4, 0, 1), _n("seed", 2, 0, 999999),
    ),
    "sweep": (
        _c("color", "#ffffff"), _n("width", 0.15, 0.02, 1), _n("period_s", 3.0, 0.5, 60),
        _n("angle_deg", 0.0, -360, 360),
    ),
    "cycle": (
        _n("period_s", 8.0, 0.5, 120), _n("spread", 0.5, 0, 3), _n("angle_deg", 0.0, -360, 360),
        _n("saturation", 1.0, 0, 1), _n("brightness", 1.0, 0, 1),
    ),
    "plasma": (
        _c("color_a", "#ff00aa"), _c("color_b", "#00e0ff"), _n("scale", 3.0, 0.5, 10),
        _n("speed", 0.5, 0, 3),
    ),
    "flicker": (
        _c("color", "#ffb040"), _n("rate_hz", 2.0, 0.1, MAX_FLICKER_HZ), _n("depth", 0.6, 0, 1),
        _n("seed", 3, 0, 999999),
    ),
}  # fmt: skip

_M32 = 0xFFFFFFFF
_GOLDEN = 0x9E3779B9


def _mix(h: np.ndarray) -> np.ndarray:
    """32-bit integer finaliser on uint64 arrays holding 32-bit values (GLSL: fkMix)."""
    h = h ^ (h >> np.uint64(16))
    h = (h * np.uint64(0x7FEB352D)) & np.uint64(_M32)
    h = h ^ (h >> np.uint64(15))
    h = (h * np.uint64(0x846CA68B)) & np.uint64(_M32)
    return h ^ (h >> np.uint64(16))


def _u32(a: np.ndarray | int) -> np.ndarray:
    return (np.asarray(a, dtype=np.int64) & _M32).astype(np.uint64)


def cell_hash(a: np.ndarray | int, b: np.ndarray | int, seed: int) -> np.ndarray:
    """Hash of integer cell (a, b) and a seed (GLSL: fkHash)."""
    m = np.uint64(_M32)
    h = (
        ((_u32(a) * np.uint64(0x8DA6B343)) & m)
        ^ ((_u32(b) * np.uint64(0xD8163841)) & m)
        ^ ((_u32(seed) * np.uint64(0xCB1AB31F)) & m)
    )
    return _mix(h)


def rnd(h: np.ndarray, k: int) -> np.ndarray:
    """k-th uniform number in [0, 1) from hash `h`, 24 bits (exact in float32; GLSL: fkRnd)."""
    hk = (h + np.uint64((k * _GOLDEN) & _M32)) & np.uint64(_M32)
    return (_mix(hk) >> np.uint64(8)).astype(np.float64) * (1.0 / 16777216.0)


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    out: np.ndarray = t * t * (3.0 - 2.0 * t)
    return out


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


def param(gen: Generator, key: str) -> float:
    """A number of a clamped generator type: the spec default if missing, clamped to its range."""
    spec = next(p for p in GENERATOR_SPECS[gen.type] if p.key == key)
    assert isinstance(spec.default, (int, float))
    value = _num(gen, key, float(spec.default))
    if spec.min is not None:
        value = max(spec.min, value)
    if spec.max is not None:
        value = min(spec.max, value)
    return value


def _int_param(gen: Generator, key: str) -> int:
    return math.floor(param(gen, key) + 0.5)


def _rgb(gen: Generator, key: str) -> np.ndarray:
    spec = next(p for p in GENERATOR_SPECS[gen.type] if p.key == key)
    b, g, r = _color(gen, key, str(spec.default))
    return np.array([b, g, r], dtype=np.float64) / 255.0


_UNIFORM_TYPES = frozenset({"solid", "strobe", "flicker"})


def generator_size(gen: Generator) -> tuple[int, int]:
    if gen.type in _UNIFORM_TYPES:
        return (2, 2)
    # Small round dots need more texels to look the same as the per-pixel preview.
    return (384, 384) if gen.type in ("stars", "snow") else (256, 256)


def flicker_level(gen: Generator, tc: float) -> float:
    """Alpha of `flicker` at clip time tc: smooth steps between seeded random levels."""
    rate = param(gen, "rate_hz")
    depth = param(gen, "depth")
    seed = _int_param(gen, "seed")
    n = max(tc, 0.0) * rate
    i = math.floor(n)
    f = n - i
    a0 = float(rnd(cell_hash(i, 0, seed), 0))
    a1 = float(rnd(cell_hash(i + 1, 0, seed), 0))
    s = f * f * (3.0 - 2.0 * f)
    return 1.0 - depth * (a0 + (a1 - a0) * s)


def _to_bgra(out: np.ndarray, bgr: np.ndarray, alpha: np.ndarray | float) -> None:
    out[:, :, :3] = np.clip(np.rint(bgr * 255.0), 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(np.rint(np.asarray(alpha) * 255.0), 0, 255).astype(np.uint8)


def _perimeter(u: np.ndarray, v: np.ndarray, aspect: float) -> tuple[np.ndarray, np.ndarray]:
    """Distance to the nearest edge (surface heights) and position along the border (0..1,
    clockwise from the top-left corner, by length)."""
    total = 2.0 * aspect + 2.0
    dists = np.stack([v, (1.0 - u) * aspect, 1.0 - v, u * aspect])  # top, right, bottom, left
    edge = np.argmin(dists, axis=0)  # first minimum wins ties, like the shader
    s = np.choose(
        edge,
        [
            u * aspect / total,
            (aspect + v) / total,
            (aspect + 1.0 + (1.0 - u) * aspect) / total,
            (2.0 * aspect + 1.0 + (1.0 - v)) / total,
        ],
    )
    return dists.min(axis=0), s


def _dots(
    x: np.ndarray,
    y: np.ndarray,
    size: float,
    seed: int,
    present: float,
    tc: float,
    twinkle_hz: float,
    sway: float,
) -> np.ndarray:
    """Soft round dots, at most one per unit cell, zero on every cell border."""
    ci = np.floor(x)
    cj = np.floor(y)
    fx = x - ci
    fy = y - cj
    hx = ci.astype(np.int64) + 1048576
    hy = cj.astype(np.int64) + 1048576
    h = cell_hash(hx, hy, seed)
    on = rnd(h, 0) < present
    jx = 2.0 * rnd(h, 1) - 1.0
    if sway > 0:
        jx = jx * (1.0 - sway) + sway * np.sin(2.0 * math.pi * (tc * 0.25 + rnd(h, 3)))
    cx = 0.5 + (0.5 - size) * jx
    cy = size + (1.0 - 2.0 * size) * rnd(h, 2)
    d = np.sqrt((fx - cx) ** 2 + (fy - cy) ** 2) / size
    a = 1.0 - _smoothstep(0.0, 1.0, d)
    if twinkle_hz > 0:
        rate = twinkle_hz * (0.5 + rnd(h, 3))
        a = a * (0.55 + 0.45 * np.sin(2.0 * math.pi * (tc * rate + rnd(h, 4))))
    out: np.ndarray = np.where(on, a, 0.0)
    return out


def render_generator(
    gen: Generator, tc: float, size: tuple[int, int] | None = None, aspect: float = 1.0
) -> np.ndarray:
    """BGRA uint8 (h, w, 4) on the pixel-centre grid of the surface unit square.

    `aspect` is the surface's width / height: the newer effects use it so that rings and
    dots stay round and border dashes stay even on a wide surface.
    """
    w, h = size if size is not None else generator_size(gen)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    u = ((np.arange(w, dtype=np.float64) + 0.5) / w)[None, :]
    v = ((np.arange(h, dtype=np.float64) + 0.5) / h)[:, None]
    uu = np.broadcast_to(u, (h, w))
    vv = np.broadcast_to(v, (h, w))
    aspect = aspect if math.isfinite(aspect) and aspect > 0 else 1.0

    if gen.type in CLAMPED_TYPES:
        _render_new(gen, tc, out, uu, vv, aspect)
        return out
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


def _render_new(
    gen: Generator, tc: float, out: np.ndarray, u: np.ndarray, v: np.ndarray, aspect: float
) -> None:
    kind = gen.type
    if kind == "chase":
        width = param(gen, "width")
        dashes = _int_param(gen, "dashes")
        d, s = _perimeter(u, v, aspect)
        m = 0.5 + 0.5 * np.cos(2.0 * math.pi * (s * dashes - tc * param(gen, "speed")))
        a = _smoothstep(0.35, 0.65, m) * (1.0 - _smoothstep(width - 0.01, width, d))
        _to_bgra(out, _rgb(gen, "color"), a)
    elif kind == "rings":
        r = np.sqrt(((u - 0.5) * aspect) ** 2 + (v - 0.5) ** 2)
        m = 0.5 + 0.5 * np.cos(2.0 * math.pi * (r * param(gen, "count") - tc * param(gen, "speed")))
        ca, cb = _rgb(gen, "color_a"), _rgb(gen, "color_b")
        _to_bgra(out, ca + (cb - ca) * m[:, :, None], 1.0)
    elif kind == "stars":
        density = param(gen, "density")
        a = _dots(
            u * aspect * density, v * density, param(gen, "size"), _int_param(gen, "seed"),
            0.55, tc, param(gen, "twinkle_hz"), 0.0,
        )  # fmt: skip
        _to_bgra(out, _rgb(gen, "color"), a)
    elif kind == "snow":
        density = param(gen, "density")
        seed = _int_param(gen, "seed")
        x = u * aspect * density
        col = rnd(cell_hash(np.floor(x).astype(np.int64) + 1048576, 0, seed ^ 0x5A5A), 0)
        fall = tc * param(gen, "speed") * density * (0.7 + 0.6 * col)
        y = v * density - fall + 7.0 * col
        a = _dots(x, y, param(gen, "size"), seed, 0.5, tc, 0.0, param(gen, "drift"))
        _to_bgra(out, _rgb(gen, "color"), a)
    elif kind == "sweep":
        width = param(gen, "width")
        ang = math.radians(param(gen, "angle_deg"))
        cos_a, sin_a = math.cos(ang), math.sin(ang)
        p = u * cos_a + v * sin_a
        lo = min(0.0, cos_a) + min(0.0, sin_a)
        hi = max(0.0, cos_a) + max(0.0, sin_a)
        phase = tc / param(gen, "period_s")
        c = lo - width + (hi - lo + 2.0 * width) * (phase - math.floor(phase))
        a = 1.0 - _smoothstep(0.0, width, np.abs(p - c))
        _to_bgra(out, _rgb(gen, "color"), a)
    elif kind == "cycle":
        ang = math.radians(param(gen, "angle_deg"))
        p = u * math.cos(ang) + v * math.sin(ang)
        hue = p * param(gen, "spread") + tc / param(gen, "period_s")
        sat = param(gen, "saturation")
        bright = param(gen, "brightness")
        chans = []
        for k in (1.0 / 3.0, 2.0 / 3.0, 0.0):  # b, g, r
            f = hue + k
            f = f - np.floor(f)
            pure = np.clip(np.abs(f * 6.0 - 3.0) - 1.0, 0.0, 1.0)
            chans.append(bright * (1.0 - sat + sat * pure))
        _to_bgra(out, np.stack(chans, axis=-1), 1.0)
    elif kind == "plasma":
        sc = param(gen, "scale")
        t = tc * param(gen, "speed")
        x = (u - 0.5) * aspect * sc
        y = (v - 0.5) * sc
        val = (
            np.sin(x * 2.0 + t * 1.7) + np.sin(y * 2.3 - t * 1.3)
            + np.sin((x + y) * 1.4 + t) + np.sin(np.sqrt(x * x + y * y) * 2.6 - t * 2.1)
        )  # fmt: skip
        m = 0.5 + 0.5 * np.sin(val * 1.2)
        ca, cb = _rgb(gen, "color_a"), _rgb(gen, "color_b")
        _to_bgra(out, ca + (cb - ca) * m[:, :, None], 1.0)
    else:  # flicker
        _to_bgra(out, _rgb(gen, "color"), flicker_level(gen, tc))
