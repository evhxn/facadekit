"""Robust plane segmentation of a point cloud (sequential RANSAC + least-squares refit)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Plane:
    normal: np.ndarray  # (3,) unit
    d: float  # plane: normal . X + d = 0
    inliers: int
    rms: float  # metres, of the inliers

    def distance(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(np.asarray(pts, dtype=np.float64) @ self.normal + self.d)


def fit_plane(pts: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares plane through (N >= 3, 3) points: (unit normal, d)."""
    p = np.asarray(pts, dtype=np.float64)
    centre = p.mean(axis=0)
    _, _, vt = np.linalg.svd(p - centre, full_matrices=False)
    n = vt[2]
    return n, float(-n @ centre)


def flatness(pts: np.ndarray) -> float:
    """RMS distance (metres) of the points to their least-squares plane."""
    p = np.asarray(pts, dtype=np.float64)
    if len(p) < 3:
        return 0.0
    n, d = fit_plane(p)
    return float(np.sqrt(np.mean((p @ n + d) ** 2)))


def ransac_plane(
    pts: np.ndarray,
    threshold: float,
    rng: np.random.Generator,
    *,
    iterations: int = 300,
) -> tuple[np.ndarray, float, np.ndarray] | None:
    """Best plane by inlier count, refitted on its inliers twice. (normal, d, inlier mask)."""
    n_pts = len(pts)
    if n_pts < 3:
        return None
    best_count = 0
    best: tuple[np.ndarray, float] | None = None
    idx = rng.integers(0, n_pts, size=(iterations, 3))
    a, b, c = pts[idx[:, 0]], pts[idx[:, 1]], pts[idx[:, 2]]
    normals = np.cross(b - a, c - a)
    norms = np.linalg.norm(normals, axis=1)
    good = norms > 1e-9
    normals = normals[good] / norms[good, None]
    ds = -np.einsum("ij,ij->i", normals, a[good])
    # score in chunks to bound memory
    for start in range(0, len(normals), 32):
        nn, dd = normals[start : start + 32], ds[start : start + 32]
        counts = (np.abs(pts @ nn.T + dd) < threshold).sum(axis=0)
        k = int(np.argmax(counts)) if len(counts) else 0
        if len(counts) and counts[k] > best_count:
            best_count = int(counts[k])
            best = (nn[k], float(dd[k]))
    if best is None or best_count < 3:
        return None
    n, d = best
    mask = np.abs(pts @ n + d) < threshold
    for _ in range(2):
        if mask.sum() < 3:
            break
        n, d = fit_plane(pts[mask])
        mask = np.abs(pts @ n + d) < threshold
    return n, d, mask


def largest_patch(pts: np.ndarray, normal: np.ndarray, cell: float) -> np.ndarray:
    """Mask of the points in the largest connected patch on their plane (cells of `cell`
    metres, gaps of one cell bridged). RANSAC alone accepts a plane that cuts through several
    separate surfaces; this keeps one of them."""
    if len(pts) < 3:
        return np.ones(len(pts), bool)
    a = np.cross(normal, [1.0, 0.0, 0.0])
    if np.linalg.norm(a) < 0.1:
        a = np.cross(normal, [0.0, 1.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(normal, a)
    uv = np.stack([pts @ a, pts @ b], axis=1)
    ij = np.floor((uv - uv.min(axis=0)) / cell).astype(np.int64)
    shape = (int(ij[:, 1].max()) + 1, int(ij[:, 0].max()) + 1)
    if shape[0] * shape[1] > 4_000_000:  # absurdly spread points: do not filter
        return np.ones(len(pts), bool)
    raw = np.zeros(shape, np.uint8)
    raw[ij[:, 1], ij[:, 0]] = 1
    grid = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, comp = cv2.connectedComponents(grid, connectivity=8)
    if n <= 2:
        return np.ones(len(pts), bool)
    lab = comp[ij[:, 1], ij[:, 0]]
    sizes = np.bincount(lab, minlength=n)
    sizes[0] = 0
    return np.asarray(lab == int(np.argmax(sizes)))


def segment_planes(
    pts: np.ndarray,
    *,
    threshold: float,
    min_fraction: float = 0.04,
    min_points: int = 150,
    max_planes: int = 8,
    cell: float = 0.04,
    seed: int = 0,
) -> tuple[list[Plane], np.ndarray]:
    """Sequential RANSAC. Returns the planes (largest first) and a label per point
    (index into the planes, -1 = not on any plane).

    Each plane keeps only its largest connected patch (``largest_patch``). A candidate parallel
    to an earlier plane and within 2.5 thresholds of it is the noise tail of that plane, not a
    surface: its points join the earlier plane."""
    rng = np.random.default_rng(seed)
    p = np.asarray(pts, dtype=np.float64)
    labels = np.full(len(p), -1, dtype=np.int32)
    planes: list[Plane] = []
    need = max(min_points, int(min_fraction * len(p)))
    tries = 0
    while len(planes) < max_planes and tries < 3 * max_planes:
        tries += 1
        free = np.nonzero(labels == -1)[0]
        if len(free) < need:
            break
        found = ransac_plane(p[free], threshold, rng)
        if found is None:
            break
        n, d, mask = found
        members = free[mask]
        if len(members) < need:
            break
        members = members[largest_patch(p[members], n, cell)]
        if len(members) < need:
            labels[members] = -3  # not a plane worth keeping; do not try these points again
            continue
        n, d = fit_plane(p[members])
        twin = next(
            (k for k, q in enumerate(planes)
             if abs(float(q.normal @ n)) > math.cos(math.radians(8))
             and float(np.median(np.abs(q.distance(p[members])))) < 2.5 * threshold),
            None,
        )  # fmt: skip
        if twin is not None:
            labels[members] = twin
            continue
        labels[members] = len(planes)
        resid = p[members] @ n + d
        planes.append(Plane(n, d, int(len(members)), float(np.sqrt(np.mean(resid**2)))))
    labels[labels == -3] = -1
    return planes, labels


def ray_plane_depth(rays: np.ndarray, origin: np.ndarray, plane: Plane) -> np.ndarray:
    """Parameter s along world rays origin + s * ray to the plane (NaN if parallel/behind)."""
    denom = rays @ plane.normal
    with np.errstate(divide="ignore", invalid="ignore"):
        s = -(origin @ plane.normal + plane.d) / denom
    s[(np.abs(denom) < 1e-9) | (s <= 0)] = np.nan
    return np.asarray(s)
