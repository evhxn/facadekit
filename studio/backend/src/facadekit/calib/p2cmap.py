"""Point lookups on a stored projector -> camera map (docs/13-surface-verification.md).

All points are pixel-corner coordinates: (0, 0) is the top-left corner of the top-left pixel.
`p2c[y, x]` holds the camera position of projector pixel centre (x + 0.5, y + 0.5), so a
projector point (px, py) is sampled at array index (px - 0.5, py - 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from facadekit.errors import NOT_FOUND, FkError
from facadekit.geom.homography import apply_h
from facadekit.models.calibration import Calibration

_JAC_STEP = 2.0  # projector px, central differences
_NEWTON_STEPS = 12
_NEWTON_TOL = 0.02  # camera px


@dataclass
class ProjCamMap:
    p2c: np.ndarray  # float32 (H, W, 2)
    measured: np.ndarray  # bool (H, W)
    h_c2p: np.ndarray | None  # (3, 3) camera -> projector, global plane fit

    @property
    def proj_size(self) -> tuple[int, int]:
        h, w = self.measured.shape
        return w, h

    def _sample(self, image: np.ndarray, pts: np.ndarray) -> np.ndarray:
        p = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2) - np.float32(0.5)
        out = cv2.remap(
            image,
            np.ascontiguousarray(p[..., 0]),
            np.ascontiguousarray(p[..., 1]),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return np.asarray(out).reshape(len(p), -1)

    def to_cam(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Projector points (N, 2) -> camera points (N, 2) and validity (N,).

        A point is valid when it is inside the projector frame and the four map nodes around
        it were measured (not filled in by the scan)."""
        p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        if len(p) == 0:
            return np.zeros((0, 2)), np.zeros(0, bool)
        cam = self._sample(self.p2c, p).astype(np.float64)
        meas = self._sample(self.measured.astype(np.float32), p)[:, 0]
        w, h = self.proj_size
        inside = (p[:, 0] >= 0) & (p[:, 0] <= w) & (p[:, 1] >= 0) & (p[:, 1] <= h)
        return cam, inside & (meas > 0.99)

    def jacobian(self, pts: np.ndarray) -> np.ndarray:
        """d(camera) / d(projector) at each point, (N, 2, 2): column k is the derivative along
        projector axis k."""
        p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        out = np.empty((len(p), 2, 2))
        for k in (0, 1):
            step = np.zeros(2)
            step[k] = _JAC_STEP
            a, _ = self.to_cam(p + step)
            b, _ = self.to_cam(p - step)
            out[:, :, k] = (a - b) / (2 * _JAC_STEP)
        return out

    def scale_at(self, pts: np.ndarray) -> np.ndarray:
        """Camera px per projector px (linear) at each projector point."""
        jac = self.jacobian(pts)
        det = jac[:, 0, 0] * jac[:, 1, 1] - jac[:, 0, 1] * jac[:, 1, 0]
        return np.asarray(np.sqrt(np.abs(det)))

    def to_proj(self, cam_pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Camera points (N, 2) -> projector points (N, 2) and validity (N,), by Newton steps
        on the dense map starting from the plane fit. Invalid where the map was not measured
        or the iteration did not land within a few hundredths of a camera pixel."""
        c = np.asarray(cam_pts, dtype=np.float64).reshape(-1, 2)
        if len(c) == 0:
            return np.zeros((0, 2)), np.zeros(0, bool)
        w, h = self.proj_size
        if self.h_c2p is not None:
            q = apply_h(self.h_c2p, c)
        else:
            q = np.tile([w / 2, h / 2], (len(c), 1)).astype(np.float64)
        q[:, 0] = np.clip(q[:, 0], 0, w)
        q[:, 1] = np.clip(q[:, 1], 0, h)
        for _ in range(_NEWTON_STEPS):
            got, _ = self.to_cam(q)
            err = c - got
            jac = self.jacobian(q)
            det = jac[:, 0, 0] * jac[:, 1, 1] - jac[:, 0, 1] * jac[:, 1, 0]
            ok = np.abs(det) > 1e-9
            safe = np.where(ok, det, 1.0)
            dx = (jac[:, 1, 1] * err[:, 0] - jac[:, 0, 1] * err[:, 1]) / safe
            dy = (-jac[:, 1, 0] * err[:, 0] + jac[:, 0, 0] * err[:, 1]) / safe
            step = np.stack([dx, dy], axis=1)
            step[~ok] = 0.0
            # damp very large steps (first iterations far from the answer)
            norm = np.linalg.norm(step, axis=1, keepdims=True)
            step = np.where(norm > 64.0, step * (64.0 / np.maximum(norm, 1e-9)), step)
            q = q + step
            q[:, 0] = np.clip(q[:, 0], 0, w)
            q[:, 1] = np.clip(q[:, 1], 0, h)
            if float(np.max(np.abs(err))) < _NEWTON_TOL:
                break
        got, valid = self.to_cam(q)
        close = np.linalg.norm(got - c, axis=1) < 0.25
        return q, valid & close

    def polygon_to_cam(self, poly: np.ndarray, step: float = 2.0) -> tuple[np.ndarray, float]:
        """A closed projector polygon -> a dense camera polyline, and the share of its samples
        that fall on measured map nodes."""
        pts = densify(np.asarray(poly, dtype=np.float64).reshape(-1, 2), step)
        cam, valid = self.to_cam(pts)
        return cam, float(valid.mean()) if len(valid) else 0.0


def densify(poly: np.ndarray, step: float) -> np.ndarray:
    """Points every `step` along the closed polygon (vertices included)."""
    out: list[np.ndarray] = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        length = float(np.linalg.norm(b - a))
        k = max(int(np.ceil(length / step)), 1)
        t = np.arange(k, dtype=np.float64)[:, None] / k
        out.append(a + t * (b - a))
    return np.concatenate(out, axis=0) if out else np.zeros((0, 2))


def load_map(project_dir: Path, cal: Calibration) -> ProjCamMap:
    folder = project_dir / "calibration" / cal.id
    p2c_path = folder / "p2c.npy"
    mask_path = folder / "measured_mask.png"
    if cal.mode != "scan" or not p2c_path.is_file():
        raise FkError(
            NOT_FOUND,
            "This project has no camera scan, so there is nothing to check against. "
            "Scan the house on the Setup page first.",
        )
    p2c = np.load(p2c_path).astype(np.float32)
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    measured = (
        mask > 0 if mask is not None else np.ones(p2c.shape[:2], dtype=bool)
    )  # pragma: no branch
    h_c2p = np.asarray(cal.h_c2p, dtype=np.float64) if cal.h_c2p is not None else None
    return ProjCamMap(p2c=p2c, measured=np.asarray(measured), h_c2p=h_c2p)
