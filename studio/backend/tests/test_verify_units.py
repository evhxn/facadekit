"""Unit and property tests for the pieces of "Verify surface" (docs/13)."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from facadekit.calib import rigcheck as rc
from facadekit.calib import surfacecheck as sc
from facadekit.calib.p2cmap import ProjCamMap, densify
from facadekit.calib.verify import Injection, grow_polygon
from facadekit.calib.verify_capture import RATIO_FLOOR, Exposure, Shot, ratio_image
from facadekit.geom.homography import apply_h

PROJ = (480, 270)


def _plane_map(h_p2c: np.ndarray, size: tuple[int, int] = PROJ) -> ProjCamMap:
    w, h = size
    xs, ys = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    pts = np.stack([xs.ravel(), ys.ravel()], axis=1)
    cam = apply_h(h_p2c, pts).reshape(h, w, 2).astype(np.float32)
    return ProjCamMap(p2c=cam, measured=np.ones((h, w), bool), h_c2p=np.linalg.inv(h_p2c))


H_TILT = np.array([[0.62, 0.05, 30.0], [-0.03, 0.58, 20.0], [2e-5, -1e-5, 1.0]])


# --- p2cmap ---------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_to_proj_inverts_to_cam(seed: int) -> None:
    pmap = _plane_map(H_TILT)
    rng = np.random.default_rng(seed)
    pts = rng.uniform([10, 10], [PROJ[0] - 10, PROJ[1] - 10], (50, 2))
    cam, ok = pmap.to_cam(pts)
    assert ok.all()
    np.testing.assert_allclose(
        cam, apply_h(H_TILT, pts), atol=0.02
    )  # bilinear on a perspective map
    back, ok2 = pmap.to_proj(cam)
    assert ok2.all()
    np.testing.assert_allclose(back, pts, atol=0.02)


def test_to_cam_is_invalid_where_not_measured() -> None:
    pmap = _plane_map(H_TILT)
    pmap.measured[:, :100] = False
    _, ok = pmap.to_cam(np.array([[50.0, 100.0], [300.0, 100.0], [-5.0, 10.0]]))
    assert ok.tolist() == [False, True, False]


def test_scale_and_densify() -> None:
    pmap = _plane_map(np.diag([0.5, 0.5, 1.0]))
    assert pmap.scale_at(np.array([[100.0, 100.0]]))[0] == pytest.approx(0.5, abs=1e-3)
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    d = densify(sq, 2.0)
    assert len(d) == 20 and np.allclose(d[0], [0, 0])


# --- dots -----------------------------------------------------------------------------------


def _disc_image(points: np.ndarray, radius: float, size: tuple[int, int]) -> np.ndarray:
    img = rc.render_dots(size, points, radius).astype(np.float32) / 255.0
    return np.asarray(cv2.GaussianBlur(img, (0, 0), 0.8))


@pytest.mark.parametrize("seed", range(4))
def test_find_dots_is_subpixel(seed: int) -> None:
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(40, 300, 52.0), np.arange(40, 300, 52.0))
    pts = np.stack([gx.ravel(), gy.ravel()], 1) + rng.uniform(-8, 8, (gx.size, 2))
    q = _disc_image(pts, 4.0, (320, 320))
    found = rc.find_dots(q, np.ones_like(q, bool), 4.0)
    assert len(found) == len(pts)
    d = np.min(np.linalg.norm(found[:, None] - pts[None], axis=2), axis=1)
    assert float(np.max(d)) < 0.12


@pytest.mark.parametrize("seed", range(6))
def test_match_dots_recovers_motion(seed: int) -> None:
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(60, 1220, 110), np.arange(60, 660, 110))
    expected = np.stack([gx.ravel(), gy.ravel()], 1) + rng.uniform(-30, 30, (gx.size, 2))
    shift = rng.uniform(-40, 40, 2)
    t = math.radians(rng.uniform(-1.5, 1.5))
    h = np.array(
        [[math.cos(t), -math.sin(t), shift[0]], [math.sin(t), math.cos(t), shift[1]], [0, 0, 1]]
    )
    observed = apply_h(h, expected) + rng.normal(0, 0.1, expected.shape)
    keep = rng.uniform(size=len(observed)) > 0.15  # some dots not seen
    noise = rng.uniform([0, 0], [1280, 720], (5, 2))  # and some stray blobs
    obs = np.vstack([observed[keep], noise])
    m = rc.match_dots(expected, obs, (1280, 720), 110.0)
    assert len(m.idx_expected) >= int(keep.sum()) - 2
    assert m.h is not None
    err = np.linalg.norm(apply_h(m.h, expected) - apply_h(h, expected), axis=1)
    assert float(err.max()) < 0.3


def test_match_dots_needs_enough_points() -> None:
    m = rc.match_dots(np.zeros((2, 2)), np.zeros((2, 2)), (100, 100), 10.0)
    assert m.h is None and len(m.idx_expected) == 0


# --- camera motion from texture -------------------------------------------------------------


def _wall(size: tuple[int, int], seed: int, flat: bool = False) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w, h = size
    if flat:
        return np.full((h, w), 120.0, np.float32)
    small = rng.uniform(60, 200, (h // 6, w // 6)).astype(np.float32)
    img = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    return np.asarray(np.clip(img, 1, 255), dtype=np.float32)


@pytest.mark.parametrize("shift", [(0.0, 0.0), (2.4, -1.3), (7.0, 3.5)])
def test_camera_motion_measures_a_shift(shift: tuple[float, float]) -> None:
    ref = _wall((640, 400), 3)
    m = np.array([[1, 0, shift[0]], [0, 1, shift[1]]], dtype=np.float64)
    cur = cv2.warpAffine(ref, m, (640, 400), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    lit = np.zeros((400, 640), bool)
    lit[40:360, 40:600] = True
    rh, rm = rc.texture(ref, lit)
    ch, cm = rc.texture(cur * 0.6, lit)  # dimmer: the texture must not care
    motion = rc.camera_motion(rh, rm, ch, cm)
    assert motion is not None
    p = np.array([[320.0, 200.0]])
    moved = apply_h(motion.h, p)[0] - p[0]
    assert np.allclose(moved, shift, atol=0.1)


def test_camera_motion_unknown_on_a_flat_wall() -> None:
    flat = _wall((640, 400), 0, flat=True)
    rng = np.random.default_rng(1)
    a = flat + rng.normal(0, 1.0, flat.shape).astype(np.float32)
    b = flat + rng.normal(0, 1.0, flat.shape).astype(np.float32)
    lit = np.ones((400, 640), bool)
    rh, rm = rc.texture(a, lit)
    ch, cm = rc.texture(b, lit)
    assert rc.camera_motion(rh, rm, ch, cm) is None


def test_plausible() -> None:
    assert rc.plausible(np.eye(3))
    assert not rc.plausible(np.zeros((3, 3)))
    assert not rc.plausible(np.diag([3.0, 3.0, 1.0]))
    assert not rc.plausible(np.diag([-1.0, 1.0, 1.0]))


def test_dot_layout_is_deterministic_and_measured() -> None:
    pmap = _plane_map(H_TILT, (1920, 1080))
    pmap.measured[:, :400] = False
    a = rc.dot_layout(pmap, 0.6)
    b = rc.dot_layout(pmap, 0.6)
    assert np.array_equal(a.points, b.points)
    assert len(a.points) > 40
    assert float(a.points[:, 0].min()) > 400 + a.radius
    assert a.radius >= rc.DOT_MIN_CAM_RADIUS / 0.6 - 1e-9


# --- ratio image ----------------------------------------------------------------------------


def _shot(gray: np.ndarray) -> Shot:
    return Shot(gray=gray.astype(np.float32), bgr=np.dstack([gray] * 3).astype(np.uint8))


def test_ratio_image_cancels_colour_and_ambient() -> None:
    albedo = np.tile(np.linspace(0.2, 1.0, 64, dtype=np.float32), (32, 1))
    ambient = 20.0 * albedo
    black = _shot(ambient)
    white = _shot(ambient + 200 * albedo)
    half = np.zeros_like(albedo)
    half[:, :32] = 1
    fill = _shot(ambient + 200 * albedo * half)
    exp = Exposure(black, white, True, 1.0, 200, 100, np.ones_like(albedo, bool))
    q, valid = ratio_image(fill, exp)
    assert valid.all()
    assert np.allclose(q[:, :32], 1.0, atol=1e-4) and np.allclose(q[:, 32:], 0.0, atol=1e-4)
    dark = _shot(np.zeros_like(albedo) + RATIO_FLOOR / 2)
    exp2 = Exposure(black, dark, True, 1.0, 200, 100, np.ones_like(albedo, bool))
    _, valid2 = ratio_image(fill, exp2)
    assert not valid2.any()


# --- surface geometry -----------------------------------------------------------------------

SQUARE = np.array([[100, 100], [300, 100], [300, 200], [100, 200]], dtype=float)


def test_outward_normals_do_not_depend_on_winding() -> None:
    n1 = sc.outward_normals(SQUARE)
    n2 = sc.outward_normals(SQUARE[::-1])
    assert [sc.side_name(n) for n in n1] == ["top", "right", "bottom", "left"]
    assert sorted(sc.side_name(n) for n in n2) == ["bottom", "left", "right", "top"]


@pytest.mark.parametrize("grow", [-6.0, 3.0, 12.5])
def test_grow_polygon_moves_every_side(grow: float) -> None:
    g = grow_polygon(SQUARE, grow)
    np.testing.assert_allclose(g[0], SQUARE[0] + [-grow, -grow])
    np.testing.assert_allclose(g[2], SQUARE[2] + [grow, grow])


def test_ends_fit_a_line_and_ignore_outliers() -> None:
    u = np.linspace(0.15, 0.85, 20)
    v = 2.0 + 10.0 * (u - 0.5)
    v[3] = 40.0  # one bad sample
    lo, hi = sc._ends(u, v)
    expect = sorted(2.0 + 10.0 * (np.percentile(u, q) - 0.5) for q in (10, 90))
    assert lo == pytest.approx(expect[0], abs=0.2) and hi == pytest.approx(expect[1], abs=0.2)


def test_crossing_is_subpixel_and_nearest() -> None:
    t = np.arange(-10, 10.01, 0.5)
    prof = np.where(t < 2.25, 1.0, 0.0)
    assert sc._crossing(prof, t, 0.5) == pytest.approx(2.25, abs=0.26)
    assert sc._crossing(np.zeros_like(t), t, 0.5) is None


def _samples(quad: np.ndarray, spill: dict[str, float]) -> list[sc.EdgeSample]:
    out = []
    normals = sc.outward_normals(quad)
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        side = sc.side_name(normals[i])
        for u in np.linspace(0.15, 0.85, 10):
            out.append(
                sc.EdgeSample(
                    side,
                    a + u * (b - a),
                    normals[i],
                    0.0,
                    0.0,
                    spill.get(side, 0.0),
                    False,
                    float(u),
                    i,
                )
            )
    return out


def test_suggest_quad_moves_the_spilling_sides_back() -> None:
    samples = _samples(SQUARE, {"left": 6.0, "top": -4.0})
    edges = sc.summarize_edges(samples)
    sug = sc.suggest(sc.Shape(SQUARE, [], True), SQUARE, samples, edges)
    assert sug.quad is not None
    q = np.array(sug.quad)
    np.testing.assert_allclose(q[0], [106, 96], atol=1e-6)  # left in by 6, top out by 4
    np.testing.assert_allclose(q[2], [300, 200], atol=1e-6)


def test_suggest_quad_refuses_large_or_needless_changes() -> None:
    small = _samples(SQUARE, {"left": 1.0})
    shape = sc.Shape(SQUARE, [], True)
    assert sc.suggest(shape, SQUARE, small, sc.summarize_edges(small)).quad is None
    big = _samples(SQUARE, {"left": 80.0})
    sug = sc.suggest(shape, SQUARE, big, sc.summarize_edges(big))
    assert sug.quad is None and "too large" in sug.reason


def test_suggest_moves_a_mask_and_its_quad() -> None:
    hexagon = np.array(
        [[100, 100], [200, 80], [300, 100], [300, 200], [200, 220], [100, 200]], dtype=float
    )
    samples = []
    normals = sc.outward_normals(hexagon)
    for i in range(6):
        a, b = hexagon[i], hexagon[(i + 1) % 6]
        for u in np.linspace(0.15, 0.85, 8):
            samples.append(
                sc.EdgeSample(
                    sc.side_name(normals[i]),
                    a + u * (b - a),
                    normals[i],
                    0.0,
                    0.0,
                    5.0,
                    False,
                    float(u),
                    i,
                )
            )
    edges = sc.summarize_edges(samples)
    sug = sc.suggest(sc.Shape(hexagon, [], False), SQUARE, samples, edges)
    assert sug.mask is not None and sug.quad is not None
    from facadekit.calib.verify import grow_polygon

    np.testing.assert_allclose(np.array(sug.mask.outer), grow_polygon(hexagon, -5.0), atol=1e-6)
    np.testing.assert_allclose(np.array(sug.quad), grow_polygon(SQUARE, -5.0), atol=1e-6)


def test_injection_flags() -> None:
    assert not Injection().active
    assert Injection(grow_px=2).active and Injection(cam_shift=(1, 0)).active
