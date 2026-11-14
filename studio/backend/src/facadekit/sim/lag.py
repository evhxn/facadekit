"""A simulated projector and camera with real-world timing, on a virtual clock.

The plain simulator answers at once, so nothing about scan *speed* can be tested with it. This
one models what a scan spends its time on: the projector picture reaches the camera late, each
camera frame takes a frame time, and a camera whose automatic exposure cannot be switched off
keeps adjusting after the picture's brightness jumps. Time is virtual (`VirtualClock`), so a
scan that "takes" 80 s runs in a few seconds and the result is exact.

The numbers below are assumptions, chosen so that the scan of main takes about as long as the
first hardware test did (82 s). They are not measurements of any real camera.
"""

from __future__ import annotations

import numpy as np

from facadekit import errors
from facadekit.errors import FkError
from facadekit.sim.devices import SimDisplay
from facadekit.sim.scene import SimScene


class VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)


class LaggyDisplay:
    """`show()` takes `show_s`; the camera sees the picture `latency_s` after it returns."""

    def __init__(
        self, scene: SimScene, clock: VirtualClock, *, show_s: float = 0.08, latency_s: float = 0.1
    ) -> None:
        self._inner = SimDisplay(scene)
        self._clock = clock
        self._show_s = show_s
        self._latency_s = latency_s
        self._history: list[tuple[float, np.ndarray | None]] = [(-1.0, None)]

    @property
    def size(self) -> tuple[int, int]:
        return self._inner.size

    def show(self, image: np.ndarray) -> None:
        self._inner.show(image)  # validates the size
        self._clock.sleep(self._show_s)
        self._history.append((self._clock.now + self._latency_s, self._inner.current))
        del self._history[:-8]

    def visible(self) -> np.ndarray | None:
        """What the camera sees now."""
        current = self._history[0][1]
        for at, image in self._history:
            if at <= self._clock.now:
                current = image
        return current


class LaggyCamera:
    """`read()` takes one frame time. Unless `lockable`, automatic exposure follows the picture."""

    def __init__(
        self,
        scene: SimScene,
        display: LaggyDisplay,
        clock: VirtualClock,
        *,
        frame_s: float = 1 / 30,
        lockable: bool = False,
        ae_tau_s: float = 0.35,
        ae_target: float = 70.0,
    ) -> None:
        self._scene = scene
        self._display = display
        self._clock = clock
        self._frame_s = frame_s
        self._lockable = lockable
        self._tau = ae_tau_s
        self._target = ae_target
        self._auto = True
        self._gain = 1.0
        self._rng = np.random.default_rng(scene.seed)
        self._open = False

    @property
    def resolution(self) -> tuple[int, int]:
        return self._scene.cam_size

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def read(self) -> np.ndarray:
        if not self._open:
            raise FkError(errors.CAMERA_READ_FAILED, "camera is not open")
        self._clock.sleep(self._frame_s)
        raw = self._scene.render_camera(self._display.visible(), self._rng).astype(np.float32)
        if self._auto:
            mean = max(float(raw.mean()) * self._gain, 1.0)
            # First-order approach to the gain that makes the picture `ae_target` bright.
            wanted = float(np.clip(self._gain * self._target / mean, 0.4, 6.0))
            self._gain += (wanted - self._gain) * min(self._frame_s / self._tau, 1.0)
        return np.asarray(np.clip(raw * self._gain, 0, 255).astype(np.uint8))

    def set_controls(
        self,
        *,
        auto_exposure: bool | None = None,
        exposure: float | None = None,
        auto_wb: bool | None = None,
        autofocus: bool | None = None,
    ) -> dict[str, bool]:
        out: dict[str, bool] = {}
        if auto_exposure is not None:
            out["auto_exposure"] = self._lockable
            if self._lockable:
                self._auto = auto_exposure
        if exposure is not None:
            out["exposure"] = self._lockable
            if self._lockable:
                self._gain = 0.4 + 1.6 * float(np.clip(exposure, 0.0, 1.0))
        if auto_wb is not None:
            out["auto_wb"] = True
        if autofocus is not None:
            out["autofocus"] = True
        return out
