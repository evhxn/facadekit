from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import cv2
import numpy as np

from facadekit.errors import FkError
from facadekit.geom.homography import canvas_surface, clip_homography
from facadekit.geom.mask import rasterize_mask
from facadekit.media.reader import ClipSource
from facadekit.models.project import Asset, Clip, Mask, Project, Surface


@dataclass
class _SurfaceCache:
    surface: Surface
    mask: np.ndarray  # float32 (H, W)
    box: tuple[int, int, int, int]  # x, y, w, h of mask > 0 (w or h == 0 if empty)


def _scale_surface(surface: Surface, scale: float) -> Surface:
    def pt(p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] * scale, p[1] * scale)

    mask = surface.mask
    scaled_mask = (
        None
        if mask is None
        else Mask(
            outer=[pt(p) for p in mask.outer],
            holes=[[pt(p) for p in hole] for hole in mask.holes],
        )
    )
    quad = surface.quad
    return surface.model_copy(
        update={
            "quad": (pt(quad[0]), pt(quad[1]), pt(quad[2]), pt(quad[3])),
            "mask": scaled_mask,
            "feather_px": surface.feather_px * scale,
        }
    )


def _fade(clip: Clip, t_ms: int) -> float:
    fade = 1.0
    if clip.fade_in_ms > 0:
        fade = min(1.0, (t_ms - clip.start_ms) / clip.fade_in_ms)
    if clip.fade_out_ms > 0:
        fade *= min(1.0, (clip.start_ms + clip.duration_ms - t_ms) / clip.fade_out_ms)
    return fade


class Compositor:
    """Authoritative frame renderer: docs/02-architecture.md section 6.2."""

    def __init__(self, project: Project, project_dir: Path, *, scale: float = 1.0) -> None:
        self._project = project
        self._dir = project_dir
        self.size: tuple[int, int] = (
            round(project.projector.width * scale),
            round(project.projector.height * scale),
        )
        self._assets = {a.id: a for a in project.assets}
        self._sources: dict[str, ClipSource] = {}
        width, height = self.size
        self._surfaces: dict[str, _SurfaceCache] = {}
        for base in [*project.surfaces, canvas_surface(project.projector)]:
            surface = _scale_surface(base, scale)
            outer = surface.mask.outer if surface.mask is not None else list(surface.quad)
            holes = surface.mask.holes if surface.mask is not None else []
            mask = rasterize_mask(width, height, outer, holes, surface.feather_px)
            bx, by, bw, bh = cv2.boundingRect((mask > 0).astype(np.uint8))
            box = (int(bx), int(by), int(bw), int(bh))
            self._surfaces[base.id] = _SurfaceCache(surface, mask, box)

    def _source(self, clip: Clip, asset: Asset) -> ClipSource:
        source = self._sources.get(clip.id)
        if source is None:
            path = self._dir / asset.filename
            if not path.is_file():
                raise FkError("NOT_FOUND", f"Media file missing: {asset.original_name}")
            source = ClipSource(asset, path, fps=self._project.projector.fps, max_size=self.size)
            self._sources[clip.id] = source
        return source

    def frame(self, t_ms: int) -> np.ndarray:
        """Render the BGR uint8 frame (H_out, W_out, 3) at `t_ms`."""
        width, height = self.size
        dst = np.zeros((height, width, 3), dtype=np.float32)
        for track in self._project.timeline.tracks:
            for clip in track.clips:
                if clip.start_ms <= t_ms < clip.start_ms + clip.duration_ms:
                    self._draw_clip(dst, clip, t_ms)
        out: np.ndarray = np.clip(np.rint(dst * 255.0), 0, 255).astype(np.uint8)
        return out

    def _draw_clip(self, dst: np.ndarray, clip: Clip, t_ms: int) -> None:
        if clip.generator is not None or clip.asset_id is None:
            return
        asset = self._assets[clip.asset_id]
        source = self._source(clip, asset)
        cache = self._surfaces[clip.surface_id]
        bx, by, bw, bh = cache.box
        if bw == 0 or bh == 0:
            return

        s = clip.in_offset_ms + (t_ms - clip.start_ms)
        if clip.loop and asset.duration_ms:
            s %= asset.duration_ms
        img = source.frame_at(s)
        src_w, src_h = source.size
        h_mat, src_rect = clip_homography(src_w, src_h, cache.surface, clip.fit)

        # Alpha plane in source space: src_rect region, times source alpha if present.
        x0, y0, x1, y1 = (int(round(v)) for v in src_rect)
        rect = np.zeros((img.shape[0], img.shape[1]), dtype=np.uint8)
        rect[y0:y1, x0:x1] = 255
        if img.shape[2] == 4:
            rect = (rect.astype(np.uint16) * img[:, :, 3] // 255).astype(np.uint8)
            color = np.ascontiguousarray(img[:, :, :3])
        else:
            color = img

        shift = np.array([[1.0, 0.0, -bx], [0.0, 1.0, -by], [0.0, 0.0, 1.0]])
        h_box = shift @ h_mat
        flags = dict(flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        warped = cv2.warpPerspective(color, h_box, (bw, bh), **flags)  # type: ignore[call-overload]
        warped_a = cv2.warpPerspective(rect, h_box, (bw, bh), **flags)  # type: ignore[call-overload]

        alpha = (
            warped_a.astype(np.float32)
            * np.float32(1.0 / 255.0)
            * cache.mask[by : by + bh, bx : bx + bw]
            * np.float32(clip.opacity * _fade(clip, t_ms))
        )
        a3 = alpha[:, :, None]
        src_f = warped.astype(np.float32) * np.float32(1.0 / 255.0)
        region = dst[by : by + bh, bx : bx + bw]
        if clip.blend == "normal":
            region[:] = src_f * a3 + region * (1.0 - a3)
        elif clip.blend == "add":
            region[:] = np.minimum(1.0, region + src_f * a3)
        else:  # screen
            region[:] = 1.0 - (1.0 - region) * (1.0 - src_f * a3)

    def close(self) -> None:
        for source in self._sources.values():
            source.close()
        self._sources.clear()

    def __enter__(self) -> Compositor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
