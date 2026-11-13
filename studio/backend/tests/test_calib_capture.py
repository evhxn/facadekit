from __future__ import annotations

import numpy as np
import pytest

from facadekit.calib.capture import precheck, run_capture
from facadekit.calib.patterns import PatternSpec
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene

FAST = CalibSettings(discard_frames=0, average_frames=1)


def _noop(_s: float) -> None:
    return None


def _rig(scene: SimScene | None = None) -> tuple[SimDisplay, SimCamera, PatternSpec]:
    scene = scene or default_scene()
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    return display, cam, PatternSpec(*scene.proj_size)


def test_precheck_ok() -> None:
    display, cam, spec = _rig()
    r = precheck(display, cam, spec, sleep=_noop)
    assert r["lit_fraction"] > 0.2
    assert r["median_contrast"] > 40


def test_resolution_mismatch() -> None:
    display, cam, _ = _rig()
    with pytest.raises(FkError) as ei:
        precheck(display, cam, PatternSpec(1280, 720), sleep=_noop)
    assert ei.value.code == "RESOLUTION_MISMATCH"


def test_no_projection() -> None:
    scene = default_scene()
    scene.gain = 0.0
    display, cam, spec = _rig(scene)
    with pytest.raises(FkError) as ei:
        precheck(display, cam, spec, sleep=_noop)
    assert ei.value.code == "CAMERA_NO_PROJECTION"


def test_too_bright() -> None:
    scene = default_scene()
    scene.ambient = 0.9
    scene.gain = 0.05
    display, cam, spec = _rig(scene)
    with pytest.raises(FkError) as ei:
        precheck(display, cam, spec, sleep=_noop)
    assert ei.value.code in ("TOO_BRIGHT", "CAMERA_NO_PROJECTION")


def test_run_capture() -> None:
    display, cam, spec = _rig()
    calls: list[float] = []
    cs = run_capture(display, cam, spec, FAST, progress=lambda f, _m: calls.append(f), sleep=_noop)
    assert cs.images.shape == (38, 720, 1280)
    assert cs.images.dtype == np.float32
    assert cs.white_bgr.shape == (720, 1280, 3)
    assert len(calls) == 38 and calls[-1] == 1.0
    kinds = [f.kind for f in cs.frames]
    assert cs.images[kinds.index("white")].mean() > cs.images[kinds.index("black")].mean()


def test_cancel() -> None:
    display, cam, spec = _rig()
    with pytest.raises(FkError) as ei:
        run_capture(display, cam, spec, FAST, cancel=lambda: True, sleep=_noop)
    assert ei.value.code == "CANCELLED"


def test_precheck_probe_measures_blur() -> None:
    """Six extra shots: the finest skip_lsb whose stripes survive the camera's blur."""
    from facadekit.calib.capture import precheck
    from facadekit.calib.patterns import PatternSpec
    from facadekit.sim.devices import SimCamera, SimDisplay
    from facadekit.sim.scene import SimScene, default_scene

    def run(scene: SimScene, probe: bool) -> tuple[dict[str, float], int]:
        display = SimDisplay(scene)
        shows = [0]
        real_show = display.show

        def counting(image):  # type: ignore[no-untyped-def]
            shows[0] += 1
            real_show(image)

        display.show = counting  # type: ignore[method-assign]
        camera = SimCamera(scene, display)
        camera.open()
        spec = PatternSpec(*scene.proj_size)
        return precheck(display, camera, spec, sleep=lambda _s: None, probe=probe), shows[0]

    plain, n_plain = run(default_scene(), False)
    assert "blur_floor" not in plain and n_plain == 2
    sharp, n_probe = run(default_scene(), True)
    assert n_probe == 8 and sharp["blur_floor"] == 2.0
    assert sharp["modulation_2"] < sharp["modulation_3"] < sharp["modulation_4"] <= 1.0
    assert {k: v for k, v in sharp.items() if k in plain}.keys() == plain.keys()
    blurred, _ = run(SimScene(blur_sigma=4.0), True)
    assert blurred["blur_floor"] >= 3.0
