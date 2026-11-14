"""Where the time of one scan goes: projector, camera, waiting, computing.

A scan on real hardware is almost all waiting (the computing is about half a second), so the
only way to make it faster is to know which wait is long. `ScanTimer` wraps the display, the
camera and `sleep`, and `summary()` is written to the log when the scan ends.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np

from facadekit.devices.base import Camera, ProjectorDisplay


class _TimedDisplay:
    def __init__(self, inner: ProjectorDisplay, timer: ScanTimer) -> None:
        self._inner = inner
        self._timer = timer

    @property
    def size(self) -> tuple[int, int]:
        return self._inner.size

    def show(self, image: np.ndarray) -> None:
        started = self._timer.clock()
        try:
            self._inner.show(image)
        finally:
            self._timer.add("projector", self._timer.clock() - started)
            self._timer.pictures += 1


class _TimedCamera:
    def __init__(self, inner: Camera, timer: ScanTimer) -> None:
        self._inner = inner
        self._timer = timer

    @property
    def resolution(self) -> tuple[int, int]:
        return self._inner.resolution

    def open(self) -> None:
        started = self._timer.clock()
        try:
            self._inner.open()
        finally:
            self._timer.add("camera_open", self._timer.clock() - started)

    def close(self) -> None:
        self._inner.close()

    def read(self) -> np.ndarray:
        started = self._timer.clock()
        try:
            return self._inner.read()
        finally:
            self._timer.add("camera", self._timer.clock() - started)
            self._timer.frames += 1

    def set_controls(self, **controls: Any) -> dict[str, bool]:
        return self._inner.set_controls(**controls)


class ScanTimer:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.clock = clock
        self._sleep = sleep
        self._started = clock()
        self._phase_started = self._started
        self._phase: str | None = None
        self.phases: dict[str, float] = {}
        self.kinds: dict[str, float] = {}
        self.pictures = 0
        self.frames = 0

    def add(self, kind: str, seconds: float) -> None:
        self.kinds[kind] = self.kinds.get(kind, 0.0) + seconds

    def display(self, inner: ProjectorDisplay) -> ProjectorDisplay:
        return _TimedDisplay(inner, self)

    def camera(self, inner: Camera) -> Camera:
        return _TimedCamera(inner, self)

    def sleep(self, seconds: float) -> None:
        started = self.clock()
        self._sleep(seconds)
        self.add("waiting", self.clock() - started)

    def phase(self, name: str) -> None:
        """The scan moves on to `name`; time since the last call belongs to the phase before."""
        now = self.clock()
        if self._phase is not None:
            self.phases[self._phase] = self.phases.get(self._phase, 0.0) + now - self._phase_started
        self._phase, self._phase_started = name, now

    def total(self) -> float:
        return self.clock() - self._started

    def summary(self) -> str:
        self.phase("end")
        total = self.total()
        phases = ", ".join(f"{name} {seconds:.1f}" for name, seconds in self.phases.items())
        measured = sum(self.kinds.values())
        kinds = ", ".join(
            f"{name} {self.kinds.get(name, 0.0):.1f}"
            for name in ("projector", "waiting", "camera", "camera_open")
        )
        return (
            f"{total:.1f} s in all. Phases: {phases}. "
            f"Spent on: {kinds}, computing {max(total - measured, 0.0):.1f}. "
            f"{self.pictures} pictures projected, {self.frames} camera frames read."
        )
