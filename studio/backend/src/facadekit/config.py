from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)


def data_dir() -> Path:
    """Return the data directory, creating it with projects/ and models/ if missing.

    Reads FACADEKIT_DATA on every call (no caching); defaults to ~/FacadeKit.
    """
    env = os.environ.get("FACADEKIT_DATA")
    path = Path(env).expanduser() if env else Path.home() / "FacadeKit"
    path.mkdir(parents=True, exist_ok=True)
    (path / "projects").mkdir(exist_ok=True)
    (path / "models").mkdir(exist_ok=True)
    return path


def projects_dir() -> Path:
    return data_dir() / "projects"


def models_dir() -> Path:
    return data_dir() / "models"


DEFAULT_CAMERA_SIZE = (1280, 720)


def camera_size() -> tuple[int, int]:
    """Webcam capture size requested from OpenCV: FACADEKIT_CAMERA_SIZE as "WIDTHxHEIGHT".

    Default 1280x720. 1920x1080 is opt-in (unverified on real webcams, spike S-3 extension).
    An invalid value logs a warning and falls back to the default.
    """
    raw = os.environ.get("FACADEKIT_CAMERA_SIZE")
    if raw is None or not raw.strip():
        return DEFAULT_CAMERA_SIZE
    match = re.fullmatch(r"(\d{2,5})[xX](\d{2,5})", raw.strip())
    if match is None or int(match[1]) < 64 or int(match[2]) < 64:
        log.warning(
            "FACADEKIT_CAMERA_SIZE=%r is not WIDTHxHEIGHT; using %dx%d", raw, *DEFAULT_CAMERA_SIZE
        )
        return DEFAULT_CAMERA_SIZE
    return int(match[1]), int(match[2])
