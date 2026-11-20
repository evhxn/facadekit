"""Exposure that can be locked but not set: the C920 on a Mac (2026-11-19 beta test)."""

from __future__ import annotations

import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from facadekit.calib.capture import DimmedDisplay
from facadekit.calib.pipeline import run_calibration
from facadekit.calib.tuning import CLIPPED_P99, MIN_LEVEL, auto_exposure
from facadekit.devices import avfoundation, camera_opencv
from facadekit.devices.avfoundation import AvDevice
from facadekit.devices.camera_opencv import OpenCvCamera
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimDisplay
from facadekit.sim.lag import LaggyCamera, LaggyDisplay, VirtualClock
from facadekit.sim.scene import default_scene


class LockOnly:
    """Picture brightness = projected level x gain. Auto exposure picks the gain so the
    picture's mean is 180; locking freezes it; a value cannot be set."""

    def __init__(self, display: SimDisplay, *, lockable: bool = True) -> None:
        self.display = display
        self.lockable = lockable
        self.auto = True
        self.gain = 1.0
        self.calls: list[dict[str, Any]] = []

    resolution = (64, 48)

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read(self) -> np.ndarray:
        shown = self.display.current
        level = float(shown.mean()) if shown is not None else 0.0
        if self.auto:
            self.gain = 180.0 / max(level * 0.6, 1.0)  # meters a frame that is 60 % projection
        # The brightest spots reflect twice the average: they clip unless the picture is dim.
        return np.clip(np.full((48, 64, 3), level * self.gain * 1.6), 0, 255).astype(np.uint8)

    def set_controls(self, **controls: Any) -> dict[str, bool]:
        self.calls.append({k: v for k, v in controls.items() if v is not None})
        out: dict[str, bool] = {}
        if controls.get("auto_exposure") is not None:
            out["auto_exposure"] = self.lockable
            if self.lockable:
                self.auto = bool(controls["auto_exposure"])
        if controls.get("exposure") is not None:
            out["exposure"] = False
        return out


def test_locked_camera_that_still_clips_gets_a_dimmer_projection() -> None:
    display = SimDisplay(default_scene())
    camera = LockOnly(display)
    result = auto_exposure(display, camera, sleep=lambda _s: None)
    assert result["locked"] is True and result["exposure"] is None
    level = float(result["level"] or 0)
    assert MIN_LEVEL <= level < 1.0
    assert float(result["white_p99"] or 255) < CLIPPED_P99
    # Exposure was handed back to the camera, then locked again on the white picture.
    assert {"auto_exposure": True} in camera.calls
    assert camera.calls[-1] == {"auto_exposure": False} or not camera.auto


def test_camera_without_any_lock_is_left_alone() -> None:
    display = SimDisplay(default_scene())
    result = auto_exposure(display, LockOnly(display, lockable=False), sleep=lambda _s: None)
    assert result["locked"] is False and "level" not in result


def test_dimmed_display_scales_every_picture() -> None:
    inner = SimDisplay(default_scene())
    dimmed = DimmedDisplay(inner, 0.5)
    w, h = inner.size
    dimmed.show(np.full((h, w), 255, np.uint8))
    assert inner.current is not None and int(inner.current.max()) == 128
    assert dimmed.size == inner.size


def _scan(tmp_path: Path, **camera: Any) -> tuple[float, float, list[str]]:
    scene = replace(default_scene(), gain=1.4)
    clock = VirtualClock()
    display = LaggyDisplay(scene, clock)
    cam = LaggyCamera(scene, display, clock, ae_target=110, **camera)
    cal = run_calibration(
        tmp_path, display, cam, "0", CalibSettings(settle_ms=0), sleep=clock.sleep, clock=clock
    )
    assert cal.quality is not None and cal.quality.unmeasured is not None
    return cal.quality.coverage, cal.quality.unmeasured["bright"], cal.warnings


def test_scan_with_a_lock_only_camera_is_no_longer_washed_out(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Rig numbers, main: coverage 0.833, washed out 0.168 for both kinds of camera."""
    caplog.set_level(logging.WARNING)
    coverage, bright, warnings = _scan(tmp_path / "a", lockable=True, settable=False)
    assert coverage > 0.99 and bright < 0.005
    assert "CAMERA_CONTROLS_UNAVAILABLE" not in warnings
    coverage, bright, warnings = _scan(tmp_path / "b", lockable=False)  # nothing to work with
    assert coverage < 0.9 and bright > 0.1
    assert "CAMERA_CONTROLS_UNAVAILABLE" in warnings


# -- the macOS camera itself, with AVFoundation faked --------------------------------------


class Cap:
    def __init__(self) -> None:
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802
        return True

    def set(self, prop: int, value: float) -> bool:
        return False  # OpenCV's macOS backend changes nothing

    def read(self) -> tuple[bool, np.ndarray]:
        return True, np.zeros((480, 640, 3), np.uint8)

    def release(self) -> None:
        self.released = True


def _fake_mac(monkeypatch: pytest.MonkeyPatch, lockable: bool) -> list[tuple[str, str, bool]]:
    calls: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(camera_opencv, "_platform_key", lambda: "avfoundation")
    monkeypatch.setattr(camera_opencv, "access_problem", lambda: None)
    monkeypatch.setattr(
        avfoundation, "devices", lambda: [AvDevice("usb-c920", "C920", "UVC", "External")]
    )
    monkeypatch.setattr(avfoundation, "lockable", lambda _uid, _c: lockable)

    def set_locked(uid: str, control: str, locked: bool) -> bool:
        calls.append((uid, control, locked))
        return True

    monkeypatch.setattr(avfoundation, "set_locked", set_locked)
    monkeypatch.setattr(camera_opencv, "_default_capture_factory", lambda _i: Cap())
    return calls


def test_mac_camera_locks_through_avfoundation_and_unlocks_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_mac(monkeypatch, lockable=True)
    camera = OpenCvCamera(0)
    camera.open()
    applied = camera.set_controls(auto_exposure=False, autofocus=False, exposure=0.5)
    assert applied == {"auto_exposure": True, "autofocus": True, "exposure": False}
    assert calls == [("usb-c920", "exposure", True), ("usb-c920", "focus", True)]
    camera.close()  # the lock is the device's: give it back for every other app
    assert calls[2:] == [("usb-c920", "exposure", False), ("usb-c920", "focus", False)]


def test_mac_camera_without_modes_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_mac(monkeypatch, lockable=False)  # the built-in MacBook camera
    camera = OpenCvCamera(0)
    camera.open()
    assert camera.set_controls(auto_exposure=False, autofocus=False) == {
        "auto_exposure": False,
        "autofocus": False,
    }
    camera.close()
    assert calls == []
