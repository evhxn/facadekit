"""Projector self-calibration from 3D points and their decoded projector pixels.

The projector is modelled as a pinhole camera (fx, fy, cx, cy, no skew, no lens distortion by
default) with a pose. Steps: robust DLT (RANSAC over normalised 3x4 projection matrices), RQ
decomposition for a start, then Levenberg-Marquardt on the inliers, a second inlier pass and a
final refit.

The refinement is our own Levenberg-Marquardt (``refine``) because ``cv2.calibrateCamera``
rejects a principal point outside the image, which lens-shifted projectors have.

A single plane cannot fix a projector: its points give one homography (8 numbers) against the
projector's 10 unknowns, and the DLT is singular. ``solve_projector`` therefore checks how far
the points are from one plane first and returns status ``planar`` without a model (the plane
mapping is exact there). Points with little depth relief give status ``weak`` when the
parameter uncertainties reported by the refinement are large.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import cv2
import numpy as np

from facadekit.calib3d.geometry import Intrinsics, make_pose, pose_inverse, rotation_angle_deg
from facadekit.calib3d.planes import ransac_plane

Status = Literal["ok", "weak", "planar", "too_few", "failed"]

MIN_POINTS = 30
# Relief: RMS distance of the points to their best plane, relative to their spread. Below
# PLANAR_RELIEF the problem is treated as a plane (docs/18 "Degenerate case").
PLANAR_RELIEF = 0.01
PLANAR_INLIERS = 0.97  # or this share of the points within PLANAR_TOL_M of one plane
PLANAR_TOL_M = 0.02
MIN_INLIER_FRACTION = 0.6  # below this the solve is "failed"
# Status "weak" when the refinement's standard deviations exceed these.
WEAK_F_REL = 0.02
WEAK_C_PX = 20.0


@dataclass
class ProjectorSolution:
    status: Status
    message: str
    intrinsics: Intrinsics | None = None
    pose: np.ndarray | None = None  # world_from_projector (OpenCV axes)
    dist: np.ndarray | None = None
    n_points: int = 0
    inlier_fraction: float = 0.0
    median_px: float = math.nan  # reprojection error of the inliers, projector px
    p95_px: float = math.nan
    median_all_px: float = math.nan
    std: dict[str, float] = field(default_factory=dict)  # 1-sigma of fx, fy, cx, cy
    relief: float = 0.0
    inliers: np.ndarray | None = None

    @property
    def usable(self) -> bool:
        return self.status in ("ok", "weak") and self.intrinsics is not None

    def rejected(self, status: Status, message: str) -> ProjectorSolution:
        """The same numbers, marked not usable (kept in the report for diagnosis)."""
        return replace(self, status=status, message=message)

    def project(self, pts_world: np.ndarray) -> np.ndarray:
        assert self.intrinsics is not None and self.pose is not None
        t = pose_inverse(self.pose)
        rvec = cv2.Rodrigues(t[:3, :3])[0]
        dist = self.dist if self.dist is not None else np.zeros(5)
        out, _ = cv2.projectPoints(
            np.asarray(pts_world, dtype=np.float64).reshape(-1, 1, 3),
            rvec,
            t[:3, 3],
            self.intrinsics.k,
            dist,
        )
        return np.asarray(out.reshape(-1, 2))

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "message": self.message,
            "n_points": self.n_points,
            "inlier_fraction": round(self.inlier_fraction, 4),
            "reprojection_median_px": _r(self.median_px),
            "reprojection_p95_px": _r(self.p95_px),
            "reprojection_median_all_px": _r(self.median_all_px),
            "relief": round(self.relief, 4),
            "std": {k: _r(v) for k, v in self.std.items()},
        }
        if self.intrinsics is not None and self.pose is not None:
            out["intrinsics"] = self.intrinsics.to_json()
            out["pose_world_from_projector"] = self.pose.tolist()
            if self.dist is not None:
                out["distortion"] = [float(v) for v in self.dist.ravel()]
        return out


def _r(v: float) -> float | None:
    return None if not math.isfinite(v) else round(float(v), 4)


def _normalise(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dim = pts.shape[1]
    c = pts.mean(axis=0)
    s = math.sqrt(dim) / max(float(np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()), 1e-12)
    t = np.eye(dim + 1)
    t[:dim, :dim] *= s
    t[:dim, dim] = -s * c
    return (pts - c) * s, t


def dlt(x3: np.ndarray, x2: np.ndarray) -> np.ndarray:
    """Normalised DLT: 3x4 P with x2 ~ P [x3; 1]. Needs >= 6 points not on one plane."""
    a3, t3 = _normalise(np.asarray(x3, dtype=np.float64))
    a2, t2 = _normalise(np.asarray(x2, dtype=np.float64))
    n = len(a3)
    xh = np.hstack([a3, np.ones((n, 1))])
    rows = np.zeros((2 * n, 12))
    rows[0::2, 0:4] = xh
    rows[0::2, 8:12] = -a2[:, :1] * xh
    rows[1::2, 4:8] = xh
    rows[1::2, 8:12] = -a2[:, 1:2] * xh
    _, _, vt = np.linalg.svd(rows, full_matrices=False)
    p = vt[-1].reshape(3, 4)
    return np.asarray(np.linalg.inv(t2) @ p @ t3)


def project_p(p: np.ndarray, x3: np.ndarray) -> np.ndarray:
    xh = np.hstack([x3, np.ones((len(x3), 1))]) @ p.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(xh[:, :2] / xh[:, 2:3])


def decompose(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """P -> (K with K[2,2] = 1 and positive focal lengths, world_from_camera pose)."""
    k, r, c_h = cv2.decomposeProjectionMatrix(p)[:3]
    k = np.asarray(k, dtype=np.float64)
    r = np.asarray(r, dtype=np.float64)
    c = (c_h[:3] / c_h[3]).reshape(3)
    k = k / k[2, 2]
    # make focal lengths positive (flip axes of R accordingly)
    s = np.diag(np.sign(np.diag(k)))
    k, r = k @ s, s @ r
    if np.linalg.det(r) < 0:  # P was found up to sign
        r = -r
    return k, make_pose(r.T, c)


def relief(x3: np.ndarray) -> float:
    """RMS distance to the best plane over the RMS spread within the plane."""
    c = x3 - x3.mean(axis=0)
    s = np.linalg.svd(c, compute_uv=False)
    return float(s[2] / max(math.hypot(s[0], s[1]), 1e-12))


def planar_tolerance(x3: np.ndarray, rng: np.random.Generator) -> float:
    """Distance (m) within which a point counts as on a plane: PLANAR_TOL_M, or 3 robust
    sigmas of the points' distances to their best plane when the depth is noisier. Noise
    larger than the relief makes a surface indistinguishable from a plane, and the projector
    solve then converges to a wrong projector that still fits the plane."""
    tol = PLANAR_TOL_M
    for _ in range(3):  # inliers are cut at tol, so widen while the noise says so
        found = ransac_plane(x3, tol, rng)
        if found is None:
            return tol
        n, d, inl = found
        sigma = float(np.median(np.abs(x3[inl] @ n + d))) / 0.6745  # median |N(0, s)| = 0.6745 s
        new = max(PLANAR_TOL_M, 3.0 * sigma)
        if new <= tol * 1.05:
            break
        tol = new
    return tol


def _ransac_dlt(
    x3: np.ndarray, x2: np.ndarray, thr: float, rng: np.random.Generator, iters: int
) -> np.ndarray | None:
    n = len(x3)
    best_count, best = 0, None
    sample = min(12, n)
    for _ in range(iters):
        idx = rng.choice(n, sample, replace=False)
        try:
            p = dlt(x3[idx], x2[idx])
        except np.linalg.LinAlgError:
            continue
        err = np.linalg.norm(project_p(p, x3) - x2, axis=1)
        count = int(np.sum(err < thr))
        if count > best_count:
            best_count, best = count, p
    if best is None:
        return None
    err = np.linalg.norm(project_p(best, x3) - x2, axis=1)
    inl = err < thr
    return dlt(x3[inl], x2[inl]) if inl.sum() >= 6 else best


def solve_projector(
    x3: np.ndarray,
    x2: np.ndarray,
    size: tuple[int, int],
    *,
    weights: np.ndarray | None = None,
    ransac_px: float = 6.0,
    inlier_px: float = 3.0,
    distortion: bool = False,
    seed: int = 0,
) -> ProjectorSolution:
    """Solve a projector of `size` (w, h) from world points x3 (N, 3) and the projector pixels
    x2 (N, 2, OpenCV centre convention) that light them."""
    del weights  # reserved; every block counts once (see solve.correspondences)
    x3 = np.asarray(x3, dtype=np.float64)
    x2 = np.asarray(x2, dtype=np.float64)
    ok = np.isfinite(x3).all(axis=1) & np.isfinite(x2).all(axis=1)
    x3, x2 = x3[ok], x2[ok]
    n = len(x3)
    if n < MIN_POINTS:
        return ProjectorSolution("too_few", f"only {n} 3D-2D pairs (need {MIN_POINTS})", n_points=n)
    rng = np.random.default_rng(seed)
    rel = relief(x3)
    tol = planar_tolerance(x3, rng)
    plane = ransac_plane(x3, tol, rng)
    on_plane = float(plane[2].mean()) if plane is not None else 0.0
    if rel < PLANAR_RELIEF or on_plane >= PLANAR_INLIERS:
        return ProjectorSolution(
            "planar",
            f"the lit points lie on one plane ({on_plane * 100:.0f} % within "
            f"{tol * 100:.1f} cm; relief {rel:.3f}). A plane fixes only a homography "
            "(8 numbers), the projector has 10: its intrinsics and pose are under-constrained. "
            "The plane mapping is exact for this surface and is used instead.",
            n_points=n,
            relief=rel,
        )
    p = _ransac_dlt(x3, x2, ransac_px, rng, 300)
    if p is None:
        return ProjectorSolution(
            "failed", "RANSAC found no projection matrix", n_points=n, relief=rel
        )
    k0, pose0 = decompose(p)
    w, h = size
    if not (0 < k0[0, 0] < 50 * w and 0 < k0[1, 1] < 50 * w):
        return ProjectorSolution(
            "failed", "DLT gave an implausible focal length", n_points=n, relief=rel
        )
    k0[0, 1] = 0.0
    err0 = np.linalg.norm(project_p(p, x3) - x2, axis=1)
    inl = err0 < ransac_px
    k, dist = k0.copy(), np.zeros(5)
    t_cw = pose_inverse(pose0)
    std = np.zeros(4)
    err = err0
    for thr in (ransac_px, inlier_px):
        if inl.sum() < MIN_POINTS:
            return ProjectorSolution(
                "failed", f"only {int(inl.sum())} inliers", n_points=n, relief=rel
            )
        k, dist, t_cw, std = refine(x3[inl], x2[inl], k, dist, t_cw, distortion=distortion)
        sol = ProjectorSolution(
            "ok", "", Intrinsics.from_k(k, w, h), pose_inverse(t_cw), dist if distortion else None
        )
        err = np.linalg.norm(sol.project(x3) - x2, axis=1)
        inl = err < thr
    err_in = err[inl]
    sol.n_points = n
    sol.inliers = inl
    sol.inlier_fraction = float(inl.mean())
    sol.median_px = float(np.median(err_in)) if len(err_in) else math.nan
    sol.p95_px = float(np.percentile(err_in, 95)) if len(err_in) else math.nan
    sol.median_all_px = float(np.median(err))
    sol.relief = rel
    sol.std = {"fx": float(std[0]), "fy": float(std[1]), "cx": float(std[2]), "cy": float(std[3])}
    weak = []
    if std[0] > WEAK_F_REL * k[0, 0] or std[1] > WEAK_F_REL * k[1, 1]:
        weak.append(f"focal length ±{std[0]:.0f} px")
    if std[2] > WEAK_C_PX or std[3] > WEAK_C_PX:
        weak.append(f"principal point ±({std[2]:.0f}, {std[3]:.0f}) px")
    plane_in = ransac_plane(x3[inl], tol, rng) if inl.sum() >= 3 else None
    on_plane_in = float(plane_in[2].mean()) if plane_in is not None else 1.0
    if sol.inlier_fraction < MIN_INLIER_FRACTION:
        sol.status = "failed"
        sol.message = (
            f"only {sol.inlier_fraction * 100:.0f} % of the 3D-2D pairs agree with one projector "
            f"(need {MIN_INLIER_FRACTION * 100:.0f} %): depth and scan do not fit together"
        )
    elif on_plane_in >= PLANAR_INLIERS:
        sol.status = "planar"
        sol.message = (
            f"after outlier rejection {on_plane_in * 100:.0f} % of the remaining points lie on one "
            "plane: the projector is under-constrained. The plane mapping is used instead."
        )
    elif weak:
        sol.status = "weak"
        sol.message = (
            "solved, but weakly constrained (" + ", ".join(weak) + "): the surface has "
            "little depth relief. Points far from the measured ones may be off."
        )
    else:
        sol.message = "solved"
    return sol


def refine(
    x3: np.ndarray,
    x2: np.ndarray,
    k: np.ndarray,
    dist: np.ndarray,
    cam_from_world: np.ndarray,
    *,
    distortion: bool = False,
    iterations: int = 50,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Levenberg-Marquardt over (rvec, tvec, fx, fy, cx, cy[, k1, k2]) with the Jacobian from
    ``cv2.projectPoints``. Unlike ``cv2.calibrateCamera`` it accepts a principal point outside
    the image, which projectors with lens shift have. Returns (K, dist, camera_from_world,
    1-sigma of fx, fy, cx, cy)."""
    obj = np.asarray(x3, dtype=np.float64).reshape(-1, 1, 3)
    target = np.asarray(x2, dtype=np.float64).reshape(-1)
    rvec = cv2.Rodrigues(cam_from_world[:3, :3])[0].ravel()
    tvec = cam_from_world[:3, 3].copy()
    intr = np.array([k[0, 0], k[1, 1], k[0, 2], k[1, 2]])
    d = np.asarray(dist, dtype=np.float64).ravel()[:5].copy()
    n_par = 10 + (2 if distortion else 0)

    def unpack(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        kk = np.array([[x[6], 0.0, x[8]], [0.0, x[7], x[9]], [0.0, 0.0, 1.0]])
        dd = d.copy()
        if distortion:
            dd[0], dd[1] = x[10], x[11]
        return x[:3], x[3:6], kk, dd

    def residual(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rv, tv, kk, dd = unpack(x)
        pts, jac = cv2.projectPoints(obj, rv, tv, kk, dd)
        # jacobian columns: rvec 3, tvec 3, focal 2, principal 2, distortion ...
        j = jac[:, : 10 + (2 if distortion else 0)]
        return pts.reshape(-1) - target, np.asarray(j, dtype=np.float64)

    x = np.concatenate([rvec, tvec, intr, d[:2] if distortion else np.zeros(0)])
    lam = 1e-3
    r, j = residual(x)
    cost = float(r @ r)
    for _ in range(iterations):
        jtj = j.T @ j
        g = j.T @ r
        step = np.linalg.solve(jtj + lam * np.diag(np.diag(jtj) + 1e-12), -g)
        x_new = x + step
        r_new, j_new = residual(x_new)
        c_new = float(r_new @ r_new)
        if c_new < cost:
            converged = cost - c_new < 1e-12 * max(cost, 1e-12)
            x, r, j, cost = x_new, r_new, j_new, c_new
            lam = max(lam / 10, 1e-12)
            if converged:
                break
        else:
            lam *= 10
            if lam > 1e10:
                break
    rv, tv, kk, dd = unpack(x)
    dof = max(len(r) - n_par, 1)
    sigma2 = cost / dof
    try:
        cov = np.linalg.inv(j.T @ j) * sigma2
        std = np.sqrt(np.maximum(np.diag(cov)[6:10], 0))
    except np.linalg.LinAlgError:
        std = np.full(4, np.inf)
    cfw = make_pose(cv2.Rodrigues(rv)[0], tv)
    return kk, dd, cfw, std


def compare_to_truth(
    sol: ProjectorSolution, k_true: np.ndarray, pose_true: np.ndarray
) -> dict[str, float]:
    """Errors of a solved projector against ground truth (simulator only)."""
    assert sol.intrinsics is not None and sol.pose is not None
    k = sol.intrinsics.k
    return {
        "fx_rel": float(abs(k[0, 0] - k_true[0, 0]) / k_true[0, 0]),
        "fy_rel": float(abs(k[1, 1] - k_true[1, 1]) / k_true[1, 1]),
        "cx_px": float(abs(k[0, 2] - k_true[0, 2])),
        "cy_px": float(abs(k[1, 2] - k_true[1, 2])),
        "position_m": float(np.linalg.norm(sol.pose[:3, 3] - pose_true[:3, 3])),
        "rotation_deg": rotation_angle_deg(sol.pose[:3, :3].T @ pose_true[:3, :3]),
    }
