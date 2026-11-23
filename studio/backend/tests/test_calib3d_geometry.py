"""calib3d geometry, planes and projector solve on exact synthetic data (S-11, docs/18)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from facadekit.calib3d.geometry import (
    ARKIT_TO_OPENCV,
    Intrinsics,
    backproject,
    centre_to_corner,
    corner_to_centre,
    look_at,
    pose_from_arkit,
    pose_inverse,
    project,
    rotation_angle_deg,
    transform,
)
from facadekit.calib3d.planes import (
    Plane,
    fit_plane,
    flatness,
    largest_patch,
    ransac_plane,
    ray_plane_depth,
    segment_planes,
)
from facadekit.calib3d.projector import (
    compare_to_truth,
    decompose,
    dlt,
    project_p,
    relief,
    solve_projector,
)

UP = np.array([0.0, 1.0, 0.0])
PROJ = Intrinsics(2800.0, 2805.0, 965.0, 1100.0, 1920, 1080)  # lens shift: cy below the frame


def _random_pose(rng: np.random.Generator) -> np.ndarray:
    eye = np.array([rng.uniform(-0.5, 0.5), rng.uniform(0.0, 1.0), rng.uniform(2.0, 4.0)])
    target = np.array([rng.uniform(-0.2, 0.2), rng.uniform(0.3, 0.8), 0.0])
    return look_at(eye, target, UP, roll_deg=float(rng.uniform(-3, 3)))


def _scene_points(rng: np.random.Generator, n_wall: int = 3000, box: bool = True) -> np.ndarray:
    wall = np.c_[rng.uniform(-1.2, 1.2, n_wall), rng.uniform(-0.2, 1.4, n_wall), np.zeros(n_wall)]
    if not box:
        return wall
    nb = n_wall // 3
    front = np.c_[rng.uniform(-0.3, 0.3, nb), rng.uniform(0.2, 0.7, nb), np.full(nb, 0.35)]
    side = np.c_[np.full(nb, 0.3), rng.uniform(0.2, 0.7, nb), rng.uniform(0, 0.35, nb)]
    return np.concatenate([wall, front, side])


def _visible(intr: Intrinsics, pose: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    uv, z = project(intr, pose, pts)
    ok = (
        (z > 0)
        & (uv[:, 0] > 0)
        & (uv[:, 0] < intr.width)
        & (uv[:, 1] > 0)
        & (uv[:, 1] < intr.height)
    )
    return pts[ok], uv[ok]


# ---------------------------------------------------------------- geometry


def test_pixel_conventions_round_trip() -> None:
    xy = np.array([[0.0, 0.0], [10.25, 3.5]])
    assert np.allclose(corner_to_centre(centre_to_corner(xy)), xy)
    assert np.allclose(corner_to_centre(np.array([[0.5, 0.5]])), [[0.0, 0.0]])


@pytest.mark.parametrize("seed", range(5))
def test_project_backproject_round_trip(seed: int) -> None:
    rng = np.random.default_rng(seed)
    intr = Intrinsics(1000.0, 990.0, 640.0, 470.0, 1280, 960)
    pose = _random_pose(rng)
    pts = _scene_points(rng, 500)
    uv, z = project(intr, pose, pts)
    back = transform(pose, backproject(intr, uv[:, 0], uv[:, 1], z))
    assert np.allclose(back, pts, atol=1e-9)
    assert np.allclose(pose_inverse(pose_inverse(pose)), pose)
    assert np.allclose(pose_inverse(pose) @ pose, np.eye(4), atol=1e-12)


def test_intrinsics_scaled_keeps_the_field_of_view() -> None:
    intr = Intrinsics(1000.0, 1000.0, 639.5, 479.5, 1280, 960)
    small = intr.scaled(256, 192)
    assert small.cx == pytest.approx(127.5) and small.cy == pytest.approx(95.5)
    back = small.scaled(1280, 960)
    for a, b in zip(back.to_json().values(), intr.to_json().values(), strict=True):
        assert a == pytest.approx(b)
    # the image corner maps to the same ray at both resolutions
    r1 = backproject(intr, np.array([-0.5]), np.array([-0.5]), np.array([1.0]))
    r2 = backproject(small, np.array([-0.5]), np.array([-0.5]), np.array([1.0]))
    assert np.allclose(r1, r2)


def test_arkit_pose_conversion() -> None:
    rng = np.random.default_rng(3)
    pose_cv = _random_pose(rng)
    pose_ar = pose_cv @ np.linalg.inv(ARKIT_TO_OPENCV)  # what ARKit would report
    assert np.allclose(pose_from_arkit(pose_ar), pose_cv)
    # ARKit looks down its -z: the OpenCV +z is the ARKit -z
    assert np.allclose(pose_ar[:3, 2], -pose_cv[:3, 2])


def test_look_at_points_the_camera() -> None:
    pose = look_at(np.array([0.0, 1.0, 3.0]), np.array([0.0, 1.0, 0.0]), UP)
    intr = Intrinsics(1000.0, 1000.0, 640.0, 480.0, 1280, 960)
    uv, z = project(intr, pose, np.array([[0.0, 1.0, 0.0], [0.0, 1.5, 0.0], [0.5, 1.0, 0.0]]))
    assert np.allclose(uv[0], [640, 480]) and z[0] == pytest.approx(3.0)
    assert uv[1, 1] < 480  # world up is image up
    assert uv[2, 0] > 640  # world right is image right
    assert rotation_angle_deg(pose[:3, :3] @ pose[:3, :3].T) == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------- planes


def test_fit_and_ransac_plane_with_outliers() -> None:
    rng = np.random.default_rng(0)
    n = np.array([0.2, -0.1, 1.0])
    n /= np.linalg.norm(n)
    a = np.cross(n, [1, 0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    uv = rng.uniform(-1, 1, (2000, 2))
    pts = uv[:, :1] * a + uv[:, 1:] * b + 0.4 * n + rng.normal(0, 0.003, (2000, 1)) * n
    pts[:300] += rng.uniform(-0.5, 0.5, (300, 3))
    nn, d, mask = ransac_plane(pts, 0.01, rng)  # type: ignore[misc]
    assert abs(abs(float(nn @ n)) - 1) < 1e-4
    assert abs(abs(d) - 0.4) < 0.002
    assert mask[300:].mean() > 0.95 and mask[:300].mean() < 0.2
    assert flatness(pts[300:]) == pytest.approx(0.003, rel=0.15)
    n2, d2 = fit_plane(pts[300:])
    assert abs(float(n2 @ n)) > 0.9999


def test_segment_planes_separates_a_box_from_its_wall() -> None:
    rng = np.random.default_rng(1)
    pts = _scene_points(rng, 6000)
    pts = pts + rng.normal(0, 0.002, pts.shape)
    planes, labels = segment_planes(pts, threshold=0.01, min_fraction=0.05)
    normals = [p.normal for p in planes]
    assert len(planes) == 3
    assert any(abs(n[2]) > 0.999 and abs(p.d) < 0.01 for n, p in zip(normals, planes, strict=True))
    assert any(
        abs(n[2]) > 0.999 and abs(abs(p.d) - 0.35) < 0.01
        for n, p in zip(normals, planes, strict=True)
    )
    assert any(abs(n[0]) > 0.999 for n in normals)
    assert (labels >= 0).mean() > 0.95


def test_largest_patch_keeps_one_piece() -> None:
    rng = np.random.default_rng(2)
    a = np.c_[rng.uniform(0, 1, 800), rng.uniform(0, 1, 800), np.zeros(800)]
    b = np.c_[rng.uniform(3, 3.5, 300), rng.uniform(0, 1, 300), np.zeros(300)]
    keep = largest_patch(np.concatenate([a, b]), np.array([0.0, 0.0, 1.0]), 0.05)
    assert keep[:800].all() and not keep[800:].any()


def test_ray_plane_depth() -> None:
    plane = Plane(np.array([0.0, 0.0, 1.0]), -0.5, 10, 0.0)  # z = 0.5
    rays = np.array([[0.0, 0.0, -1.0], [1.0, 0.0, -1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    s = ray_plane_depth(rays, np.array([0.0, 0.0, 2.0]), plane)
    assert s[0] == pytest.approx(1.5) and s[1] == pytest.approx(1.5)
    assert math.isnan(s[2]) and math.isnan(s[3])  # parallel, behind


# ---------------------------------------------------------------- projector


@pytest.mark.parametrize("seed", range(6))
def test_dlt_and_decompose_are_exact(seed: int) -> None:
    rng = np.random.default_rng(seed)
    pose = _random_pose(rng)
    x3, x2 = _visible(PROJ, pose, _scene_points(rng, 600))
    p = dlt(x3, x2)
    assert np.abs(project_p(p, x3) - x2).max() < 1e-6
    k, pose2 = decompose(p)
    assert np.allclose(k, PROJ.k, rtol=1e-6, atol=1e-5)
    assert np.allclose(pose2, pose, atol=1e-6)


@pytest.mark.parametrize("seed", range(4))
def test_solve_projector_exact_data(seed: int) -> None:
    rng = np.random.default_rng(10 + seed)
    pose = _random_pose(rng)
    x3, x2 = _visible(PROJ, pose, _scene_points(rng))
    sol = solve_projector(x3, x2, (1920, 1080))
    assert sol.status == "ok", sol.message
    err = compare_to_truth(sol, PROJ.k, pose)
    assert err["fx_rel"] < 1e-6 and err["cx_px"] < 1e-4 and err["cy_px"] < 1e-4
    assert err["position_m"] < 1e-6 and err["rotation_deg"] < 1e-5
    assert sol.median_px < 1e-6 and sol.inlier_fraction == 1.0


def test_solve_projector_noise_and_outliers() -> None:
    rng = np.random.default_rng(20)
    pose = _random_pose(rng)
    x3, x2 = _visible(PROJ, pose, _scene_points(rng))
    noisy = x2 + rng.normal(0, 0.3, x2.shape)
    bad = rng.choice(len(x2), len(x2) // 10, replace=False)
    noisy[bad] += rng.uniform(20, 80, (len(bad), 2)) * rng.choice([-1, 1], (len(bad), 2))
    sol = solve_projector(x3, noisy, (1920, 1080))
    assert sol.status == "ok"
    assert 0.88 < sol.inlier_fraction < 0.92
    assert sol.median_px < 0.5 and sol.p95_px < 1.0
    assert sol.inliers is not None and not sol.inliers[bad].any()
    err = compare_to_truth(sol, PROJ.k, pose)
    assert err["fx_rel"] < 0.01 and err["position_m"] < 0.02
    js = sol.to_json()
    assert js["status"] == "ok" and js["intrinsics"]["width"] == 1920


def test_planar_points_are_reported_as_under_constrained() -> None:
    rng = np.random.default_rng(30)
    pose = _random_pose(rng)
    x3, x2 = _visible(PROJ, pose, _scene_points(rng, box=False))
    assert relief(x3) < 1e-9
    sol = solve_projector(x3, x2, (1920, 1080))
    assert sol.status == "planar"
    assert "under-constrained" in sol.message
    assert not sol.usable and sol.intrinsics is None


def test_too_few_points() -> None:
    sol = solve_projector(np.zeros((5, 3)), np.zeros((5, 2)), (1920, 1080))
    assert sol.status == "too_few" and not sol.usable


def test_little_relief_is_flagged_weak() -> None:
    rng = np.random.default_rng(40)
    pose = _random_pose(rng)
    wall = np.c_[rng.uniform(-1.2, 1.2, 4000), rng.uniform(-0.2, 1.4, 4000), np.zeros(4000)]
    bump = np.c_[rng.uniform(-0.05, 0.05, 40), rng.uniform(0.5, 0.6, 40), np.full(40, 0.05)]
    x3, x2 = _visible(PROJ, pose, np.concatenate([wall, bump]))
    sol = solve_projector(x3, x2 + rng.normal(0, 0.5, x2.shape), (1920, 1080))
    assert sol.status in ("weak", "planar")  # never a confident "ok" on 1 % relief
    assert not (sol.status == "weak") or "weakly constrained" in sol.message


def test_noise_larger_than_the_relief_is_planar() -> None:
    rng = np.random.default_rng(41)
    pose = _random_pose(rng)
    x3, x2 = _visible(PROJ, pose, _scene_points(rng))
    noisy3 = x3 + rng.normal(0, 0.25, x3.shape) * np.array([0, 0, 1])  # 25 cm depth noise
    sol = solve_projector(noisy3, x2, (1920, 1080))
    assert sol.status in ("planar", "failed")
