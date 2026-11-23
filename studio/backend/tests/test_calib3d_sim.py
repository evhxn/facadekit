"""calib3d end to end on the 3D simulator (S-11 Part A). Simulated data only."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from facadekit.calib3d.capture_format import load_capture
from facadekit.calib3d.fusion import fuse
from facadekit.calib3d.solve import (
    SolveOptions,
    build_mapping,
    decode_capture,
    projector_warp,
    solve_capture,
)
from facadekit.cli import main
from facadekit.sim.calib3d_eval import BASE_SEED, dense_predict, eval_points, run_scene
from facadekit.sim.calib3d_report import report_markdown
from facadekit.sim.scene3d import (
    SCENE_KINDS,
    DepthSensorModel,
    make_scene,
    write_session,
)

# train-split seeds (index % 3 != 0), as the evaluation uses them
BOX_SEED = BASE_SEED + SCENE_KINDS.index("box") * 10_000 + 2
FLAT_SEED = BASE_SEED + SCENE_KINDS.index("flat") * 10_000 + 1


@pytest.fixture(scope="module")
def box_capture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_session(make_scene("box", BOX_SEED), tmp_path_factory.mktemp("box"))


# ---------------------------------------------------------------- simulator


def test_depth_sensor_model() -> None:
    scene = make_scene("box", 5, distance_m=2.0)
    rng = np.random.default_rng(0)
    depth, conf, intr = scene.render_depth(scene.cam_pose, DepthSensorModel(), rng)
    assert depth.shape == (192, 256) and conf.shape == (192, 256)
    assert (intr.width, intr.height) == (256, 192)
    truth = scene.view(intr, scene.cam_pose)["z"]
    ok = (conf == 2) & (depth > 0)
    err = np.abs(depth[ok] - truth[ok])
    assert 0.002 < float(np.median(err)) < 0.03  # centimetre-level, ASSUMED
    assert set(np.unique(conf)) <= {0, 1, 2}
    assert (conf[depth == 0] == 0).all()  # no data is never confident
    # deterministic for a seed
    d2, c2, _ = scene.render_depth(scene.cam_pose, DepthSensorModel(), np.random.default_rng(0))
    assert np.array_equal(depth, d2) and np.array_equal(conf, c2)


def test_depth_sensor_range_and_materials() -> None:
    far = make_scene("flat", 5, distance_m=7.0)
    depth, conf, _ = far.render_depth(far.cam_pose, DepthSensorModel(), np.random.default_rng(1))
    assert (depth == 0).all() and (conf == 0).all()  # beyond 5 m: nothing
    dark = make_scene("box", 5, distance_m=2.0, object_material="dark")
    d, c, intr = dark.render_depth(dark.cam_pose, DepthSensorModel(), np.random.default_rng(1))
    prim = dark.view(intr, dark.cam_pose)["prim"]
    on_box = dark.object_of(prim) == "box"
    assert (d[on_box] == 0).mean() > 0.4  # dark paint drops out
    assert (d[~on_box] == 0).mean() < 0.1


def test_flying_pixels_at_depth_edges() -> None:
    scene = make_scene("box", 7, distance_m=2.0)
    model = DepthSensorModel(noise_scale=0.0, corr_sigma_m=0.0, dropout_base=0.0)
    depth, conf, intr = scene.render_depth(scene.cam_pose, model, np.random.default_rng(2))
    truth = scene.view(intr, scene.cam_pose)["z"]
    err = np.abs(depth - truth)
    # quantisation only, except small depth steps inside one pixel (below the flying threshold)
    assert np.mean(err[conf == 2] < 0.002) > 0.999
    assert ((conf < 2) & (err > 0.02)).sum() > 10  # mixed edge pixels, flagged


def test_scene_ground_truth_is_consistent() -> None:
    scene = make_scene("cylinder", 9)
    truth = scene.camera_truth()
    lit = truth["lit"]
    assert 0.1 < lit.mean() < 0.8
    # q is the projection of the surface point through the projector
    from facadekit.calib3d.geometry import project

    vs, us = np.nonzero(lit)
    pick = slice(None, None, 997)
    q, _ = project(scene.proj_intr, scene.proj_pose, truth["points"][vs[pick], us[pick]])
    assert np.allclose(q, truth["q"][vs[pick], us[pick]])


# ---------------------------------------------------------------- pipeline


def test_end_to_end_box(box_capture: Path) -> None:
    cap = load_capture(box_capture)
    assert cap.ground_truth is not None and cap.ground_truth["source"] == "simulator"
    opts = SolveOptions()
    dec, corr, _ = decode_capture(cap, opts)
    surface = fuse(cap, opts.fusion)
    assert surface.stats["coverage"] > 0.9 and len(surface.planes) >= 2
    mapping = build_mapping(corr, surface, (1920, 1080), opts)
    sol = mapping.solution
    assert sol.status == "ok", sol.message
    assert sol.median_px < 1.0 and sol.inlier_fraction > 0.9
    scene = make_scene("box", BOX_SEED)
    cam, q_true, obj = eval_points(scene, scene.camera_truth())
    box = obj == "box"
    err = {
        m: np.linalg.norm(mapping.predict(cam, m) - q_true, axis=1)
        for m in ("plane", "model", "hybrid")
    }
    hyb, plane = np.median(err["hybrid"][box]), np.median(err["plane"][box])
    assert hyb < 0.3 and plane > 3 * hyb  # simulator, not a device
    assert np.percentile(err["hybrid"][~box], 95) < 1.0
    # the dense map agrees where the scan has data
    from facadekit.calib.maps import build_maps

    p2c = build_maps(dec, cap.graycode.pattern).p2c  # type: ignore[arg-type]
    dense = np.linalg.norm(
        dense_predict(p2c, cam[~box], mapping.predict(cam[~box], "plane")) - q_true[~box], axis=1
    )
    assert np.median(dense) < 0.5


def test_flat_scene_falls_back_to_the_plane(tmp_path: Path) -> None:
    scene = make_scene("flat", FLAT_SEED)
    result = solve_capture(load_capture(write_session(scene, tmp_path)))
    sol = result.mapping.solution
    assert sol.status == "planar" and "under-constrained" in sol.message
    cam, q_true, _ = eval_points(scene, scene.camera_truth())
    plane = np.linalg.norm(result.mapping.predict(cam, "plane") - q_true, axis=1)
    hyb = np.linalg.norm(result.mapping.predict(cam, "hybrid") - q_true, axis=1)
    assert np.median(hyb) <= np.median(plane) + 0.1
    assert np.percentile(hyb, 95) <= np.percentile(plane, 95) + 0.1


def test_solve_is_deterministic(box_capture: Path) -> None:
    a = solve_capture(load_capture(box_capture)).to_json()
    b = solve_capture(load_capture(box_capture)).to_json()
    a.pop("elapsed_s")
    b.pop("elapsed_s")
    assert a == b


def test_session_is_deterministic(tmp_path: Path) -> None:
    scene = make_scene("stepped", 4)
    write_session(scene, tmp_path / "a")
    write_session(scene, tmp_path / "b")
    for name in ("manifest.json", "shots/s002_depth.npy", "graycode/x05.png"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_cli_solve(box_capture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["calib3d", "solve", str(box_capture), "--json", "--report", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["simulated"] is True and out["projector"]["status"] == "ok"
    for name in ("report.json", "report.md", "overlay.png", "proj_view_3d.png"):
        assert (tmp_path / name).is_file()
    assert main(["calib3d", "solve", str(box_capture)]) == 0
    text = capsys.readouterr().out
    assert "reprojection median / p95" in text and "SIMULATED" in text
    assert main(["calib3d", "solve", str(tmp_path / "missing")]) == 1


def test_projector_warp_covers_the_lit_surface(box_capture: Path) -> None:
    result = solve_capture(load_capture(box_capture))
    warp = projector_warp(result.mapping, (1920, 1080))
    assert warp.shape == (1080, 1920, 2)
    assert np.isfinite(warp[..., 0]).mean() > 0.8


# ---------------------------------------------------------------- evaluation harness


def test_evaluation_harness_small() -> None:
    rows = run_scene("board", 1, sensitivity=False)
    assert len(rows) == 1
    r = rows[0]
    assert set(r["covered"]) == {"plane", "dense", "model", "hybrid"}
    assert set(r["gaps"]) == {"plane", "dense", "model", "hybrid"}
    assert r["covered"]["hybrid"]["object"]["n"] > 100
    md, summary = report_markdown(rows)
    assert "SIMULATED" in md and summary["train_scenes"] == 1
