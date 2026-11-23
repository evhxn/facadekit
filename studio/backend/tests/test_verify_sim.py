"""Verify surface end to end in the simulator: a real scan, then the real check (docs/13)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest

from facadekit.calib.pipeline import create_manual_calibration, run_calibration
from facadekit.calib.selftest import format_report, run_self_test
from facadekit.calib.verify import (
    Injection,
    VerifyOutcome,
    load_record,
    overlay_path,
    run_verify,
    save_record,
)
from facadekit.errors import FkError
from facadekit.geom.homography import estimate_aspect
from facadekit.models.calibration import CalibSettings
from facadekit.models.project import Mask, Project, Surface
from facadekit.models.verification import VerificationRecord
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.e2e import new_project_dir
from facadekit.sim.lag import LaggyCamera, LaggyDisplay, VirtualClock
from facadekit.sim.scene import SimScene, default_scene
from facadekit.store.project_store import load_project_dir, save_project_dir

DOOR = "garage_door"


@dataclass
class Rig:
    project_dir: Path
    project: Project
    scene: SimScene
    door: np.ndarray


def _surface(quad: np.ndarray, sid: str = "srf_door", mask: Mask | None = None) -> Surface:
    pts = [(float(x), float(y)) for x, y in quad]
    return Surface(
        id=sid,
        name="Door",
        quad=(pts[0], pts[1], pts[2], pts[3]),
        mask=mask,
        aspect=estimate_aspect(pts),
    )


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Rig]:
    root = tmp_path_factory.mktemp("verify")
    pd = new_project_dir(root / "e2e.fkproj")
    scene = default_scene()
    display = SimDisplay(scene)
    cal = run_calibration(
        pd,
        display,
        SimCamera(scene, display),
        "sim",
        CalibSettings(discard_frames=0, average_frames=1),
        sleep=lambda s: None,
    )
    door = np.asarray(scene.feature_quad_proj(DOOR), dtype=np.float64)
    project = load_project_dir(pd).model_copy(
        update={"calibration_id": cal.id, "surfaces": [_surface(door)]}
    )
    save_project_dir(pd, project)
    yield Rig(pd, load_project_dir(pd), scene, door)
    shutil.rmtree(root, ignore_errors=True)


def _run(
    rig: Rig,
    scene: SimScene | None = None,
    *,
    surface: str | None = "srf_door",
    project: Project | None = None,
    **kw: object,
) -> VerifyOutcome:
    sc = scene or replace(rig.scene, seed=11)
    display = SimDisplay(sc)
    return run_verify(
        rig.project_dir,
        project or rig.project,
        display,
        SimCamera(sc, display),
        surface_id=surface,
        sleep=lambda s: None,
        **kw,  # type: ignore[arg-type]
    )


def test_scan_keeps_the_camera_pictures(rig: Rig) -> None:
    folder = rig.project_dir / "calibration" / str(rig.project.calibration_id)
    assert (folder / "cam_white.png").is_file() and (folder / "cam_black.png").is_file()


def test_stable_rig_and_exact_outline_pass(rig: Rig) -> None:
    out = _run(rig)
    r = out.record
    assert r.rig.verdict == "stable"
    assert r.rig.dots_found == r.rig.dots_expected
    assert r.rig.projector_shift_px is not None and r.rig.projector_shift_px < 0.75
    assert r.surface is not None and r.surface.verdict == "pass"
    for e in r.surface.edges:
        assert e.spill_px is not None and abs(e.spill_px) < 1.5
    assert r.surface.leakage is not None and r.surface.leakage < 0.03
    assert r.surface.suggested_quad is None
    assert out.overlay is not None and out.overlay.ndim == 3
    assert r.level < 1.0  # no full-brightness flash when a dimmer picture is enough
    assert r.shots <= 6  # black, white (once more if it had to be brighter), dots, fill, inner


def test_projector_is_left_black(rig: Rig) -> None:
    sc = replace(rig.scene, seed=12)
    display = SimDisplay(sc)
    run_verify(
        rig.project_dir,
        rig.project,
        display,
        SimCamera(sc, display),
        surface_id="srf_door",
        sleep=lambda s: None,
    )
    assert display.current is not None and int(display.current.max()) == 0


@pytest.mark.parametrize("grow", [8.0, -6.0])
def test_outline_too_big_or_too_small_is_measured(rig: Rig, grow: float) -> None:
    r = _run(rig, inject=Injection(grow_px=grow)).record
    assert r.surface is not None and r.surface.verdict == "fail"
    spills = [e.spill_px for e in r.surface.edges]
    assert len(spills) == 4 and all(s is not None for s in spills)
    for s in spills:
        assert s == pytest.approx(grow, abs=1.5)
    if grow > 0:
        assert "pull it in" in r.surface.edges[0].advice
        assert r.surface.leakage is not None and r.surface.leakage > 0.015
    else:
        assert "push it out" in r.surface.edges[0].advice
        assert r.surface.uncovered is not None and r.surface.uncovered > 0.015


def test_suggested_fix_lands_on_the_door(rig: Rig) -> None:
    from facadekit.calib.verify import grow_polygon

    drawn = grow_polygon(rig.door, 7.0) + np.array([3.0, -2.0])
    project = rig.project.model_copy(update={"surfaces": [_surface(drawn)]})
    r = _run(rig, project=project).record
    assert r.surface is not None and r.surface.suggested_quad is not None
    fixed = np.array(r.surface.suggested_quad)
    assert float(np.max(np.linalg.norm(fixed - rig.door, axis=1))) < 1.5


def test_projector_move_is_told_apart_from_a_camera_move(rig: Rig) -> None:
    moved_p = replace(
        rig.scene,
        seed=13,
        proj_corners_world=tuple((x + 3.0, y) for x, y in rig.scene.proj_corners_world),
    )  # type: ignore[arg-type]
    r = _run(rig, moved_p).record
    assert r.rig.verdict == "projector_moved"
    assert r.rig.projector_shift_px == pytest.approx(3.0 * 1920 / 1370, abs=0.6)
    assert r.rig.camera == "stable" and r.rig.drift is not None
    moved_c = replace(
        rig.scene,
        seed=14,
        world_corners_cam=tuple((x + 4.0, y - 2.0) for x, y in rig.scene.world_corners_cam),
    )  # type: ignore[arg-type]
    r2 = _run(rig, moved_c).record
    assert r2.rig.verdict == "camera_moved"
    assert r2.rig.camera_shift_px == pytest.approx(np.hypot(4, 2), abs=0.4)
    assert r2.rig.projector == "stable"
    # a camera move does not touch the show: the outline still passes
    assert r2.surface is not None and r2.surface.verdict == "pass"


def test_software_projector_shift_is_measured(rig: Rig) -> None:
    r = _run(rig, surface=None, inject=Injection(proj_shift=(6.0, 0.0))).record
    assert r.rig.verdict == "projector_moved"
    assert r.rig.projector_shift_px == pytest.approx(6.0, abs=0.5)
    assert r.surface is None and r.injected is not None


def test_unlockable_camera_still_works(rig: Rig) -> None:
    clock = VirtualClock()
    sc = replace(rig.scene, seed=15)
    display = LaggyDisplay(sc, clock)
    camera = LaggyCamera(sc, display, clock, lockable=False)
    r = run_verify(
        rig.project_dir,
        rig.project,
        display,
        camera,
        surface_id="srf_door",
        sleep=clock.sleep,
        clock=clock,
    ).record
    assert r.exposure_locked is False
    assert r.rig.verdict == "stable"
    assert r.surface is not None and r.surface.verdict == "pass"
    assert r.duration_s < 6.0  # virtual seconds with the scan's own waits


def test_low_power_mode(rig: Rig) -> None:
    r = _run(rig, low_power=True).record
    assert r.rig.verdict == "stable"
    assert r.surface is not None and r.surface.verdict == "pass"


def test_mask_surface_gets_a_suggested_mask(rig: Rig) -> None:
    from facadekit.calib.verify import grow_polygon

    big = grow_polygon(rig.door, 9.0)
    outer = [(float(x), float(y)) for x, y in big]
    project = rig.project.model_copy(update={"surfaces": [_surface(big, mask=Mask(outer=outer))]})
    r = _run(rig, project=project).record
    assert r.surface is not None and r.surface.verdict == "fail"
    assert r.surface.suggested_mask is not None and r.surface.suggested_quad is not None
    fixed = np.array(r.surface.suggested_quad)
    assert float(np.max(np.linalg.norm(fixed - rig.door, axis=1))) < 3.0


def test_cancel(rig: Rig) -> None:
    with pytest.raises(FkError) as exc:
        _run(rig, cancel=lambda: True)
    assert exc.value.code == "CANCELLED"


def test_wrong_projector_size_is_refused(rig: Rig) -> None:
    sc = replace(rig.scene, proj_size=(1280, 720))
    with pytest.raises(FkError) as exc:
        _run(rig, sc)
    assert exc.value.code == "RESOLUTION_MISMATCH"


def test_no_projection_is_reported(rig: Rig) -> None:
    dark = replace(rig.scene, gain=0.0, seed=16)
    with pytest.raises(FkError) as exc:
        _run(rig, dark)
    assert exc.value.code == "CAMERA_NO_PROJECTION"


def test_manual_calibration_is_refused(tmp_path: Path) -> None:
    pd = new_project_dir(tmp_path / "m.fkproj")
    project = load_project_dir(pd)
    cal = create_manual_calibration(pd, project.projector)
    project = project.model_copy(update={"calibration_id": cal.id})
    sc = default_scene()
    display = SimDisplay(sc)
    with pytest.raises(FkError) as exc:
        run_verify(pd, project, display, SimCamera(sc, display), sleep=lambda s: None)
    assert exc.value.code == "BAD_REQUEST"


def test_older_scan_without_camera_pictures(rig: Rig, tmp_path: Path) -> None:
    copy = tmp_path / "old.fkproj"
    shutil.copytree(rig.project_dir, copy)
    folder = copy / "calibration" / str(rig.project.calibration_id)
    (folder / "cam_white.png").unlink()
    (folder / "cam_black.png").unlink()
    sc = replace(rig.scene, seed=17)
    display = SimDisplay(sc)
    r = run_verify(
        copy,
        rig.project,
        display,
        SimCamera(sc, display),
        surface_id="srf_door",
        sleep=lambda s: None,
    ).record
    assert r.rig.verdict == "stable"
    assert r.surface is not None and r.surface.verdict == "pass"


def test_record_round_trip(rig: Rig, tmp_path: Path) -> None:
    out = _run(rig)
    save_record(tmp_path, out)
    back = load_record(tmp_path, "srf_door")
    assert back == out.record
    assert overlay_path(tmp_path, "srf_door").is_file()
    with pytest.raises(FkError):
        load_record(tmp_path, "srf_other")
    # unknown fields in a newer record are ignored
    data = back.model_dump(mode="json")
    data["future"] = 1
    assert VerificationRecord.model_validate(data).id == back.id


def test_self_test_passes_in_the_simulator(rig: Rig) -> None:
    def runner(inject: Injection | None, sid: str | None) -> VerificationRecord:
        return _run(rig, surface=sid, inject=inject).record

    report = run_self_test(runner, "srf_door")
    text = format_report(report)
    assert report.passed, text
    assert [r.status for r in report.rows].count("PASS") >= 5
    assert "SELF-TEST PASSED" in text
