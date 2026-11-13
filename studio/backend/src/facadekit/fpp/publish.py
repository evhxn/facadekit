"""Publish a rendered MP4 to a Falcon Player (FPP): check, upload, verify, playlist, start.

Values that spike S-1 has not confirmed are the constants below (the plan's values,
docs/05-interfaces.md section 7). They are only defaults: read them through the accessor
functions, which apply the user's ``config.json`` (see ``facadekit.config.load_overrides``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from facadekit.config import override_str
from facadekit.errors import FkError
from facadekit.fpp.client import FppClient

VIDEO_DIR = "videos"  # FPP media directory for video files [assumption; spike S-1]
SEQUENCES_DIR = "sequences"  # FPP directory for light sequence files (.fseq) [assumption]
MIN_FPP_MAJOR = 10
NAME_PREFIX = "fk_"
FALLBACK_STEM = "show"
ENTRY_TYPE_MEDIA = "media"  # playlist entry types [assumption; spike S-1]
ENTRY_TYPE_BOTH = "both"
ENTRY_VIDEO_OUT = "--Default--"

# Progress budget: (start, end) fractions of the whole publish.
_P_VERSION = (0.0, 0.02)
_P_UPLOAD = (0.02, 0.92)
_P_VERIFY = (0.92, 0.95)
_P_PLAYLIST = (0.95, 0.98)
_P_START = (0.98, 1.0)

_BAD_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MULTI_UNDERSCORE = re.compile(r"_+")
_ALNUM = re.compile(r"[A-Za-z0-9]")

ProgressFn = Callable[[float, str], None]


def video_dir() -> str:
    """FPP media directory for video files (config key ``fpp.video_dir``)."""
    return override_str("fpp.video_dir", VIDEO_DIR)


def sequences_dir() -> str:
    """FPP directory for .fseq files (config key ``fpp.sequences_dir``)."""
    return override_str("fpp.sequences_dir", SEQUENCES_DIR)


def sanitize_name(name: str) -> str:
    """Make a file name safe for FPP: [A-Za-z0-9._-] only, prefixed with ``fk_``."""
    cleaned = _MULTI_UNDERSCORE.sub("_", _BAD_CHARS.sub("_", name))
    stem, ext = os.path.splitext(cleaned)
    core = stem[len(NAME_PREFIX) :] if stem.startswith(NAME_PREFIX) else stem
    if _ALNUM.search(core) is None:
        return f"{NAME_PREFIX}{FALLBACK_STEM}{ext}"
    return cleaned if cleaned.startswith(NAME_PREFIX) else f"{NAME_PREFIX}{cleaned}"


def media_entry(media_name: str, sequence_name: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": (
            override_str("fpp.entry_type_media", ENTRY_TYPE_MEDIA)
            if sequence_name is None
            else override_str("fpp.entry_type_both", ENTRY_TYPE_BOTH)
        ),
        "enabled": 1,
        "playOnce": 0,
        "mediaName": media_name,
        "videoOut": override_str("fpp.video_out", ENTRY_VIDEO_OUT),
    }
    if sequence_name is not None:
        entry["sequenceName"] = sequence_name
    return entry


def probe_fpp(host: str) -> dict[str, Any]:
    """Check that FPP answers and is new enough. Never raises."""
    result: dict[str, Any] = {
        "reachable": False,
        "version": None,
        "major": None,
        "ok": False,
        "problems": [],
    }
    client: FppClient | None = None
    try:
        client = FppClient(host)
        version = client.version()
    except FkError as exc:
        if exc.code == "FPP_BAD_RESPONSE":
            result["reachable"] = True
            result["problems"] = [f"FPP at {host} gave an unexpected answer: {exc.message}"]
        else:
            result["problems"] = [
                f"Could not reach FPP at {host}. "
                "Check the address and that the Pi is on the same network."
            ]
        return result
    finally:
        if client is not None:
            client.close()
    result.update(reachable=True, version=version.version, major=version.major)
    if version.major < MIN_FPP_MAJOR:
        result["problems"] = [
            f"FPP {version.version} is too old. Update FPP to version {MIN_FPP_MAJOR} or newer."
        ]
    else:
        result["ok"] = True
    return result


def _report(progress: ProgressFn | None, span: tuple[float, float], frac: float, msg: str) -> None:
    if progress is not None:
        progress(span[0] + (span[1] - span[0]) * min(max(frac, 0.0), 1.0), msg)


def publish_render(
    host: str,
    local_path: Path,
    *,
    media_name: str,
    playlist_name: str,
    start: bool,
    sequence_name: str | None = None,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if not local_path.is_file():
        raise FkError("NOT_FOUND", f"Rendered file not found: {local_path}")
    if not playlist_name.strip():
        raise FkError("BAD_REQUEST", "Playlist name is empty")
    media_name = sanitize_name(media_name)
    target_dir = video_dir()
    client = FppClient(host)
    try:
        _report(progress, _P_VERSION, 0.0, "Checking FPP")
        version = client.version()
        if version.major < MIN_FPP_MAJOR:
            raise FkError(
                "FPP_TOO_OLD",
                f"FPP {version.version} found. FacadeKit needs FPP {MIN_FPP_MAJOR} or newer.",
            )
        _report(progress, _P_VERSION, 1.0, "Checking FPP")

        _report(progress, _P_UPLOAD, 0.0, "Uploading")
        client.upload(
            target_dir,
            local_path,
            media_name,
            progress=lambda frac, _msg: _report(progress, _P_UPLOAD, frac, "Uploading"),
            cancel=cancel,
        )

        _report(progress, _P_VERIFY, 0.0, "Verifying")
        if media_name not in client.list_files(target_dir):
            raise FkError("FPP_UPLOAD_FAILED", "Upload finished but FPP does not list the file.")
        _report(progress, _P_VERIFY, 1.0, "Verifying")

        _report(progress, _P_PLAYLIST, 0.0, "Creating playlist")
        client.put_playlist(playlist_name, [media_entry(media_name, sequence_name)])
        _report(progress, _P_PLAYLIST, 1.0, "Creating playlist")

        if start:
            _report(progress, _P_START, 0.0, "Starting")
            client.start_playlist(playlist_name)
        _report(progress, _P_START, 1.0, "Starting" if start else "Done")
    finally:
        client.close()
    return {"media_name": media_name, "playlist_name": playlist_name, "started": start}
