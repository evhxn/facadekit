from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import IO

import numpy as np

from facadekit.errors import BAD_IMAGE_SIZE, BAD_REQUEST, FFMPEG_FAILED, FkError
from facadekit.media.ffmpeg import ffmpeg_exe, local_input
from facadekit.render.presets import Preset

# Convert BGR to YUV with the BT.709 matrix (limited range) and tag the stream as BT.709, so
# players that assume BT.709 for HD show the intended colours. Whether the Pi decoder honours
# these tags is unverified until hardware spike S-2 is run.
COLOR_ARGS: list[str] = [
    "-vf", "scale=in_range=pc:out_range=tv:out_color_matrix=bt709",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    "-color_range", "tv",
]  # fmt: skip


class Encoder:
    """Pipes raw BGR frames into ffmpeg, producing an H.264 + AAC MP4."""

    def __init__(
        self,
        out_path: Path,
        *,
        width: int,
        height: int,
        fps: int,
        preset: Preset,
        audio_path: Path | None,
        audio_offset_ms: int = 0,
        duration_ms: int,
    ) -> None:
        assert width % 2 == 0 and height % 2 == 0, "yuv420p needs even dimensions"
        if preset.id.startswith("pi3-") and (fps > 30 or width * height > 1920 * 1088):
            raise FkError(BAD_REQUEST, "Preset pi3 supports at most 1080p30")
        self._out_path = out_path
        self._width = width
        self._height = height
        self._closed = False

        cmd = [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
            "-r", str(fps), "-i", "-",
        ]  # fmt: skip
        if audio_path is not None:
            cmd += ["-ss", f"{audio_offset_ms / 1000}", *local_input(audio_path)]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        cmd += COLOR_ARGS + list(preset.video_args) + list(preset.audio_args)
        cmd += ["-t", f"{duration_ms / 1000:.3f}", "-movflags", "+faststart", str(out_path)]

        self._stderr: IO[bytes] = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._stderr
            )
        except OSError as exc:
            self._stderr.close()
            raise FkError(FFMPEG_FAILED, f"could not run ffmpeg: {exc}") from exc

    def _stderr_tail(self) -> str:
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            data = self._stderr.read()
        except (OSError, ValueError):
            return ""
        return data.decode("utf-8", errors="replace").strip()[-500:]

    def write(self, frame: np.ndarray) -> None:
        if frame.shape != (self._height, self._width, 3) or frame.dtype != np.uint8:
            raise FkError(
                BAD_IMAGE_SIZE,
                f"Expected uint8 frame of shape {(self._height, self._width, 3)}, "
                f"got {frame.dtype} {frame.shape}",
            )
        stdin = self._proc.stdin
        assert stdin is not None
        try:
            stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            self._proc.wait()
            tail = self._stderr_tail()
            self._cleanup()
            raise FkError(FFMPEG_FAILED, tail) from None

    def _cleanup(self) -> None:
        self._closed = True
        self._stderr.close()

    def close(self) -> None:
        if self._closed:
            return
        stdin = self._proc.stdin
        assert stdin is not None
        try:
            stdin.close()
        except (BrokenPipeError, OSError):
            pass
        code = self._proc.wait()
        tail = self._stderr_tail()
        self._cleanup()
        if code != 0:
            raise FkError(FFMPEG_FAILED, tail)

    def abort(self) -> None:
        if not self._closed:
            self._proc.kill()
            self._proc.wait()
            if self._proc.stdin is not None:
                try:
                    self._proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            self._cleanup()
        self._out_path.unlink(missing_ok=True)
