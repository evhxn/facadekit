from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from facadekit.calib.patterns import PatternFrame, PatternSpec, pattern_sequence, render_pattern
from facadekit.devices.base import Camera, ProjectorDisplay
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings


@dataclass
class CaptureSet:
    spec: PatternSpec
    frames: list[PatternFrame]
    images: np.ndarray  # float32 (N, Hc, Wc) grayscale, same order as frames
    white_bgr: np.ndarray  # uint8 (Hc, Wc, 3) colour photo under the WHITE frame
    controls_applied: dict[str, bool]  # result of the camera locking attempt


def _shoot(
    display: ProjectorDisplay,
    camera: Camera,
    image: np.ndarray,
    settings: CalibSettings,
    sleep: Callable[[float], None],
) -> tuple[np.ndarray, np.ndarray]:
    display.show(image)
    sleep(settings.settle_ms / 1000)
    for _ in range(settings.discard_frames):
        camera.read()
    acc: np.ndarray | None = None
    n = max(1, settings.average_frames)
    for _ in range(n):
        frame = camera.read().astype(np.float64)
        acc = frame if acc is None else acc + frame
    assert acc is not None
    bgr = np.clip(np.rint(acc / n), 0, 255).astype(np.uint8)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return gray, bgr


def precheck(
    display: ProjectorDisplay,
    camera: Camera,
    spec: PatternSpec,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, float]:
    w, h = display.size
    if (w, h) != (spec.width, spec.height):
        raise FkError(
            "RESOLUTION_MISMATCH",
            f"Projector window is {w}x{h} but the project expects {spec.width}x{spec.height}.",
        )
    settings = CalibSettings()
    black, _ = _shoot(
        display, camera, np.zeros((spec.height, spec.width), np.uint8), settings, sleep
    )
    white, _ = _shoot(
        display, camera, np.full((spec.height, spec.width), 255, np.uint8), settings, sleep
    )
    contrast = white - black
    thr = max(12.0, 0.15 * float(np.percentile(contrast, 95)))
    lit = contrast > thr
    lit_fraction = float(lit.mean())
    if lit_fraction < 0.05:
        raise FkError(
            "CAMERA_NO_PROJECTION",
            "The camera does not see the projected light. Check that the camera faces the "
            "projection and that the projector is on.",
        )
    median_contrast = float(np.median(contrast[lit]))
    if median_contrast < 20:
        raise FkError(
            "TOO_BRIGHT",
            "The projection is too faint compared with the ambient light. Dim the room "
            "or increase the projector brightness.",
        )
    return {"lit_fraction": lit_fraction, "median_contrast": median_contrast}


def run_capture(
    display: ProjectorDisplay,
    camera: Camera,
    spec: PatternSpec,
    settings: CalibSettings,
    *,
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CaptureSet:
    controls = camera.set_controls(auto_exposure=False, auto_wb=False, autofocus=False)
    white_img = np.full((spec.height, spec.width), 255, np.uint8)
    if controls.get("auto_exposure"):
        for e in (0.6, 0.5, 0.4, 0.3, 0.2, 0.1):
            camera.set_controls(exposure=e)
            gray, _ = _shoot(display, camera, white_img, settings, sleep)
            if float(np.percentile(gray, 99)) < 250:
                break

    frames = pattern_sequence(spec)
    cam_w, cam_h = camera.resolution
    images = np.empty((len(frames), cam_h, cam_w), np.float32)
    white_bgr: np.ndarray | None = None
    for i, f in enumerate(frames):
        if cancel is not None and cancel():
            raise FkError("CANCELLED", "Calibration cancelled")
        gray, bgr = _shoot(display, camera, render_pattern(spec, f), settings, sleep)
        images[i] = gray
        if f.kind == "white":
            white_bgr = bgr
        if progress is not None:
            progress((i + 1) / len(frames), f"Pattern {i + 1} of {len(frames)}")
    assert white_bgr is not None
    return CaptureSet(spec, frames, images, white_bgr, controls)
