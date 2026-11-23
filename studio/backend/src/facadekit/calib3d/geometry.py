"""Pinhole cameras, poses and conventions for calib3d (docs/18-calib3d-capture-format.md).

Conventions used everywhere in this package:

- Pixel coordinates follow OpenCV: integer coordinates are pixel *centres*, so pixel (0, 0)
  covers [-0.5, 0.5] x [-0.5, 0.5]. FacadeKit's scan maps (``calib.decode``, ``calib.maps``)
  use pixel-*corner* coordinates; convert with ``corner_to_centre`` / ``centre_to_corner``.
- Camera frames follow OpenCV: x right, y down, z forward (into the scene).
- A pose is the 4x4 ``world_from_camera`` matrix: ``X_world = T @ X_camera``.
- Lengths are metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

# ARKit cameras look down -z with y up; OpenCV looks down +z with y down.
ARKIT_TO_OPENCV = np.diag([1.0, -1.0, -1.0, 1.0])


def corner_to_centre(xy: np.ndarray) -> np.ndarray:
    return np.asarray(xy, dtype=np.float64) - 0.5


def centre_to_corner(xy: np.ndarray) -> np.ndarray:
    return np.asarray(xy, dtype=np.float64) + 0.5


@dataclass(frozen=True)
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    @property
    def k(self) -> np.ndarray:
        return np.array([[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]])

    @classmethod
    def from_k(cls, k: np.ndarray, width: int, height: int) -> Intrinsics:
        return cls(
            float(k[0, 0]), float(k[1, 1]), float(k[0, 2]), float(k[1, 2]), int(width), int(height)
        )

    def scaled(self, width: int, height: int) -> Intrinsics:
        """The same camera sampled at another resolution with the same field of view
        (as ARKit's depth map is to its colour image)."""
        sx, sy = width / self.width, height / self.height
        return Intrinsics(
            self.fx * sx,
            self.fy * sy,
            (self.cx + 0.5) * sx - 0.5,
            (self.cy + 0.5) * sy - 0.5,
            int(width),
            int(height),
        )

    def to_json(self) -> dict[str, float | int]:
        return {
            "fx": self.fx,
            "fy": self.fy,
            "cx": self.cx,
            "cy": self.cy,
            "width": self.width,
            "height": self.height,
        }


def pose_inverse(t: np.ndarray) -> np.ndarray:
    r = t[:3, :3]
    out = np.eye(4)
    out[:3, :3] = r.T
    out[:3, 3] = -r.T @ t[:3, 3]
    return out


def pose_from_arkit(t_arkit: np.ndarray) -> np.ndarray:
    """ARKit ``camera.transform`` (world from an ARKit camera) -> world from an OpenCV camera."""
    return np.asarray(t_arkit, dtype=np.float64) @ ARKIT_TO_OPENCV


def make_pose(r: np.ndarray, t: np.ndarray) -> np.ndarray:
    out = np.eye(4)
    out[:3, :3] = r
    out[:3, 3] = np.asarray(t, dtype=np.float64).reshape(3)
    return out


def look_at(
    eye: np.ndarray, target: np.ndarray, up: np.ndarray, roll_deg: float = 0.0
) -> np.ndarray:
    """world_from_camera for an OpenCV camera at `eye` looking at `target` (`up` is world up)."""
    eye = np.asarray(eye, dtype=np.float64)
    z = np.asarray(target, dtype=np.float64) - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, np.asarray(up, dtype=np.float64))  # right = forward x up for y-down cams
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    r = np.stack([x, y, z], axis=1)
    if roll_deg:
        r = r @ rot_axis(np.array([0.0, 0.0, 1.0]), math.radians(roll_deg))
    return make_pose(r, eye)


def rot_axis(axis: np.ndarray, angle: float) -> np.ndarray:
    rv = np.asarray(axis, dtype=np.float64).reshape(3)
    rv = rv / np.linalg.norm(rv) * angle
    return np.asarray(cv2.Rodrigues(rv)[0], dtype=np.float64)


def rotation_angle_deg(r: np.ndarray) -> float:
    c = (float(np.trace(r)) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def transform(t: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    return np.asarray(p @ t[:3, :3].T + t[:3, 3])


def pixel_rays(intr: Intrinsics, us: np.ndarray, vs: np.ndarray) -> np.ndarray:
    """Camera-frame ray directions (z = 1) through pixel centres (us, vs)."""
    x = (np.asarray(us, dtype=np.float64) - intr.cx) / intr.fx
    y = (np.asarray(vs, dtype=np.float64) - intr.cy) / intr.fy
    return np.stack([x, y, np.ones_like(x)], axis=-1)


def backproject(intr: Intrinsics, us: np.ndarray, vs: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Camera-frame points at z-depth `z` (along the optical axis, not along the ray)."""
    return pixel_rays(intr, us, vs) * np.asarray(z, dtype=np.float64)[..., None]


def project(
    intr: Intrinsics, world_from_cam: np.ndarray, pts_world: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(N, 2) pixel coordinates and (N,) z-depths of world points. z <= 0 is behind."""
    pc = transform(pose_inverse(world_from_cam), pts_world)
    z = pc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = intr.fx * pc[:, 0] / z + intr.cx
        v = intr.fy * pc[:, 1] / z + intr.cy
    return np.stack([u, v], axis=1), z


def depth_image_points(
    intr: Intrinsics, depth: np.ndarray, valid: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Camera-frame points of a depth image: (points (N, 3), us, vs) of valid pixels."""
    ok = np.isfinite(depth) & (depth > 0)
    if valid is not None:
        ok &= valid
    vs, us = np.nonzero(ok)
    return backproject(intr, us, vs, depth[vs, us]), us, vs
