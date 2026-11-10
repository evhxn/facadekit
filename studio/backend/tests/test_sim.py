from __future__ import annotations

import cv2
import numpy as np
import pytest

from facadekit.errors import FkError
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene


def _apply(h: np.ndarray, p: tuple[float, float]) -> np.ndarray:
    pts = np.array([[p]], dtype=np.float64)
    return np.asarray(cv2.perspectiveTransform(pts, h)[0, 0])


def _rig(scene: SimScene | None = None) -> tuple[SimScene, SimDisplay, SimCamera]:
    scene = scene or default_scene()
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    return scene, display, cam


def _px(img: np.ndarray, p: np.ndarray) -> float:
    x, y = int(round(p[0])), int(round(p[1]))
    return float(img[y - 1 : y + 2, x - 1 : x + 2].mean())


def test_p2c_composition() -> None:
    s = default_scene()
    assert np.allclose(_apply(s.h_p2c, (0, 0)), _apply(s.h_w2c, (150, 80)), atol=1e-6)


def test_feature_quad() -> None:
    s = default_scene()
    quad = s.feature_quad_proj("garage_door")
    world = [_apply(s.h_p2w, p) for p in quad]
    assert np.allclose(world, [(500, 480), (1100, 480), (1100, 800), (500, 800)], atol=1e-6)
    with pytest.raises(KeyError):
        s.feature_quad_proj("nope")


def test_white_and_black() -> None:
    s, d, c = _rig()
    centre = _apply(s.h_w2c, (800, 640))
    d.show(np.full((1080, 1920), 255, np.uint8))
    assert _px(c.read(), centre) > 150
    d.show(np.zeros((1080, 1920, 3), np.uint8))
    assert _px(c.read(), centre) < 30


def test_half_image() -> None:
    s, d, c = _rig()
    img = np.zeros((1080, 1920), np.uint8)
    img[:, :960] = 255
    d.show(img)
    frame = c.read()
    # Compare the red channel: brick is (60, 70, 150) BGR, so the channel mean
    # (~84 vs ~4) falls just short of 80 levels while red (~135 vs ~7) clears it.
    red = frame[:, :, 2:3]
    left = _px(red, _apply(s.h_p2c, (480, 540)))
    right = _px(red, _apply(s.h_p2c, (1440, 540)))
    assert left - right >= 80


def test_occluder() -> None:
    s = default_scene()
    s.occluders_world.append((500, 480, 1100, 800))
    _, d, c = _rig(s)
    d.show(np.full((1080, 1920), 255, np.uint8))
    assert _px(c.read(), _apply(s.h_w2c, (800, 640))) < 30


def test_bad_size() -> None:
    d = SimDisplay(default_scene())
    with pytest.raises(FkError) as ei:
        d.show(np.zeros((100, 100), np.uint8))
    assert ei.value.code == "BAD_IMAGE_SIZE"


def test_read_before_open_and_controls() -> None:
    s = default_scene()
    c = SimCamera(s, SimDisplay(s))
    with pytest.raises(FkError) as ei:
        c.read()
    assert ei.value.code == "CAMERA_READ_FAILED"
    assert c.set_controls(exposure=0.5, autofocus=False) == {"exposure": True, "autofocus": True}


def test_deterministic() -> None:
    frames = []
    for _ in range(2):
        _, d, c = _rig()
        d.show(np.full((1080, 1920), 128, np.uint8))
        frames.append(c.read())
    assert frames[0].shape == (720, 1280, 3)
    assert np.array_equal(frames[0], frames[1])
