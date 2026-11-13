from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from facadekit.errors import BAD_REQUEST, FkError

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


# --- user overrides (<data dir>/config.json) ------------------------------------------------
#
# Values no hardware spike has confirmed yet (FPP directory names and playlist entry fields,
# spike S-1; encoder settings, spike S-2) can be corrected here without editing code.

CONFIG_FILE = "config.json"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STRING_KEYS = {
    "fpp.video_dir": True,  # True: used as a URL path segment, must be a safe name
    "fpp.sequences_dir": True,
    "fpp.video_out": False,
    "fpp.entry_type_media": False,
    "fpp.entry_type_both": False,
    "render.default_preset": False,
}
_PRESET_KEY = re.compile(r"^render\.presets\.([A-Za-z0-9._-]+)\.video_args$")
_warned: set[str] = set()


def _warn_once(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        log.warning("%s", message)


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{name}."))
        else:
            out[name] = value
    return out


def load_overrides() -> dict[str, Any]:
    """Recognised, well-formed overrides from ``<data dir>/config.json`` as dotted keys.

    No file gives ``{}``. Invalid JSON raises ``BAD_REQUEST``. Unknown keys and values of the
    wrong type are ignored with one warning each. The file is read on every call (it is tiny),
    so an edit takes effect without a restart.
    """
    path = data_dir() / CONFIG_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise FkError(BAD_REQUEST, f"{CONFIG_FILE} cannot be read: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise FkError(BAD_REQUEST, f"{CONFIG_FILE} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise FkError(BAD_REQUEST, f"{CONFIG_FILE} must contain a JSON object")
    out: dict[str, Any] = {}
    for key, value in _flatten(data).items():
        if key in _STRING_KEYS:
            ok = isinstance(value, str) and bool(value.strip())
            if ok and _STRING_KEYS[key]:
                ok = _SAFE_NAME.fullmatch(value) is not None
        elif _PRESET_KEY.fullmatch(key):
            ok = (
                isinstance(value, list)
                and bool(value)
                and all(isinstance(item, str) and item for item in value)
            )
        else:
            _warn_once(f"{CONFIG_FILE}: unknown setting {key!r} ignored")
            continue
        if ok:
            out[key] = value
        else:
            _warn_once(f"{CONFIG_FILE}: {key!r} has an unusable value and is ignored")
    return out


def override_str(key: str, default: str) -> str:
    value = load_overrides().get(key)
    return value if isinstance(value, str) else default
