from __future__ import annotations

import re
from pathlib import Path

import cv2

from facadekit.errors import BAD_REQUEST, UNSUPPORTED_MEDIA, FkError
from facadekit.media.ffmpeg import ffmpeg_info, local_input, run_ffmpeg
from facadekit.models.project import Asset

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}

THUMB_WIDTH = 320
_UNSUPPORTED_MSG = "Not a video, image or audio file ffmpeg can read"

# Only ffmpeg's own lines, at its own indentation, count. Container metadata is printed at
# 4+ spaces, so a title/comment such as "Stream #0:0: Video: h264, 640x360" or
# "Duration: 00:05:00" cannot match.
_DURATION_RE = re.compile(r"^  Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", re.MULTILINE)
_VIDEO_LINE_RE = re.compile(r"^  Stream #\d+:\d+\S*: Video:")
_AUDIO_LINE_RE = re.compile(r"^  Stream #\d+:\d+\S*: Audio:", re.MULTILINE)
_SIZE_RE = re.compile(r"(?<![\w.])(\d{2,5})x(\d{2,5})(?![\w.])")
_FPS_RE = re.compile(r"([\d.]+) fps")
_TBR_RE = re.compile(r"([\d.]+) tbr")
_ROTATION_RE = re.compile(
    r"^      (?:Display Matrix|displaymatrix): rotation of (-?[\d.]+) degrees"
    r"|^    rotate\s*:\s*(-?[\d.]+)",
    re.MULTILINE,
)


def _unsupported() -> FkError:
    return FkError(UNSUPPORTED_MEDIA, _UNSUPPORTED_MSG)


def _parse_duration_ms(text: str) -> int | None:
    m = _DURATION_RE.search(text)
    if m is None:
        return None
    seconds = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return round(seconds * 1000)


def _probe_image(path: Path) -> Asset:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise _unsupported()
    height, width = img.shape[:2]
    return Asset(
        id="",
        kind="image",
        filename="",
        original_name=path.name,
        width=int(width),
        height=int(height),
        duration_ms=None,
        fps=None,
        has_alpha=bool(img.ndim == 3 and img.shape[2] == 4),
    )


def _probe_audio(path: Path) -> Asset:
    duration = _parse_duration_ms(ffmpeg_info(path))
    if duration is None:
        raise _unsupported()
    return Asset(
        id="",
        kind="audio",
        filename="",
        original_name=path.name,
        width=None,
        height=None,
        duration_ms=duration,
        fps=None,
        has_audio=True,
    )


def _probe_video(path: Path) -> Asset:
    text = ffmpeg_info(path)
    lines = text.splitlines()
    video_idx = next((i for i, ln in enumerate(lines) if _VIDEO_LINE_RE.match(ln)), None)
    if video_idx is None:
        raise _unsupported()
    video_line = lines[video_idx]
    size = _SIZE_RE.search(video_line)
    if size is None:
        raise _unsupported()
    width, height = int(size.group(1)), int(size.group(2))
    fps_m = _FPS_RE.search(video_line) or _TBR_RE.search(video_line)
    fps = float(fps_m.group(1)) if fps_m else None

    duration = _parse_duration_ms(text)
    if duration is None:
        raise _unsupported()

    # Rotation info lives in the stream's own indented block (metadata / side data).
    block: list[str] = []
    for ln in lines[video_idx + 1 :]:
        if not ln.startswith("    "):
            break
        block.append(ln)
    rot = _ROTATION_RE.search("\n".join(block))
    if rot is not None:
        angle = float(rot.group(1) or rot.group(2))
        if round(abs(angle)) % 180 == 90:
            width, height = height, width

    return Asset(
        id="",
        kind="video",
        filename="",
        original_name=path.name,
        width=width,
        height=height,
        duration_ms=duration,
        fps=fps,
        has_audio=_AUDIO_LINE_RE.search(text) is not None,
    )


def probe(path: Path) -> Asset:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return _probe_image(path)
    if ext in AUDIO_EXTS:
        return _probe_audio(path)
    return _probe_video(path)


def make_thumbnail(path: Path, kind: str, out_path: Path) -> None:
    if kind == "audio":
        raise FkError(BAD_REQUEST, "Audio files have no thumbnail")
    if kind == "image":
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise _unsupported()
        if img.ndim == 3 and img.shape[2] == 4:
            img = img[:, :, :3]
        elif img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.dtype != "uint8":
            img = cv2.convertScaleAbs(img, alpha=255.0 / 65535.0 if img.dtype == "uint16" else 1.0)
        h, w = img.shape[:2]
        new_h = max(1, round(h * THUMB_WIDTH / w))
        small = cv2.resize(img, (THUMB_WIDTH, new_h), interpolation=cv2.INTER_AREA)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out_path), small, [cv2.IMWRITE_JPEG_QUALITY, 80]):
            raise FkError(BAD_REQUEST, f"could not write thumbnail {out_path.name}")
        return
    if kind != "video":
        raise FkError(BAD_REQUEST, f"Unknown media kind: {kind}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for seek in ("1", "0"):
        out_path.unlink(missing_ok=True)
        try:
            run_ffmpeg(
                [
                    "-y",
                    "-ss",
                    seek,
                    *local_input(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={THUMB_WIDTH}:-2",
                    str(out_path),
                ]
            )
        except FkError:
            if seek == "0":
                raise
            continue
        if out_path.exists() and out_path.stat().st_size > 0:
            return
    raise FkError("FFMPEG_FAILED", "ffmpeg produced no thumbnail")
