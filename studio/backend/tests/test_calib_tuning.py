from __future__ import annotations

from bisect import bisect_right
from pathlib import Path

import numpy as np
import pytest

from facadekit.calib.pipeline import run_calibration
from facadekit.calib.tuning import auto_exposure, measure_latency
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene

FRAME_MS = 33.0


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class RecordingDisplay(SimDisplay):
    """SimDisplay that remembers when each image was shown (on the fake clock)."""

    def __init__(self, scene: SimScene, clock: FakeClock) -> None:
        super().__init__(scene)
        self.clock = clock
        self.times: list[float] = []
        self.history: list[np.ndarray] = []

    def show(self, image: np.ndarray) -> None:
        super().show(image)
        assert self.current is not None
        self.times.append(self.clock.t)
        self.history.append(self.current)
        if len(self.history) > 8:  # frames older than that can never be asked for
            del self.history[0], self.times[0]


class LaggyCamera(SimCamera):
    """Each read() takes 33 ms and returns the image that was on screen lag_ms earlier."""

    def __init__(
        self, scene: SimScene, display: RecordingDisplay, clock: FakeClock, lag_ms: float
    ) -> None:
        super().__init__(scene, display)
        self._rd = display
        self._clock = clock
        self._lag_s = lag_ms / 1000

    def read(self) -> np.ndarray:
        self._clock.t += FRAME_MS / 1000
        i = bisect_right(self._rd.times, self._clock.t - self._lag_s) - 1
        shown = self._rd.current
        self._rd.current = self._rd.history[max(i, 0)] if self._rd.history else None
        try:
            return super().read()
        finally:
            self._rd.current = shown


def _rig(
    lag_ms: float, scene: SimScene | None = None
) -> tuple[RecordingDisplay, LaggyCamera, FakeClock]:
    scene = scene or default_scene()
    clock = FakeClock()
    display = RecordingDisplay(scene, clock)
    cam = LaggyCamera(scene, display, clock, lag_ms)
    cam.open()
    return display, cam, clock


def test_latency_200() -> None:
    display, cam, clock = _rig(200)
    r = measure_latency(display, cam, clock=clock.now, sleep=clock.sleep)
    assert 250 <= r.settle_ms <= 420
    assert r.frame_interval_ms == pytest.approx(33, abs=1)
    assert r.discard_frames == 1
    assert clock.t < 12


def test_latency_600() -> None:
    display, cam, clock = _rig(600)
    r = measure_latency(display, cam, clock=clock.now, sleep=clock.sleep)
    assert 760 <= r.settle_ms <= 1000
    assert clock.t < 12


def test_no_projection() -> None:
    scene = default_scene()
    scene.gain = 0.0
    display, cam, clock = _rig(100, scene)
    with pytest.raises(FkError) as ei:
        measure_latency(display, cam, clock=clock.now, sleep=clock.sleep)
    assert ei.value.code == "CAMERA_NO_PROJECTION"


def _coverage(tmp: Path, settings: CalibSettings) -> float:
    display, cam, clock = _rig(400)
    try:
        cal = run_calibration(
            tmp, display, cam, "sim", settings, sleep=clock.sleep, clock=clock.now
        )
    except FkError:
        return 0.0
    assert cal.quality is not None
    return cal.quality.coverage


def test_calibration_through_laggy_camera(tmp_path: Path) -> None:
    tuned = _coverage(tmp_path / "a", CalibSettings(settle_ms=0, average_frames=1))
    fixed = _coverage(tmp_path / "b", CalibSettings(settle_ms=100, average_frames=1))
    assert tuned > 0.85
    assert tuned > fixed


def test_calibration_records_tuned_settings(tmp_path: Path) -> None:
    display, cam, clock = _rig(400)
    cal = run_calibration(
        tmp_path,
        display,
        cam,
        "sim",
        CalibSettings(settle_ms=0, average_frames=1),
        sleep=clock.sleep,
        clock=clock.now,
    )
    assert cal.settings is not None
    assert cal.settings.settle_ms > 0
    assert cal.settings.discard_frames == 1


class ExposureCamera:
    """Brightness of a white projection is 400 * exposure (clipped to 255)."""

    resolution = (64, 48)

    def __init__(self, accepts: bool) -> None:
        self.accepts = accepts
        self.exposure = 1.0

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read(self) -> np.ndarray:
        level = int(min(255.0, 400.0 * self.exposure))
        return np.full((48, 64, 3), level, np.uint8)

    def set_controls(
        self,
        *,
        auto_exposure: bool | None = None,
        exposure: float | None = None,
        auto_wb: bool | None = None,
        autofocus: bool | None = None,
    ) -> dict[str, bool]:
        if exposure is not None and self.accepts:
            self.exposure = exposure
        out = {"auto_exposure": auto_exposure, "exposure": exposure}
        return {k: self.accepts for k, v in out.items() if v is not None}


def test_auto_exposure_locks() -> None:
    display = SimDisplay(default_scene())
    cam = ExposureCamera(accepts=True)
    r = auto_exposure(display, cam, sleep=lambda _s: None)
    assert r["locked"] is True
    assert isinstance(r["exposure"], float)
    assert 200 <= float(r["white_p99"] or 0) < 240
    assert 200 <= int(cam.read()[0, 0, 0]) < 240


def test_auto_exposure_refused() -> None:
    display = SimDisplay(default_scene())
    cam = ExposureCamera(accepts=False)
    r = auto_exposure(display, cam, sleep=lambda _s: None)
    assert r["locked"] is False
    assert r["exposure"] is None
    assert float(r["white_p99"] or 0) > 0
