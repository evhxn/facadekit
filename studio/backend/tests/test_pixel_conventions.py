"""Pixel-corner convention audit: (0, 0) is the top-left corner of the top-left pixel."""

from __future__ import annotations

import numpy as np

from facadekit.calib.capture import CaptureSet
from facadekit.calib.decode import DecodeResult, decode
from facadekit.calib.maps import build_maps, make_proj_view
from facadekit.calib.patterns import PatternSpec, pattern_sequence, render_pattern
from facadekit.geom.homography import apply_h
from facadekit.sim.scene import SimScene


def _centroid(img: np.ndarray) -> tuple[float, float]:
    w = img.astype(np.float64)
    if w.ndim == 3:
        w = w.sum(axis=2)
    ys, xs = np.mgrid[0 : w.shape[0], 0 : w.shape[1]]
    total = w.sum()
    # pixel (x, y) has its centre at (x + 0.5, y + 0.5)
    return float((w * (xs + 0.5)).sum() / total), float((w * (ys + 0.5)).sum() / total)


def test_simulator_block_centroid() -> None:
    scene = SimScene(blur_sigma=0.0, noise_sigma=0.0, ambient=0.0)
    pw, ph = scene.proj_size
    img = np.zeros((ph, pw, 3), np.uint8)
    x0, y0 = 900, 500
    img[y0 : y0 + 4, x0 : x0 + 4] = 255
    cam = scene.render_camera(img, np.random.default_rng(0)).astype(np.float64)
    # remove the (white-less) albedo texture influence: compare against the dark render
    dark = scene.render_camera(np.zeros_like(img), np.random.default_rng(0)).astype(np.float64)
    diff = np.clip(cam - dark, 0, None)
    cx, cy = _centroid(diff)
    ex, ey = apply_h(scene.h_p2c, np.array([[x0 + 2.0, y0 + 2.0]]))[0]
    assert abs(cx - ex) < 0.25 and abs(cy - ey) < 0.25, (cx, cy, ex, ey)


def test_decode_identity_is_pixel_centre() -> None:
    spec = PatternSpec(256, 128, skip_lsb=0)
    frames = pattern_sequence(spec)
    images = np.stack([render_pattern(spec, f).astype(np.float32) for f in frames])
    r = decode(CaptureSet(spec, frames, images, np.zeros((128, 256, 3), np.uint8), {}))
    for x, y in [(0, 0), (17, 3), (255, 127)]:
        assert tuple(r.c2p[y, x]) == (x + 0.5, y + 0.5)


def _synthetic(h_c2p: np.ndarray, wc: int, hc: int, spec: PatternSpec) -> DecodeResult:
    uu, vv = np.meshgrid(np.arange(wc), np.arange(hc))
    centres = np.stack([uu + 0.5, vv + 0.5], axis=-1).reshape(-1, 2)
    c2p = apply_h(h_c2p, centres).reshape(hc, wc, 2).astype(np.float32)
    valid = (
        (c2p[..., 0] >= 0) & (c2p[..., 0] < spec.width) & (c2p[..., 1] >= 0)
        & (c2p[..., 1] < spec.height)
    )  # fmt: skip
    c2p[~valid] = np.nan
    return DecodeResult(c2p, valid, np.full((hc, wc), 100, np.float32), 1.0, 100.0)


H_TRUE = np.array([[1.5, 0.05, 40.0], [0.02, 1.5, 30.0], [2e-5, -1e-5, 1.0]])


def test_maps_are_projector_pixel_centres() -> None:
    spec = PatternSpec(640, 360, skip_lsb=0)
    decoded = _synthetic(H_TRUE, 640, 360, spec)
    maps = build_maps(decoded, spec)
    assert maps.h_c2p is not None
    inv = np.linalg.inv(maps.h_c2p)
    for x, y in [(100, 80), (320, 180), (500, 300), (120, 200)]:
        expect = apply_h(inv, np.array([[x + 0.5, y + 0.5]]))[0]
        assert np.abs(maps.p2c[y, x] - expect).max() < 0.5, (x, y, maps.p2c[y, x], expect)


def test_proj_view_dot_lands_on_predicted_projector_pixel() -> None:
    spec = PatternSpec(640, 360, skip_lsb=0)
    decoded = _synthetic(H_TRUE, 640, 360, spec)
    maps = build_maps(decoded, spec)
    assert maps.h_c2p is not None
    white = np.zeros((360, 640, 3), np.uint8)
    u0, v0 = 300, 200  # a 1-px dot, camera pixel centre (300.5, 200.5)
    white[v0, u0] = 255
    view = make_proj_view(white, maps)
    # forward model: camera point -> projector point
    px, py = apply_h(H_TRUE, np.array([[u0 + 0.5, v0 + 0.5]]))[0]
    sub = view[..., 0].astype(np.float64)
    ys, xs = np.nonzero(sub > 0)
    assert len(xs) > 0
    cx, cy = _centroid(sub[ys.min() - 2 : ys.max() + 3, xs.min() - 2 : xs.max() + 3])
    cx += xs.min() - 2
    cy += ys.min() - 2
    assert abs(cx - px) < 0.75 and abs(cy - py) < 0.75, (cx, cy, px, py)
