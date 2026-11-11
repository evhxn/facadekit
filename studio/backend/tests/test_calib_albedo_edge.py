"""Albedo-edge bias of the projector map.

See docs/07-scan-memory.md, "Albedo-edge bias in the projector map".

Next to a strong reflectance edge the camera blurs albedo x stripe, which moves every stripe
edge's zero crossing towards the darker side. Before the fix this put a dipole of map error
(about +-0.3 camera px) across the door frame of the simulator scene.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np
import pytest

from facadekit.calib.capture import run_capture
from facadekit.calib.decode import DecodeResult, decode
from facadekit.calib.maps import albedo_edge_weight, build_maps
from facadekit.calib.patterns import PatternSpec
from facadekit.geom.homography import apply_h
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene

FAST = CalibSettings(discard_frames=0, average_frames=1)


@dataclass
class PanelScene(SimScene):
    """Plain grey wall with one bright panel in a 6 px dark frame; `flat`: grey wall only."""

    flat: bool = False

    def _build_albedo(self) -> np.ndarray:
        ww, wh = self.world_size
        img = np.full((wh, ww, 3), 110, np.uint8)
        if not self.flat:
            x0, y0, x1, y1 = (int(v) for v in self.features_world["garage_door"])
            img[y0 - 6 : y1 + 6, x0 - 6 : x1 + 6] = 30
            img[y0:y1, x0:x1] = 210
        return img


def _scan(scene: SimScene) -> tuple[DecodeResult, PatternSpec]:
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    cap = run_capture(display, cam, PatternSpec(*scene.proj_size), FAST, sleep=lambda _s: None)
    return decode(cap), cap.spec


def _edge_profiles(scene: SimScene, p2c: np.ndarray) -> np.ndarray:
    """(4 edges, 12 bins) mean map error along each door edge's outward normal (camera px),
    in 4 projector-px bins from 24 px inside to 24 px outside, middle 70 % of each edge."""
    w, h = scene.proj_size
    xs, ys = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    truth = apply_h(scene.h_p2c, np.stack([xs, ys], -1).reshape(-1, 2)).reshape(h, w, 2)
    err = p2c.astype(np.float64) - truth
    q = np.array(scene.feature_quad_proj("garage_door"))
    rows = []
    for a, b in ((q[3], q[0]), (q[0], q[1]), (q[1], q[2]), (q[2], q[3])):
        ab = b - a
        length = float(np.linalg.norm(ab))
        t = ((xs - a[0]) * ab[0] + (ys - a[1]) * ab[1]) / length**2
        n = np.array([ab[1], -ab[0]]) / length  # outward (corners run clockwise on screen)
        d = (xs - a[0]) * n[0] + (ys - a[1]) * n[1]
        c0, c1 = apply_h(scene.h_p2c, np.array([(a + b) / 2, (a + b) / 2 + n]))
        cn = (c1 - c0) / np.linalg.norm(c1 - c0)
        e_n = err[..., 0] * cn[0] + err[..., 1] * cn[1]
        along = (t > 0.15) & (t < 0.85)
        rows.append(
            [float(e_n[along & (d >= lo) & (d < lo + 4)].mean()) for lo in range(-24, 24, 4)]
        )
    return np.array(rows)


def _peak_to_peak(profiles: np.ndarray) -> float:
    return float((profiles.max(axis=1) - profiles.min(axis=1)).max())


def _build_maps_before_fix(
    decoded: DecodeResult, spec: PatternSpec
) -> tuple[np.ndarray, np.ndarray]:
    """p2c of the node estimator used until 2026-11-11 (kept as the benchmark reference):
    unweighted mean of the pixels binned to each node, moved to the node's projector position
    with the global homography, holes filled, 3x3 median, bilinear upsample. Returns (p2c,
    measured)."""
    g = max(8, 2 << spec.skip_lsb)
    vs, us = np.nonzero(decoded.valid)
    cam_u, cam_v = us + 0.5, vs + 0.5
    px = decoded.c2p[vs, us, 0].astype(np.float64)
    py = decoded.c2p[vs, us, 1].astype(np.float64)
    gw, gh = spec.width // g + 1, spec.height // g + 1
    j = np.clip(np.round(px / g), 0, gw - 1).astype(np.int64)
    i = np.clip(np.round(py / g), 0, gh - 1).astype(np.int64)
    flat, n = i * gw + j, gw * gh
    counts = np.bincount(flat, minlength=n).reshape(gh, gw)
    sums = [
        np.bincount(flat, weights=v, minlength=n).reshape(gh, gw) for v in (cam_u, cam_v, px, py)
    ]
    meas = counts > 0
    zero = np.zeros((gh, gw))
    grid_u = np.divide(sums[0], counts, out=zero.copy(), where=meas).astype(np.float32)
    grid_v = np.divide(sums[1], counts, out=zero.copy(), where=meas).astype(np.float32)
    pick = np.random.default_rng(0).choice(len(us), min(20000, len(us)), replace=False)
    h_c2p, _ = cv2.findHomography(
        np.stack([cam_u[pick], cam_v[pick]], 1), np.stack([px[pick], py[pick]], 1), cv2.RANSAC, 6.0
    )
    inv_h = np.linalg.inv(h_c2p)
    mi, mj = np.nonzero(meas)
    mean_p = np.stack([sums[2][mi, mj], sums[3][mi, mj]], 1) / counts[mi, mj][:, None]
    node_p = np.stack([mj * g, mi * g], 1).astype(np.float64)
    delta = apply_h(inv_h, node_p) - apply_h(inv_h, mean_p)
    grid_u[mi, mj] += delta[:, 0].astype(np.float32)
    grid_v[mi, mj] += delta[:, 1].astype(np.float32)
    wt = meas.astype(np.float32)
    bw = cv2.GaussianBlur(wt, (0, 0), 2.0)
    near = (~meas) & (bw > 0.05)
    grid_u[near] = (cv2.GaussianBlur(grid_u * wt, (0, 0), 2.0) / np.maximum(bw, 1e-6))[near]
    grid_v[near] = (cv2.GaussianBlur(grid_v * wt, (0, 0), 2.0) / np.maximum(bw, 1e-6))[near]
    far = (~meas) & (~near)
    if far.any():
        fi, fj = np.nonzero(far)
        cam = apply_h(inv_h, np.stack([fj * g, fi * g], 1).astype(np.float64))
        grid_u[far], grid_v[far] = cam[:, 0], cam[:, 1]
    grid_u, grid_v = cv2.medianBlur(grid_u, 3), cv2.medianBlur(grid_v, 3)
    xs, ys = np.meshgrid(np.arange(spec.width), np.arange(spec.height))
    gx = ((xs + 0.5) / g).astype(np.float32)
    gy = ((ys + 0.5) / g).astype(np.float32)
    u = cv2.remap(grid_u, gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    v = cv2.remap(grid_v, gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    nj = np.clip(np.round((xs + 0.5) / g), 0, gw - 1).astype(np.int64)
    ni = np.clip(np.round((ys + 0.5) / g), 0, gh - 1).astype(np.int64)
    return np.stack([u, v], axis=-1).astype(np.float32), meas[ni, nj]


@pytest.fixture(scope="module")
def panel() -> tuple[PanelScene, DecodeResult, PatternSpec]:
    scene = PanelScene()
    decoded, spec = _scan(scene)
    return scene, decoded, spec


def test_edge_weight_is_brightness_free_and_marks_edges() -> None:
    contrast = np.full((40, 60), 150.0, np.float32)
    contrast[:, 30:] = 25.0
    contrast = cv2.GaussianBlur(contrast, (0, 0), 1.0)
    w = albedo_edge_weight(contrast)
    assert w[:, :20].min() > 0.99 and w[:, 40:].min() > 0.99
    assert w[5:-5, 29:31].max() < 0.05
    assert np.allclose(albedo_edge_weight(contrast * 3.0), w, atol=1e-4)


def test_albedo_edge_dipole_reduced(panel: tuple[PanelScene, DecodeResult, PatternSpec]) -> None:
    scene, decoded, spec = panel
    before = _edge_profiles(scene, _build_maps_before_fix(decoded, spec)[0])
    after = _edge_profiles(scene, build_maps(decoded, spec).p2c)
    # before: about +0.3 camera px just inside every edge, -0.15 just outside
    assert _peak_to_peak(before) > 0.3
    assert _peak_to_peak(before) >= 3 * _peak_to_peak(after), (before.round(2), after.round(2))
    assert np.abs(after).max() < 0.1


def test_flat_albedo_has_no_bias() -> None:
    scene = PanelScene(flat=True, noise_sigma=0.0)
    decoded, spec = _scan(scene)
    maps = build_maps(decoded, spec)
    profiles = _edge_profiles(scene, maps.p2c)
    assert np.abs(profiles).max() < 0.06
    assert abs(float(profiles.mean())) < 0.02
    # a flat wall gives every decoded pixel full weight (bar the projection's own border)
    w = albedo_edge_weight(decoded.contrast)[decoded.valid]
    assert float(np.median(w)) > 0.99


def test_runtime_close_to_before(panel: tuple[PanelScene, DecodeResult, PatternSpec]) -> None:
    _scene, decoded, spec = panel

    def best(fn: Callable[[], object]) -> float:
        times = []
        for _ in range(5):
            t = time.perf_counter()
            fn()
            times.append(time.perf_counter() - t)
        return min(times)

    build_maps(decoded, spec)  # warm-up
    new = best(lambda: build_maps(decoded, spec))
    old = best(lambda: _build_maps_before_fix(decoded, spec))
    # build_maps alone (decode is unchanged): at most 25 % slower, plus timer slack
    assert new <= 1.25 * old + 0.005, (new, old)


def test_follows_lens_distortion_not_the_plane_fit() -> None:
    """Strong barrel distortion: the best homography is off by 1-3 camera px, the map is not."""
    wc, hc, k1 = 1280, 720, 0.08
    spec = PatternSpec(1920, 1080)
    h_c2p = np.array([[1.45, 0.03, 30.0], [0.01, 1.45, 20.0], [1e-5, -1e-5, 1.0]])
    centre = np.array([wc / 2, hc / 2])

    def cam_to_proj(c: np.ndarray) -> np.ndarray:
        d = (c - centre) / wc
        return apply_h(h_c2p, centre + d * (1 + k1 * (d**2).sum(-1, keepdims=True)) * wc)

    uu, vv = np.meshgrid(np.arange(wc) + 0.5, np.arange(hc) + 0.5)
    p = cam_to_proj(np.stack([uu, vv], -1).reshape(-1, 2)).reshape(hc, wc, 2)
    valid = (p[..., 0] >= 0) & (p[..., 0] < 1920) & (p[..., 1] >= 0) & (p[..., 1] < 1080)
    c2p = (np.floor(p / 4) * 4 + 2).astype(np.float32)  # decoded block centres (skip_lsb 2)
    c2p[~valid] = np.nan
    maps = build_maps(DecodeResult(c2p, valid, np.full((hc, wc), 100, np.float32), 1, 100), spec)
    assert maps.h_c2p is not None

    xs, ys = np.meshgrid(np.arange(200, 1700, 37), np.arange(150, 950, 41))
    target = np.stack([xs + 0.5, ys + 0.5], -1).reshape(-1, 2)
    got = maps.p2c[ys, xs].reshape(-1, 2).astype(np.float64)
    truth = got.copy()  # Newton: camera point whose projector point is the target
    for _ in range(10):
        f0 = cam_to_proj(truth)
        jx = (cam_to_proj(truth + [1e-3, 0]) - f0) / 1e-3
        jy = (cam_to_proj(truth + [0, 1e-3]) - f0) / 1e-3
        truth += np.linalg.solve(np.stack([jx, jy], -1), (target - f0)[..., None])[..., 0]
    plane = apply_h(np.linalg.inv(maps.h_c2p), target)
    assert np.linalg.norm(plane - truth, axis=1).max() > 2.0
    err = np.linalg.norm(got - truth, axis=1)
    assert err.mean() < 0.08 and err.max() < 0.2, (err.mean(), err.max())
