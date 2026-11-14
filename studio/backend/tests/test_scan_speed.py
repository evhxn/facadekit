"""Scan speed (first hardware test, item F), on the virtual-time rig of `sim/lag.py`."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

from facadekit.calib import capture
from facadekit.calib.pipeline import run_calibration
from facadekit.calib.timing import ScanTimer
from facadekit.calib.tuning import measure_latency, measure_pattern_latency
from facadekit.models.calibration import Calibration, CalibSettings
from facadekit.sim.lag import LaggyCamera, LaggyDisplay, VirtualClock
from facadekit.sim.rl.outcomes import map_error
from facadekit.sim.scene import SimScene, default_scene


def _rig(
    latency_s: float = 0.1, lockable: bool = False, scene: SimScene | None = None
) -> tuple[SimScene, VirtualClock, LaggyDisplay, LaggyCamera]:
    scene = scene or default_scene()
    clock = VirtualClock()
    display = LaggyDisplay(scene, clock, latency_s=latency_s)
    return scene, clock, display, LaggyCamera(scene, display, clock, lockable=lockable)


def _scan(
    tmp_path: Path, settings: CalibSettings, latency_s: float = 0.1
) -> tuple[Calibration, float, float, float]:
    scene, clock, display, camera = _rig(latency_s)
    cal = run_calibration(tmp_path, display, camera, "0", settings, sleep=clock.sleep, clock=clock)
    p2c = np.load(tmp_path / "calibration" / cal.id / "p2c.npy")
    median, p95 = map_error(scene, p2c, np.isfinite(p2c).all(axis=2))
    assert median is not None and p95 is not None
    return cal, clock.now, median, p95


def test_stripe_wait_is_the_delay_not_the_exposure_drift() -> None:
    """Black-to-white makes an automatic-exposure camera drift for about a second, so the
    wait measured that way hits its 1500 ms cap. Between stripe pictures only the delay of
    projector and camera is left."""
    for latency_s, low, high in ((0.1, 100, 260), (0.25, 250, 480)):
        _, clock, display, camera = _rig(latency_s)
        camera.open()
        step = measure_latency(display, camera, clock=clock, sleep=clock.sleep)
        assert step.settle_ms == 1500
        stripes = measure_pattern_latency(
            display,
            camera,
            step_settle_ms=step.settle_ms,
            frame_interval_ms=step.frame_interval_ms,
            clock=clock,
            sleep=clock.sleep,
        )
        assert stripes is not None and low < stripes < high, stripes


def test_stripe_wait_is_never_longer_than_the_safe_wait() -> None:
    _, clock, display, camera = _rig(0.25)
    camera.open()
    stripes = measure_pattern_latency(
        display, camera, step_settle_ms=200, frame_interval_ms=33.0,
        clock=clock, sleep=clock.sleep,
    )  # fmt: skip
    assert stripes is not None and stripes <= 200


def test_stripe_wait_is_not_guessed_when_the_stripes_do_not_show() -> None:
    dark = default_scene()
    dark.gain = 0.0  # the projector adds no light: nothing to time
    _, clock, display, camera = _rig(scene=dark)
    camera.open()
    assert (
        measure_pattern_latency(
            display,
            camera,
            step_settle_ms=1500,
            frame_interval_ms=33.0,
            clock=clock,
            sleep=clock.sleep,
        )  # fmt: skip
        is None
    )


def test_automatic_scan_is_fast_and_as_accurate(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """main: 93.7 s on this rig, coverage 0.999, map error 0.17 px median and 0.44 px p95."""
    caplog.set_level(logging.INFO, logger="facadekit.calib.pipeline")
    cal, seconds, median, p95 = _scan(tmp_path, CalibSettings(settle_ms=0))
    assert cal.settings is not None
    assert cal.settings.settle_ms == 1500
    assert cal.settings.pattern_settle_ms is not None and cal.settings.pattern_settle_ms < 260
    assert seconds < 45.0  # measured 37.7 s
    assert cal.quality is not None and cal.quality.coverage > 0.99
    assert median < 0.25 and p95 < 0.6
    timing = [r.getMessage() for r in caplog.records if "scan timing" in r.getMessage()]
    assert len(timing) == 1
    assert "stripe wait" in timing[0] and "pictures projected" in timing[0]
    assert "tuning" in timing[0] and "patterns" in timing[0]


def test_the_rig_notices_a_wait_that_is_too_short(tmp_path: Path) -> None:
    """Without this the test above could not tell a fast scan from a broken one."""
    fixed = {"settle_ms": 1500, "discard_frames": 1}
    bad, _, _, bad_p95 = _scan(tmp_path / "a", CalibSettings(**fixed, pattern_settle_ms=0))
    good, _, _, good_p95 = _scan(tmp_path / "b", CalibSettings(**fixed, pattern_settle_ms=164))
    assert bad.quality is not None and good.quality is not None
    assert bad.quality.coverage < 0.8 < 0.99 < good.quality.coverage
    assert bad_p95 > good_p95


def test_only_stripe_pictures_use_the_stripe_wait() -> None:
    scene, clock, display, camera = _rig()
    camera.open()
    waits: list[float] = []
    settings = CalibSettings(settle_ms=900, pattern_settle_ms=150, discard_frames=0)
    image = np.zeros((scene.proj_size[1], scene.proj_size[0]), np.uint8)
    capture._shoot(display, camera, image, settings, waits.append)
    capture._shoot(display, camera, image, settings, waits.append, stripes=True)
    plain = CalibSettings(settle_ms=900, discard_frames=0)
    capture._shoot(display, camera, image, plain, waits.append, stripes=True)
    assert waits == [0.9, 0.15, 0.9]


def test_timer_adds_up() -> None:
    clock = VirtualClock()
    timer = ScanTimer(clock, clock.sleep)
    timer.phase("tuning")
    timer.sleep(2.0)
    timer.phase("patterns")
    timer.sleep(0.5)
    clock.sleep(0.25)  # time nobody accounted for counts as computing
    text = timer.summary()
    assert "2.8 s in all" in text
    assert "tuning 2.0, patterns 0.8" in text
    assert "waiting 2.5" in text and "computing 0.2" in text
