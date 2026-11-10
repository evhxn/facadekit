from __future__ import annotations

import cv2
import numpy as np
import pytest

from facadekit.calib.capture import CaptureSet, run_capture
from facadekit.calib.decode import decode
from facadekit.calib.patterns import PatternSpec, pattern_sequence, render_pattern
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene

FAST = CalibSettings(discard_frames=0, average_frames=1)


def _noop(_s: float) -> None:
    return None


def _identity_capture(skip_lsb: int) -> CaptureSet:
    spec = PatternSpec(256, 128, skip_lsb=skip_lsb)
    frames = pattern_sequence(spec)
    images = np.stack([render_pattern(spec, f).astype(np.float32) for f in frames])
    return CaptureSet(spec, frames, images, np.zeros((128, 256, 3), np.uint8), {})


def test_exact_skip0() -> None:
    r = decode(_identity_capture(0))
    assert r.valid.all()
    xs, ys = np.meshgrid(np.arange(256), np.arange(128))
    assert np.array_equal(r.c2p[..., 0], xs + 0.5)
    assert np.array_equal(r.c2p[..., 1], ys + 0.5)


def test_skip2() -> None:
    r = decode(_identity_capture(2))
    assert r.valid.all()
    xs, ys = np.meshgrid(np.arange(256), np.arange(128))
    assert np.abs(r.c2p[..., 0] - (xs + 0.5)).max() <= 2.0
    assert np.abs(r.c2p[..., 1] - (ys + 0.5)).max() <= 2.0


def _sim_capture(scene: SimScene) -> CaptureSet:
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    return run_capture(display, cam, PatternSpec(*scene.proj_size), FAST, sleep=_noop)


def test_simulator_accuracy() -> None:
    scene = default_scene()
    r = decode(_sim_capture(scene))
    assert r.valid.mean() > 0.15
    w, h = scene.cam_size
    uu, vv = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    pts = np.stack([uu, vv], axis=-1).reshape(1, -1, 2)
    gt = cv2.perspectiveTransform(pts, np.linalg.inv(scene.h_p2c)).reshape(h, w, 2)
    err = np.abs(r.c2p[r.valid].astype(np.float64) - gt[r.valid])
    for a in (0, 1):
        assert np.median(err[:, a]) < 3.0
        assert np.percentile(err[:, a], 95) < 6.0


def test_occluded_door() -> None:
    scene = default_scene()
    scene.occluders_world = [scene.features_world["garage_door"]]
    r = decode(_sim_capture(scene))
    x0, y0, x1, y1 = scene.features_world["garage_door"]
    quad = np.array([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]], dtype=np.float64)
    cq = cv2.perspectiveTransform(quad, scene.h_w2c)[0].astype(np.int32)
    mask = np.zeros(r.valid.shape, np.uint8)
    cv2.fillConvexPoly(mask, cq, 1)
    inside = mask.astype(bool)
    assert inside.sum() > 1000
    assert r.valid[inside].mean() < 0.05


def test_missing_white() -> None:
    cs = _identity_capture(2)
    keep = [i for i, f in enumerate(cs.frames) if f.kind != "white"]
    cs2 = CaptureSet(
        cs.spec, [cs.frames[i] for i in keep], cs.images[keep], cs.white_bgr, cs.controls_applied
    )
    with pytest.raises(FkError) as ei:
        decode(cs2)
    assert ei.value.code == "SCAN_FAILED"
