from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg

from facadekit.errors import FFMPEG_FAILED, FkError


def ffmpeg_exe() -> str:
    exe: str = imageio_ffmpeg.get_ffmpeg_exe()
    return exe


def _tail(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()[-500:]


def run_ffmpeg(args: list[str], *, timeout: float | None = None) -> None:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        raise FkError(FFMPEG_FAILED, f"ffmpeg timed out after {timeout}s. {_tail(stderr)}") from exc
    except OSError as exc:
        raise FkError(FFMPEG_FAILED, f"could not run ffmpeg: {exc}") from exc
    if proc.returncode != 0:
        raise FkError(FFMPEG_FAILED, _tail(proc.stderr))


def ffmpeg_info(path: Path) -> str:
    """Return the stderr of `ffmpeg -i <path>` (exits non-zero by design)."""
    try:
        proc = subprocess.run(
            [
                ffmpeg_exe(),
                "-hide_banner",
                "-nostdin",
                "-protocol_whitelist",
                "file",
                "-i",
                f"file:{path.resolve()}",
            ],
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FkError(FFMPEG_FAILED, f"could not inspect {path.name}: {exc}") from exc
    return proc.stderr.decode("utf-8", errors="replace")
