from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from facadekit.calib.patterns import PatternFrame, PatternSpec, pattern_sequence, render_pattern
from facadekit.calib.quality import (
    LitBlob,
    lit_blob,
    lit_threshold,
    noise_sigma,
    projection_found,
    px_per_proj_px,
)
from facadekit.calib.roi import crop_too_tight
from facadekit.calib.tuning import auto_exposure
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


def no_projection_error() -> FkError:
    return FkError(
        "CAMERA_NO_PROJECTION",
        "The camera does not see the projected light. Check that the camera faces the "
        "projection and that the projector is on.",
    )


def lit_stats(
    contrast: np.ndarray, white: np.ndarray, blob: LitBlob, proj_w: int, proj_h: int
) -> dict[str, float]:
    """Measurements shared by the live precheck and recorded captures."""
    lit = contrast > lit_threshold(contrast)
    x0, y0, x1, y1 = blob.box
    return {
        "lit_fraction": float(lit.mean()),
        "median_contrast": float(np.median(contrast[lit])),
        "lit_px": float(blob.area_px),
        "px_per_proj_px": px_per_proj_px(blob.hull_area_px, proj_w, proj_h),
        "p99_white": float(np.percentile(white, 99)),
        "lit_x0": float(x0),
        "lit_y0": float(y0),
        "lit_x1": float(x1),
        "lit_y1": float(y1),
    }


def precheck(
    display: ProjectorDisplay,
    camera: Camera,
    spec: PatternSpec,
    *,
    settings: CalibSettings | None = None,
    sleep: Callable[[float], None] = time.sleep,
    crop_check: bool = False,
) -> dict[str, float]:
    w, h = display.size
    if (w, h) != (spec.width, spec.height):
        raise FkError(
            "RESOLUTION_MISMATCH",
            f"Projector window is {w}x{h} but the project expects {spec.width}x{spec.height}.",
        )
    settings = settings or CalibSettings()
    black, _ = _shoot(
        display, camera, np.zeros((spec.height, spec.width), np.uint8), settings, sleep
    )
    white, _ = _shoot(
        display, camera, np.full((spec.height, spec.width), 255, np.uint8), settings, sleep
    )
    contrast = white - black
    blob = lit_blob(contrast)
    if blob is None or not projection_found(blob):
        raise no_projection_error()
    stats = lit_stats(contrast, white, blob, spec.width, spec.height)
    if crop_check:  # cropped scan: is the crop cutting into the lit area?
        stats["crop_tight"] = 1.0 if crop_too_tight(blob.mask) else 0.0
    median_contrast = stats["median_contrast"]
    if median_contrast < 20:
        raise FkError(
            "TOO_BRIGHT",
            "The projection is too faint compared with the ambient light. Dim the room "
            "or increase the projector brightness.",
        )
    # Two more single frames of the still-white scene give the sensor noise.
    frame_a = cv2.cvtColor(camera.read(), cv2.COLOR_BGR2GRAY).astype(np.float32)
    frame_b = cv2.cvtColor(camera.read(), cv2.COLOR_BGR2GRAY).astype(np.float32)
    snr = median_contrast / max(noise_sigma(frame_a, frame_b, blob.mask), 0.1)
    stats["snr"] = snr
    return stats


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
    auto_exposure(display, camera, settings=settings, sleep=sleep)

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
