from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from facadekit.models.project import Project
from facadekit.render.pipeline import CancelFn, ProgressFn, encode_frames, frame_count
from facadekit.render.presets import get_preset, output_size
from facadekit.store.project_store import load_project_dir

_WHITE = (255, 255, 255)
_YELLOW = (0, 255, 255)


def outline_frame(
    project: Project, *, selected_id: str | None = None, fill: float = 0.0
) -> np.ndarray:
    """Black W x H BGR frame with every surface quad outlined."""
    width, height = project.projector.width, project.projector.height
    img = np.zeros((height, width, 3), np.uint8)
    polys = [
        (s, np.array([[round(x), round(y)] for x, y in s.quad], np.int32).reshape(-1, 1, 2))
        for s in project.surfaces
    ]
    if fill > 0:
        level = round(255 * fill)
        for _, poly in polys:
            cv2.fillPoly(img, [poly], (level, level, level))
    for surface, poly in polys:
        color = _YELLOW if surface.id == selected_id else _WHITE
        cv2.polylines(img, [poly], isClosed=True, color=color, thickness=3, lineType=cv2.LINE_AA)
        cv2.circle(img, (int(poly[0, 0, 0]), int(poly[0, 0, 1])), 8, color, -1, cv2.LINE_AA)
        cx = sum(p[0] for p in surface.quad) / 4
        cy = sum(p[1] for p in surface.quad) / 4
        (tw, th), _ = cv2.getTextSize(surface.name, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
        cv2.putText(
            img, surface.name, (round(cx - tw / 2), round(cy + th / 2)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.8, _WHITE, 2, cv2.LINE_AA,
        )  # fmt: skip
    return img


def render_alignment_check(
    project_dir: Path,
    out_path: Path,
    *,
    seconds: int = 20,
    preset_id: str = "pi3-1080p30",
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> dict[str, Any]:
    project = load_project_dir(project_dir)
    preset = get_preset(preset_id)
    proj_w, proj_h = project.projector.width, project.projector.height
    width, height = output_size(preset, proj_w, proj_h)
    fps = project.projector.fps
    total = frame_count(seconds * 1000, fps)
    plain = outline_frame(project, fill=0.0)
    filled = outline_frame(project, fill=0.1)

    def frames() -> Iterator[np.ndarray]:
        for n in range(total):
            yield filled if (n * 2 // fps) % 2 == 1 else plain

    return encode_frames(
        out_path, frames(), total=total, width=width, height=height, fps=fps,
        preset=preset, audio_path=None, progress=progress, cancel=cancel,
    )  # fmt: skip
