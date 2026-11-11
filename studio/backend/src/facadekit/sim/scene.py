from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import cv2
import numpy as np

Point = tuple[float, float]

# Homographies here use pixel-corner coordinates ((0, 0) is the top-left corner of the
# top-left pixel). cv2.warpPerspective treats integer coordinates as pixel centres, so
# conjugate with a half-pixel shift before calling it.
_HALF_IN = np.array([[1.0, 0.0, 0.5], [0.0, 1.0, 0.5], [0.0, 0.0, 1.0]])
_HALF_OUT = np.array([[1.0, 0.0, -0.5], [0.0, 1.0, -0.5], [0.0, 0.0, 1.0]])


def _cv_warp_matrix(h: np.ndarray) -> np.ndarray:
    return np.asarray(_HALF_OUT @ h @ _HALF_IN, dtype=np.float64)


@dataclass
class SimScene:
    world_size: tuple[int, int] = (1600, 900)
    proj_size: tuple[int, int] = (1920, 1080)
    cam_size: tuple[int, int] = (1280, 720)
    proj_corners_world: tuple[Point, Point, Point, Point] = (
        (150, 80),
        (1480, 40),
        (1520, 860),
        (110, 820),
    )
    world_corners_cam: tuple[Point, Point, Point, Point] = (
        (90, 60),
        (1200, 95),
        (1180, 660),
        (70, 640),
    )
    features_world: dict[str, tuple[float, float, float, float]] = field(
        default_factory=lambda: {
            "garage_door": (500, 480, 1100, 800),
            "window_left": (250, 200, 420, 380),
            "window_right": (1180, 200, 1350, 380),
        }
    )
    occluders_world: list[tuple[float, float, float, float]] = field(default_factory=list)
    ambient: float = 0.05
    gain: float = 0.85
    gamma: float = 2.2
    blur_sigma: float = 1.0
    noise_sigma: float = 2.0
    seed: int = 0
    _albedo_f32: np.ndarray | None = field(default=None, init=False, repr=False, compare=False)
    _albedo_u8: np.ndarray | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def h_p2w(self) -> np.ndarray:
        w, h = self.proj_size
        src = np.array([(0, 0), (w, 0), (w, h), (0, h)], dtype=np.float32)
        dst = np.array(self.proj_corners_world, dtype=np.float32)
        return np.asarray(cv2.getPerspectiveTransform(src, dst), dtype=np.float64)

    @property
    def h_w2c(self) -> np.ndarray:
        w, h = self.world_size
        src = np.array([(0, 0), (w, 0), (w, h), (0, h)], dtype=np.float32)
        dst = np.array(self.world_corners_cam, dtype=np.float32)
        return np.asarray(cv2.getPerspectiveTransform(src, dst), dtype=np.float64)

    @property
    def h_p2c(self) -> np.ndarray:
        return np.asarray(self.h_w2c @ self.h_p2w, dtype=np.float64)

    def albedo(self) -> np.ndarray:
        if self._albedo_u8 is None:
            self._albedo_u8 = self._build_albedo()
        return self._albedo_u8

    def _albedo_float(self) -> np.ndarray:
        if self._albedo_f32 is None:
            self._albedo_f32 = self.albedo().astype(np.float32) / np.float32(255.0)
        return self._albedo_f32

    def _build_albedo(self) -> np.ndarray:
        ww, wh = self.world_size
        img = np.empty((wh, ww, 3), dtype=np.uint8)
        img[:] = (60, 70, 150)
        mortar = (120, 120, 120)
        for y in range(0, wh, 24):
            img[y : y + 2, :] = mortar
        for row, y in enumerate(range(0, wh, 24)):
            offset = 30 if row % 2 else 0
            for x in range(offset, ww, 60):
                img[y : y + 24, x : x + 2] = mortar
        for name, (x0, y0, x1, y1) in self.features_world.items():
            ix0, iy0, ix1, iy1 = (int(round(v)) for v in (x0, y0, x1, y1))
            # The 6 px dark frame sits just outside the feature rectangle, so the light fill
            # covers exactly the rectangle that feature_quad_proj() reports.
            img[max(iy0 - 6, 0) : iy1 + 6, max(ix0 - 6, 0) : ix1 + 6] = (30, 30, 30)
            fill = (200, 200, 200) if name == "garage_door" else (90, 60, 40)
            img[iy0:iy1, ix0:ix1] = fill
        return img

    def feature_quad_proj(self, name: str) -> list[Point]:
        x0, y0, x1, y1 = self.features_world[name]
        pts = np.array([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]], dtype=np.float64)
        out = cv2.perspectiveTransform(pts, np.linalg.inv(self.h_p2w))[0]
        return [(float(p[0]), float(p[1])) for p in out]

    def render_camera(self, proj_image: np.ndarray | None, rng: np.random.Generator) -> np.ndarray:
        ww, wh = self.world_size
        light: np.ndarray
        if proj_image is None:
            light = np.zeros((wh, ww, 3), dtype=np.float32)
        else:
            lin = np.power(proj_image.astype(np.float32) / 255.0, np.float32(self.gamma))
            light = cv2.warpPerspective(
                lin, _cv_warp_matrix(self.h_p2w), (ww, wh), flags=cv2.INTER_LINEAR
            )
            if light.ndim == 2:
                light = np.repeat(light[:, :, None], 3, axis=2)
        for ox0, oy0, ox1, oy1 in self.occluders_world:
            x0, y0 = max(int(round(ox0)), 0), max(int(round(oy0)), 0)
            light[y0 : max(int(round(oy1)), 0), x0 : max(int(round(ox1)), 0)] = 0
        radiance = self._albedo_float() * (self.ambient + self.gain * light)
        cam = cv2.warpPerspective(
            radiance.astype(np.float32),
            _cv_warp_matrix(self.h_w2c),
            self.cam_size,
            flags=cv2.INTER_LINEAR,
        )
        if self.blur_sigma > 0:
            cam = cv2.GaussianBlur(cam, (0, 0), self.blur_sigma)
        noise = rng.normal(0, self.noise_sigma, cam.shape)
        return np.asarray(np.clip(cam * 255 + noise, 0, 255).astype(np.uint8))


def default_scene() -> SimScene:
    return SimScene()


def _scale_pt(p: Point, f: float) -> Point:
    return (float(p[0] * f), float(p[1] * f))


def scale_world(scene: SimScene, f: float) -> SimScene:
    """Scene with a world plane `f` times larger. h_p2c is unchanged; h_w2c changes.

    `replace` re-runs __init__, so the cached albedo (init=False) is rebuilt at the new size.
    """
    a, b, c, d = (_scale_pt(p, f) for p in scene.proj_corners_world)
    return replace(
        scene,
        world_size=(int(round(scene.world_size[0] * f)), int(round(scene.world_size[1] * f))),
        proj_corners_world=(a, b, c, d),
        features_world={
            k: (float(v[0] * f), float(v[1] * f), float(v[2] * f), float(v[3] * f))
            for k, v in scene.features_world.items()
        },
        occluders_world=[
            (float(v[0] * f), float(v[1] * f), float(v[2] * f), float(v[3] * f))
            for v in scene.occluders_world
        ],
    )


def far_scene(
    k: float,
    *,
    cam_size: tuple[int, int] = (1280, 720),
    centre: tuple[float, float] = (0.6, 0.45),
    world_scale: float = 1.0,
    **overrides: Any,
) -> SimScene:
    """The default layout scaled by `k * cam_w / 1280` about `centre` (fractions of the frame),
    so the projection is small and off-centre in the camera picture."""
    base = default_scene()
    m = np.mean(np.asarray(base.world_corners_cam, dtype=np.float64), axis=0)
    cx, cy = centre[0] * cam_size[0], centre[1] * cam_size[1]
    g = k * cam_size[0] / 1280
    corners = tuple(
        (float(cx + g * (p[0] - m[0])), float(cy + g * (p[1] - m[1])))
        for p in base.world_corners_cam
    )
    scene = replace(base, cam_size=cam_size, world_corners_cam=corners)  # type: ignore[arg-type]
    if world_scale != 1:
        scene = scale_world(scene, world_scale)
    return replace(scene, **overrides) if overrides else scene


def true_px_per_proj_px(scene: SimScene) -> float:
    """Camera px per projector px (linear): sqrt(|det J|) of h_p2c at the projector centre."""
    w, h = scene.proj_size
    hm = scene.h_p2c
    step = 0.5

    def at(x: float, y: float) -> np.ndarray:
        v = hm @ np.array([x, y, 1.0])
        return np.asarray(v[:2] / v[2])

    cx, cy = w / 2, h / 2
    dx = (at(cx + step, cy) - at(cx - step, cy)) / (2 * step)
    dy = (at(cx, cy + step) - at(cx, cy - step)) / (2 * step)
    return float(np.sqrt(abs(dx[0] * dy[1] - dx[1] * dy[0])))


def scene_far_marginal() -> SimScene:
    return far_scene(0.58)


def scene_far_too_coarse() -> SimScene:
    return far_scene(0.28)


def scene_phone() -> SimScene:
    return far_scene(0.7, cam_size=(2560, 1440), world_scale=2.0, blur_sigma=1.2, noise_sigma=1.5)
