from __future__ import annotations

from dataclasses import dataclass, field

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
