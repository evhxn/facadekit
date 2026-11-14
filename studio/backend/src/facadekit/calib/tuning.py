from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from facadekit.devices.base import Camera, ProjectorDisplay
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings

_WINDOW_S = 1.5  # longest time we watch one edge
_BUDGET_S = 9.0  # stop adding trials after this much time (tuning should stay near 12 s)
_MIN_STEP = 8.0  # gray levels between black and white, else the camera sees no projection


@dataclass
class LatencyResult:
    settle_ms: int  # recommended wait after show() before capturing
    discard_frames: int  # recommended frames to discard
    frame_interval_ms: float  # measured camera frame interval


def _mean(frame: np.ndarray) -> float:
    return float(frame.mean())


def _settled_time(readings: list[tuple[float, float]], b0: float) -> tuple[float, float]:
    """Return (seconds until the level stays within 5 % of the final one, final level)."""
    tail = [m for _, m in readings[-5:]]
    final = float(np.median(tail))
    tol = 0.05 * abs(final - b0)
    settled = readings[-1][0]
    for t, m in reversed(readings):
        if abs(m - final) > tol:
            break
        settled = t
    return settled, final


def _watch_edge(
    camera: Camera,
    clock: Callable[[], float],
    t_show: float,
    b0: float,
    level: Callable[[np.ndarray], float] = _mean,
) -> list[tuple[float, float]]:
    readings: list[tuple[float, float]] = []
    while clock() - t_show < _WINDOW_S:
        m = level(camera.read())
        readings.append((clock() - t_show, m))
        if len(readings) >= 8:
            last = [v for _, v in readings[-5:]]
            step = abs(last[-1] - b0)
            if step >= _MIN_STEP and max(last) - min(last) <= max(1.0, 0.02 * step):
                break  # new level reached and stable; no need to watch longer
    if not readings:
        readings.append((clock() - t_show, level(camera.read())))
    return readings


def measure_latency(
    display: ProjectorDisplay,
    camera: Camera,
    *,
    trials: int = 4,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> LatencyResult:
    w, h = display.size
    black = np.zeros((h, w), np.uint8)
    white = np.full((h, w), 255, np.uint8)
    start = clock()

    display.show(black)
    sleep(1.0)
    stamps: list[float] = []
    for _ in range(10):
        t0 = clock()
        camera.read()
        stamps.append((clock() - t0) * 1000.0)
    interval_ms = float(np.median(stamps))

    worst = 0.0
    previous: float | None = None
    for trial in range(max(1, trials)):
        if trial >= 1 and clock() - start > _BUDGET_S:
            break
        # Stop once a trial did not raise the worst time by more than one camera frame:
        # further trials would only cost time (each takes 1 to 4 s).
        if trial >= 2 and previous is not None and (worst - previous) * 1000 <= interval_ms:
            break
        previous = worst if trial >= 1 else None
        display.show(black)
        sleep(0.7)
        b0 = _mean(camera.read())
        display.show(white)
        t_show = clock()
        rising = _watch_edge(camera, clock, t_show, b0)
        settled, white_level = _settled_time(rising, b0)
        if abs(white_level - b0) < _MIN_STEP:
            raise FkError(
                "CAMERA_NO_PROJECTION",
                "The camera does not see the projected light. Check that the camera faces the "
                "projection and that the projector is on.",
            )
        worst = max(worst, settled)
        display.show(black)
        t_show = clock()
        falling = _watch_edge(camera, clock, t_show, white_level)
        settled, _ = _settled_time(falling, white_level)
        worst = max(worst, settled)

    settle_ms = min(1500, math.ceil(worst * 1000.0 * 1.3 + interval_ms))
    return LatencyResult(settle_ms=settle_ms, discard_frames=1, frame_interval_ms=interval_ms)


_PATTERN_STEP = 4.0  # mean gray-level change between a stripe picture and its inverse


def _small_gray(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return gray[::4, ::4].astype(np.float32)


def measure_pattern_latency(
    display: ProjectorDisplay,
    camera: Camera,
    *,
    step_settle_ms: int,
    frame_interval_ms: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int | None:
    """Wait needed between two stripe pictures, in ms; None when it cannot be measured.

    `measure_latency` times a jump from black to white. A camera whose automatic exposure
    cannot be switched off (most laptop cameras) keeps adjusting for about a second after such
    a jump, so that wait comes out long. A scan, though, shows stripe pictures one after the
    other, and each is half lit: the exposure does not move and only the projector and camera
    delay is left. This measures that delay, by swapping a wide-stripe picture with its
    inverse and timing how long the camera picture takes to follow.

    Never longer than `step_settle_ms` (that wait is always safe).
    """
    from facadekit.calib.patterns import (  # tuning is imported by capture
        PatternFrame,
        PatternSpec,
        bits_for,
        render_pattern,
    )

    w, h = display.size
    spec = PatternSpec(w, h)
    bit = max(bits_for(w) - 3, 0)  # stripes an eighth of the width: any camera resolves them
    pictures = [
        render_pattern(spec, PatternFrame("pattern", "x", bit, inverted))
        for inverted in (False, True)
    ]
    display.show(pictures[0])
    sleep(step_settle_ms / 1000)  # the first stripes follow black: give the exposure its time
    camera.read()
    reference = _small_gray(camera.read())

    worst = 0.0
    for swap in range(1, 5):

        def change(frame: np.ndarray, ref: np.ndarray = reference) -> float:
            return float(np.abs(_small_gray(frame) - ref).mean())

        baseline = change(camera.read())
        display.show(pictures[swap % 2])
        t_show = clock()
        readings = _watch_edge(camera, clock, t_show, baseline, change)
        settled, final = _settled_time(readings, baseline)
        if final - baseline < _PATTERN_STEP:
            return None  # the stripes hardly show in the picture: keep the safe wait
        worst = max(worst, settled)
        reference = _small_gray(camera.read())
    pattern_ms = math.ceil(worst * 1000.0 * 1.3 + frame_interval_ms)
    return int(min(pattern_ms, step_settle_ms))


def auto_exposure(
    display: ProjectorDisplay,
    camera: Camera,
    *,
    settings: CalibSettings | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, float | bool | None]:
    """Lock exposure so a white projection does not clip. `settings` gives the wait per probe."""
    s = settings or CalibSettings()
    w, h = display.size
    white = np.full((h, w), 255, np.uint8)

    def white_p99() -> float:
        sleep(s.settle_ms / 1000)
        for _ in range(s.discard_frames):
            camera.read()
        gray = cv2.cvtColor(camera.read(), cv2.COLOR_BGR2GRAY)
        return float(np.percentile(gray, 99))

    if not camera.set_controls(auto_exposure=False).get("auto_exposure", False):
        display.show(np.full((h, w), 128, np.uint8))
        sleep(2.0)  # let the camera's own auto-exposure settle on a representative scene
        display.show(white)
        return {"locked": False, "exposure": None, "white_p99": white_p99()}

    display.show(white)
    lo, hi = 0.0, 1.0
    for _ in range(6):
        mid = (lo + hi) / 2
        camera.set_controls(exposure=mid)
        if white_p99() < 240:
            lo = mid
        else:
            hi = mid
    camera.set_controls(exposure=lo)
    return {"locked": True, "exposure": lo, "white_p99": white_p99()}
