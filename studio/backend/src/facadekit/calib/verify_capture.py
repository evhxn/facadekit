"""Photographs for the verification checks: shooting, exposure and the lit-ratio image.

Every measurement works on a *ratio* image: (picture - black) / (white - black). The black shot
removes ambient light and the white shot divides out the surface colour, so a dark brick next
to a light door does not look "less lit" (docs/13-surface-verification.md, "Reference
subtraction").
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

from facadekit.calib.quality import lit_blob, projection_found
from facadekit.calib.tuning import CLIPPED_P99, MIN_LEVEL
from facadekit.devices.base import Camera, ProjectorDisplay
from facadekit.errors import CAMERA_NO_PROJECTION, CANCELLED, TOO_BRIGHT, FkError
from facadekit.models.calibration import CalibSettings

START_LEVEL = 0.6  # first projection level: well below full brightness (no blinding flash)
DIM_FACTOR = 0.65  # level step when the white picture clips
MIN_CONTRAST = 14.0  # median white-minus-black gray levels needed in the lit area
GOOD_CONTRAST = 40.0  # below this the level is raised (towards full brightness)
BRIGHTER_STEP = 0.2
RATIO_FLOOR = 6.0  # white-minus-black below this is too dark to measure a ratio on
MAX_AVERAGE = 3  # frames averaged per shot (fewer in low-power mode)


@dataclass
class Shot:
    gray: np.ndarray  # float32 (Hc, Wc)
    bgr: np.ndarray  # uint8 (Hc, Wc, 3)


@dataclass
class Shooter:
    """Shows a picture at `level` of its brightness and photographs it."""

    display: ProjectorDisplay
    camera: Camera
    settings: CalibSettings
    sleep: Callable[[float], None] = time.sleep
    cancel: Callable[[], bool] | None = None
    level: float = START_LEVEL
    shots: int = 0
    log: list[str] = field(default_factory=list)

    def check_cancel(self) -> None:
        if self.cancel is not None and self.cancel():
            raise FkError(CANCELLED, "Verification cancelled")

    def shoot(self, image: np.ndarray, name: str) -> Shot:
        self.check_cancel()
        level = self.level
        dimmed = np.rint(image.astype(np.float32) * np.float32(level)).astype(np.uint8)
        self.display.show(dimmed)
        self.sleep(self.settings.settle_ms / 1000)
        for _ in range(self.settings.discard_frames):
            self.camera.read()
        acc: np.ndarray | None = None
        n = max(1, min(self.settings.average_frames, MAX_AVERAGE))
        for _ in range(n):
            frame = self.camera.read().astype(np.float64)
            acc = frame if acc is None else acc + frame
        assert acc is not None
        bgr = np.clip(np.rint(acc / n), 0, 255).astype(np.uint8)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        self.shots += 1
        self.log.append(f"{name}@{level:.2f}")
        return Shot(gray=gray, bgr=bgr)


@dataclass
class Exposure:
    black: Shot
    white: Shot
    locked: bool
    level: float
    p99_white: float
    median_contrast: float
    lit: np.ndarray  # bool (Hc, Wc): where the white picture lights the scene


def _lit_stats(black: Shot, white: Shot) -> tuple[np.ndarray, float, float]:
    contrast = white.gray - black.gray
    blob = lit_blob(contrast)
    if blob is None or not projection_found(blob):
        raise FkError(
            CAMERA_NO_PROJECTION,
            "The camera does not see the projected light. Check that the camera still faces "
            "the projection and that the projector window is on the projector.",
        )
    lit = blob.mask
    return lit, float(np.percentile(white.gray[lit], 99)), float(np.median(contrast[lit]))


def prepare_exposure(shooter: Shooter, size: tuple[int, int]) -> Exposure:
    """Lock the camera where it is, then find a projection level that neither clips nor
    drowns in noise. Starts at START_LEVEL; goes brighter (up to full) only while the picture
    is faint, dimmer (down to MIN_LEVEL) while it clips.

    A camera that cannot be locked keeps its automatic exposure; the ratio images and the
    relative thresholds of the checks are what keep them working for it.
    """
    w, h = size
    controls = shooter.camera.set_controls(auto_exposure=False, auto_wb=False, autofocus=False)
    locked = bool(controls.get("auto_exposure", False))
    black_img = np.zeros((h, w), np.uint8)
    white_img = np.full((h, w), 255, np.uint8)
    black = shooter.shoot(black_img, "black")
    white = shooter.shoot(white_img, "white")
    found: tuple[np.ndarray, float, float] | None = None
    for _ in range(3):
        try:
            found = _lit_stats(black, white)
        except FkError:
            found = None
        faint = found is None or found[2] < GOOD_CONTRAST
        if not faint or shooter.level >= 1.0:
            break
        shooter.level = min(1.0, shooter.level + BRIGHTER_STEP)
        white = shooter.shoot(white_img, "white")
    if found is None:
        found = _lit_stats(black, white)  # raises CAMERA_NO_PROJECTION with its message
    lit, p99, contrast = found
    tries = 0
    while p99 >= CLIPPED_P99 and shooter.level > MIN_LEVEL + 1e-6 and tries < 3:
        shooter.level = max(MIN_LEVEL, shooter.level * DIM_FACTOR)
        white = shooter.shoot(white_img, "white")
        lit, p99, contrast = _lit_stats(black, white)
        tries += 1
    if contrast < MIN_CONTRAST:
        raise FkError(
            TOO_BRIGHT,
            "The projection is too faint compared with the light around it to check anything. "
            "Dim the room or wait until it is darker.",
        )
    return Exposure(
        black=black,
        white=white,
        locked=locked,
        level=shooter.level,
        p99_white=p99,
        median_contrast=contrast,
        lit=lit,
    )


def ambient_gain(shot: Shot, black: Shot, unlit: np.ndarray) -> float:
    """Brightness ratio of `shot` to `black` where neither is lit by the projector: the
    automatic exposure of an unlockable camera changes between shots. 1.0 when the scene is
    too dark to tell (and then the black shot hardly matters)."""
    sel = unlit & (black.gray > 20) & (black.gray < 235)
    if int(sel.sum()) < 2000:
        return 1.0
    ratio = float(np.median(shot.gray[sel]) / max(float(np.median(black.gray[sel])), 1.0))
    return float(np.clip(ratio, 0.5, 2.0))


def ratio_image(
    shot: Shot, exposure: Exposure, *, gain_mask: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """(shot - black) / (white - black), float32, and where it can be trusted.

    `gain_mask`: pixels known to be unlit in `shot`, used to correct the black level for an
    unlocked camera."""
    g = 1.0
    if not exposure.locked and gain_mask is not None:
        g = ambient_gain(shot, exposure.black, gain_mask)
    denom = exposure.white.gray - exposure.black.gray
    valid = denom >= RATIO_FLOOR
    q = (shot.gray - exposure.black.gray * np.float32(g)) / np.maximum(denom, RATIO_FLOOR)
    q = np.where(valid, q, 0.0).astype(np.float32)
    return q, valid
