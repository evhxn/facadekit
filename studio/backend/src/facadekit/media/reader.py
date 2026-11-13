from __future__ import annotations

import math
import subprocess
from pathlib import Path
from types import TracebackType

import cv2
import numpy as np

from facadekit.media.ffmpeg import ffmpeg_exe, local_input
from facadekit.models.project import Asset


class VideoReader:
    """Decode raw BGR frames from a video through an ffmpeg pipe at a fixed fps."""

    def __init__(self, path: Path, *, fps: int, width: int, height: int, start_ms: int = 0) -> None:
        self._width = width
        self._height = height
        self._frame_bytes = width * height * 3
        self._closed = False
        self._proc = subprocess.Popen(
            [
                ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-ss",
                f"{start_ms / 1000:.3f}",
                *local_input(path),
                "-an",
                "-vf",
                f"fps={fps},scale={width}:{height}",
                "-pix_fmt",
                "bgr24",
                "-f",
                "rawvideo",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read(self) -> np.ndarray | None:
        """Return the next (height, width, 3) uint8 frame, or None at the end."""
        if self._closed or self._proc.stdout is None:
            return None
        buf = bytearray()
        while len(buf) < self._frame_bytes:
            chunk = self._proc.stdout.read(self._frame_bytes - len(buf))
            if not chunk:
                return None
            buf += chunk
        frame: np.ndarray = (
            np.frombuffer(bytes(buf), dtype=np.uint8).reshape(self._height, self._width, 3).copy()
        )
        return frame

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc.stdout is not None:
            self._proc.stdout.close()
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()

    def __enter__(self) -> VideoReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _even_dim(value: float) -> int:
    return max(2, round(value / 2) * 2)


class ClipSource:
    """Frame source for one asset, optimised for increasing times.

    Frames returned by `frame_at` are shared: callers must not modify them in place.
    """

    def __init__(self, asset: Asset, path: Path, *, fps: int, max_size: tuple[int, int]) -> None:
        self._asset = asset
        self._path = path
        self._fps = fps
        src_w = asset.width or max_size[0]
        src_h = asset.height or max_size[1]
        scale = min(1.0, max_size[0] / src_w, max_size[1] / src_h)
        self.size: tuple[int, int] = (_even_dim(src_w * scale), _even_dim(src_h * scale))
        self._reader: VideoReader | None = None
        self._index = -1
        self._last: np.ndarray | None = None
        self._image: np.ndarray | None = None
        if asset.kind == "image":
            self._image = self._load_image(path)

    def _load_image(self, path: Path) -> np.ndarray:
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            return np.zeros((self.size[1], self.size[0], 3), dtype=np.uint8)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        out: np.ndarray = cv2.resize(img, self.size, interpolation=cv2.INTER_AREA)
        return out

    def frame_at(self, source_ms: int) -> np.ndarray:
        """BGR (H, W, 3), or BGRA (H, W, 4) for images with alpha. Do not modify in place."""
        if self._image is not None:
            return self._image
        fps = self._fps
        target = source_ms * fps // 1000
        duration_ms = self._asset.duration_ms or 0
        last_index = max(0, math.ceil(duration_ms * fps / 1000) - 1)
        target = min(target, last_index)
        if self._reader is None or target < self._index or target > self._index + 2 * fps:
            if self._reader is not None:
                self._reader.close()
            self._reader = VideoReader(
                self._path,
                fps=fps,
                width=self.size[0],
                height=self.size[1],
                start_ms=target * 1000 // fps,
            )
            self._index = target - 1
        while self._index < target:
            f = self._reader.read()
            if f is None:
                break
            self._last = f
            self._index += 1
        if self._last is None:
            return np.zeros((self.size[1], self.size[0], 3), dtype=np.uint8)
        return self._last

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None
