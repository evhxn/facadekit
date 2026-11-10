from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from facadekit.errors import CANCELLED, FkError
from facadekit.render.compositor import Compositor
from facadekit.render.encoder import Encoder
from facadekit.render.presets import Preset, get_preset, output_size
from facadekit.store.project_store import load_project_dir

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]

_PROGRESS_INTERVAL_S = 0.25


def frame_count(duration_ms: int, fps: int) -> int:
    return math.ceil(duration_ms * fps / 1000)


def encode_frames(
    out_path: Path,
    frames: Iterator[np.ndarray],
    *,
    total: int,
    width: int,
    height: int,
    fps: int,
    preset: Preset,
    audio_path: Path | None,
    progress: ProgressFn | None,
    cancel: CancelFn | None,
) -> dict[str, Any]:
    """Encode `total` frames from `frames` to `out_path` via a `.part.mp4` file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_suffix(".part.mp4")
    duration_ms = total * 1000 // fps
    encoder = Encoder(
        part, width=width, height=height, fps=fps, preset=preset,
        audio_path=audio_path, duration_ms=duration_ms,
    )  # fmt: skip
    try:
        last_report = time.monotonic()
        for n in range(total):
            if cancel is not None and cancel():
                encoder.abort()
                raise FkError(CANCELLED, "Render cancelled")
            frame = next(frames)
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            encoder.write(frame)
            now = time.monotonic()
            if progress is not None and now - last_report >= _PROGRESS_INTERVAL_S:
                last_report = now
                progress((n + 1) / total, f"Frame {n + 1} of {total}")
        encoder.close()
        os.replace(part, out_path)
    except BaseException:
        encoder.abort()
        part.unlink(missing_ok=True)
        raise
    if progress is not None:
        progress(1.0, f"Frame {total} of {total}")
    return {
        "filename": out_path.name,
        "size_bytes": out_path.stat().st_size,
        "duration_ms": duration_ms,
        "frames": total,
    }


def render_project(
    project_dir: Path,
    preset_id: str,
    out_path: Path,
    *,
    include_audio: bool = True,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> dict[str, Any]:
    project = load_project_dir(project_dir)
    preset = get_preset(preset_id)
    proj_w, proj_h = project.projector.width, project.projector.height
    width, height = output_size(preset, proj_w, proj_h)
    fps = project.projector.fps
    total = frame_count(project.timeline.duration_ms, fps)

    audio_path: Path | None = None
    audio_id = project.timeline.audio_asset_id
    if include_audio and audio_id is not None:
        asset = next(a for a in project.assets if a.id == audio_id)
        audio_path = project_dir / asset.filename

    compositor = Compositor(project, project_dir, scale=width / proj_w)
    try:
        frames = (compositor.frame(n * 1000 // fps) for n in range(total))
        return encode_frames(
            out_path, frames, total=total, width=width, height=height, fps=fps,
            preset=preset, audio_path=audio_path, progress=progress, cancel=cancel,
        )  # fmt: skip
    finally:
        compositor.close()
