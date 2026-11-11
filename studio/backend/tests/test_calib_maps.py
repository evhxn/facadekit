from __future__ import annotations

import cv2
import numpy as np
import pytest

from facadekit.calib.capture import CaptureSet, run_capture
from facadekit.calib.decode import DecodeResult, decode
from facadekit.calib.maps import build_maps, make_proj_view
from facadekit.calib.patterns import PatternSpec
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene

FAST = CalibSettings(discard_frames=0, average_frames=1)


def _noop(_s: float) -> None:
    return None


def _apply(h: np.ndarray, x: float, y: float) -> np.ndarray:
    pt = np.array([[[x, y]]], dtype=np.float64)
    return np.asarray(cv2.perspectiveTransform(pt, h)).reshape(2)


def _capture(scene: SimScene) -> CaptureSet:
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    return run_capture(display, cam, PatternSpec(*scene.proj_size), FAST, sleep=_noop)


def test_exact_synthetic() -> None:
    h_true = np.array([[1.0, 0.02, 1.0], [0.01, 1.0, 1.0], [2e-5, -1e-5, 1.0]])
    wc, hc = 640, 360
    # pixel-corner convention: camera pixel (u, v) is centred at (u + 0.5, v + 0.5)
    uu, vv = np.meshgrid(
        np.arange(wc, dtype=np.float64) + 0.5, np.arange(hc, dtype=np.float64) + 0.5
    )
    pts = np.stack([uu, vv], axis=-1).reshape(1, -1, 2)
    c2p = cv2.perspectiveTransform(pts, h_true).reshape(hc, wc, 2).astype(np.float32)
    decoded = DecodeResult(
        c2p=c2p,
        valid=np.ones((hc, wc), bool),
        contrast=np.full((hc, wc), 100.0, np.float32),
        lit_fraction=1.0,
        median_contrast=100.0,
    )
    maps = build_maps(decoded, PatternSpec(640, 360))
    assert maps.coverage > 0.95
    assert maps.planarity > 0.99
    inv = np.linalg.inv(h_true)
    rng = np.random.default_rng(1)
    checked = 0
    while checked < 200:
        x, y = rng.integers(40, 600), rng.integers(30, 330)
        cam = _apply(inv, x + 0.5, y + 0.5)
        if not (5 <= cam[0] < wc - 5 and 5 <= cam[1] < hc - 5):
            continue
        assert np.abs(maps.p2c[y, x] - cam).max() < 1.0
        checked += 1


def test_grid_step_must_align() -> None:
    decoded = DecodeResult(
        np.zeros((4, 4, 2), np.float32), np.ones((4, 4), bool), np.zeros((4, 4), np.float32), 1, 1
    )
    with pytest.raises(ValueError):
        build_maps(decoded, PatternSpec(64, 64), grid_step=6)


def test_simulator_and_projector_view() -> None:
    scene = default_scene()
    cap = _capture(scene)
    maps = build_maps(decode(cap), PatternSpec(*scene.proj_size))
    assert maps.coverage > 0.85
    assert maps.planarity > 0.8
    for cx, cy in scene.feature_quad_proj("garage_door"):
        got = maps.p2c[int(round(cy)), int(round(cx))]
        want = _apply(scene.h_p2c, cx, cy)
        assert np.abs(got - want).max() < 2.5

    view = make_proj_view(cap.white_bgr, maps)
    assert view.shape == (1080, 1920, 3) and view.dtype == np.uint8
    quad = np.array(scene.feature_quad_proj("garage_door"))
    cx, cy = quad.mean(axis=0)
    assert view[int(cy), int(cx)].mean() > 120
    win = [
        np.array(scene.feature_quad_proj(n)).mean(axis=0) for n in ("window_left", "window_right")
    ]
    mx, my = (win[0] + win[1]) / 2
    b, _g, r = (int(v) for v in view[int(my), int(mx)])
    assert r > b
    assert view[int(my), int(mx)].mean() < view[int(cy), int(cx)].mean()


def test_holes_filled() -> None:
    scene = default_scene()
    scene.occluders_world = [(700, 560, 900, 720)]
    maps = build_maps(decode(_capture(scene)), PatternSpec(*scene.proj_size))
    x0, y0, x1, y1 = scene.occluders_world[0]
    p = _apply(np.linalg.inv(scene.h_p2w), (x0 + x1) / 2, (y0 + y1) / 2)
    px, py = int(round(p[0])), int(round(p[1]))
    assert not maps.measured[py, px]
    want = _apply(scene.h_p2c, float(px), float(py))
    assert np.abs(maps.p2c[py, px] - want).max() < 6.0


def test_too_few_valid() -> None:
    decoded = DecodeResult(
        np.full((50, 50, 2), np.nan, np.float32),
        np.zeros((50, 50), bool),
        np.zeros((50, 50), np.float32),
        0.0,
        0.0,
    )
    with pytest.raises(FkError) as ei:
        build_maps(decoded, PatternSpec(640, 360))
    assert ei.value.code == "SCAN_FAILED"
