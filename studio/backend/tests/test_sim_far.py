from __future__ import annotations

import hashlib
from collections.abc import Callable

import numpy as np
import pytest

from facadekit.geom.homography import apply_h
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import (
    SimScene,
    default_scene,
    scale_world,
    scene_far_marginal,
    scene_far_too_coarse,
    scene_phone,
    true_px_per_proj_px,
)

PRESETS: dict[str, Callable[[], SimScene]] = {
    "marginal": scene_far_marginal,
    "too_coarse": scene_far_too_coarse,
    "phone": scene_phone,
}


def _white_render(scene: SimScene) -> np.ndarray:
    w, h = scene.proj_size
    return scene.render_camera(np.full((h, w), 255, np.uint8), np.random.default_rng(0))


def _digest(img: np.ndarray) -> str:
    return hashlib.sha256(img.tobytes()).hexdigest()


def test_default_scene_untouched() -> None:
    assert _digest(_white_render(default_scene())) == _digest(_white_render(SimScene()))
    assert default_scene() == SimScene()


def test_scale_world_keeps_h_p2c() -> None:
    base = default_scene()
    scaled = scale_world(base, 2.0)
    assert scaled.world_size == (3200, 1800)
    np.testing.assert_allclose(scaled.h_p2c, base.h_p2c, rtol=1e-9)
    assert scaled.world_corners_cam == base.world_corners_cam
    assert scaled.proj_corners_world[0] == (300.0, 160.0)
    assert scaled.features_world["garage_door"] == (1000.0, 960.0, 2200.0, 1600.0)
    assert base.world_size == (1600, 900)  # the source scene is not modified


def test_scale_world_albedo_rebuilt() -> None:
    base = default_scene()
    base.albedo()
    scaled = scale_world(base, 2.0)
    assert scaled.albedo().shape[:2] == (1800, 3200)


def test_far_scene_types() -> None:
    sc = scene_far_marginal()
    assert all(isinstance(v, float) for p in sc.world_corners_cam for v in p)
    assert isinstance(sc.world_corners_cam, tuple) and len(sc.world_corners_cam) == 4


def test_true_scale_ranges(capsys: pytest.CaptureFixture[str]) -> None:
    s_default = true_px_per_proj_px(default_scene())
    s = {name: true_px_per_proj_px(f()) for name, f in PRESETS.items()}
    with capsys.disabled():
        print(
            f"\ntrue s: default={s_default:.4f} " + " ".join(f"{k}={v:.4f}" for k, v in s.items())
        )
    assert 0.40 <= s_default <= 0.55
    assert 0.22 <= s["marginal"] <= 0.34
    assert 0.11 <= s["too_coarse"] <= 0.16
    assert s["phone"] >= 0.55


@pytest.mark.parametrize("name", list(PRESETS))
def test_projector_corners_inside_camera_frame(name: str) -> None:
    sc = PRESETS[name]()
    w, h = sc.proj_size
    cw, ch = sc.cam_size
    pts = apply_h(sc.h_p2c, np.array([(0, 0), (w, 0), (w, h), (0, h)], dtype=np.float64))
    assert (pts[:, 0] >= 0).all() and (pts[:, 0] <= cw).all()
    assert (pts[:, 1] >= 0).all() and (pts[:, 1] <= ch).all()


def test_too_coarse_fails_old_lit_rule() -> None:
    sc = scene_far_too_coarse()
    w, h = sc.proj_size
    display = SimDisplay(sc)
    cam = SimCamera(sc, display)
    cam.open()
    display.show(np.zeros((h, w), np.uint8))
    black = cam.read().astype(np.float32).mean(axis=2)
    display.show(np.full((h, w), 255, np.uint8))
    white = cam.read().astype(np.float32).mean(axis=2)
    contrast = white - black
    lit = contrast > max(12.0, 0.15 * float(np.percentile(contrast, 95)))
    assert float(lit.mean()) < 0.05
