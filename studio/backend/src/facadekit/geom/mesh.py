"""Mesh warp: control-point offsets that bend a surface after the perspective mapping."""

from __future__ import annotations

import math

import numpy as np

from facadekit.errors import FkError
from facadekit.geom.homography import apply_h, quad_homography
from facadekit.models.project import Mesh, Surface

_EPS = 1e-6


def identity_mesh(cols: int, rows: int) -> Mesh:
    """A mesh of `cols` x `rows` cells with all offsets zero."""
    return Mesh(cols=cols, rows=rows, offsets=[(0.0, 0.0)] * ((cols + 1) * (rows + 1)))


def _require_mesh(surface: Surface) -> Mesh:
    if surface.mesh is None:
        raise FkError("BAD_REQUEST", "surface has no mesh")
    return surface.mesh


def mesh_control_points(surface: Surface) -> np.ndarray:
    """(rows+1, cols+1, 2) final projector positions of the control points (base + offset)."""
    mesh = _require_mesh(surface)
    cols, rows = mesh.cols, mesh.rows
    h = quad_homography(1, 1, surface.quad)
    uu, vv = np.meshgrid(np.arange(cols + 1) / cols, np.arange(rows + 1) / rows)
    base = apply_h(h, np.stack([uu.ravel(), vv.ravel()], axis=1))
    off = np.asarray(mesh.offsets, dtype=np.float64).reshape(-1, 2)
    return np.asarray((base + off).reshape(rows + 1, cols + 1, 2))


def _final_positions(surface: Surface, subdiv: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fine-grid (u, v) coordinates and final positions: each (R, C[, 2])."""
    mesh = _require_mesh(surface)
    cols, rows = mesh.cols, mesh.rows
    nc, nr = cols * subdiv + 1, rows * subdiv + 1
    u = np.linspace(0.0, 1.0, nc)
    v = np.linspace(0.0, 1.0, nr)
    uu, vv = np.meshgrid(u, v)
    h = quad_homography(1, 1, surface.quad)
    base = apply_h(h, np.stack([uu.ravel(), vv.ravel()], axis=1)).reshape(nr, nc, 2)

    off = np.asarray(mesh.offsets, dtype=np.float64).reshape(rows + 1, cols + 1, 2)
    fj = np.clip(uu * cols, 0.0, cols)
    fi = np.clip(vv * rows, 0.0, rows)
    j0 = np.minimum(np.floor(fj).astype(int), cols - 1)
    i0 = np.minimum(np.floor(fi).astype(int), rows - 1)
    tj = (fj - j0)[..., None]
    ti = (fi - i0)[..., None]
    interp = (
        off[i0, j0] * (1 - tj) * (1 - ti)
        + off[i0, j0 + 1] * tj * (1 - ti)
        + off[i0 + 1, j0] * (1 - tj) * ti
        + off[i0 + 1, j0 + 1] * tj * ti
    )
    return uu, vv, np.asarray(base + interp)


def _fill_triangle(
    map_u: np.ndarray,
    map_v: np.ndarray,
    pts: np.ndarray,
    uvs: np.ndarray,
) -> None:
    """Rasterise one triangle (vertices at pixel-centre-index coordinates), edges inclusive."""
    height, width = map_u.shape
    x_lo = max(int(math.ceil(pts[:, 0].min() - _EPS)), 0)
    x_hi = min(int(math.floor(pts[:, 0].max() + _EPS)), width - 1)
    y_lo = max(int(math.ceil(pts[:, 1].min() - _EPS)), 0)
    y_hi = min(int(math.floor(pts[:, 1].max() + _EPS)), height - 1)
    if x_lo > x_hi or y_lo > y_hi:
        return
    (ax, ay), (bx, by), (cx, cy) = pts
    denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(denom) < 1e-12:
        return
    xs = np.arange(x_lo, x_hi + 1, dtype=np.float64)[None, :]
    ys = np.arange(y_lo, y_hi + 1, dtype=np.float64)[:, None]
    l1 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / denom
    l2 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / denom
    l3 = 1.0 - l1 - l2
    inside = (l1 >= -_EPS) & (l2 >= -_EPS) & (l3 >= -_EPS)
    if not inside.any():
        return
    u = l1 * uvs[0, 0] + l2 * uvs[1, 0] + l3 * uvs[2, 0]
    v = l1 * uvs[0, 1] + l2 * uvs[1, 1] + l3 * uvs[2, 1]
    sl = (slice(y_lo, y_hi + 1), slice(x_lo, x_hi + 1))
    map_u[sl][inside] = u[inside]
    map_v[sl][inside] = v[inside]


def mesh_inverse_map(
    surface: Surface, out_size: tuple[int, int], *, subdiv: int = 8
) -> tuple[np.ndarray, np.ndarray]:
    """Per output pixel, the surface-unit (u, v) it shows (sampled at pixel centres), else -1."""
    width, height = out_size
    map_u = np.full((height, width), -1.0, dtype=np.float32)
    map_v = np.full((height, width), -1.0, dtype=np.float32)
    uu, vv, pos = _final_positions(surface, max(1, subdiv))
    # Pixel (x, y) has its centre at (x + 0.5, y + 0.5): shift so centres sit on integers.
    pos = pos - 0.5
    uv = np.stack([uu, vv], axis=-1)
    nr, nc = uu.shape
    for i in range(nr - 1):
        for j in range(nc - 1):
            p00, p01 = pos[i, j], pos[i, j + 1]
            p10, p11 = pos[i + 1, j], pos[i + 1, j + 1]
            u00, u01 = uv[i, j], uv[i, j + 1]
            u10, u11 = uv[i + 1, j], uv[i + 1, j + 1]
            _fill_triangle(map_u, map_v, np.array([p00, p01, p10]), np.array([u00, u01, u10]))
            _fill_triangle(map_u, map_v, np.array([p01, p11, p10]), np.array([u01, u11, u10]))
    return map_u, map_v
