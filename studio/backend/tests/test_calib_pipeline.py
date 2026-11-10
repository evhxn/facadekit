from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from facadekit.calib.pipeline import create_manual_calibration, load_calibration, run_calibration
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings
from facadekit.models.project import ProjectorSpec
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene

FAST = CalibSettings(discard_frames=0, average_frames=1)


def _nosleep(_s: float) -> None:
    return None


class RecordingCamera(SimCamera):
    closed = 0

    def close(self) -> None:
        self.closed += 1
        super().close()


def _run(
    tmp: Path, scene: SimScene, progress: list[float] | None = None
) -> tuple[RecordingCamera, object]:
    display = SimDisplay(scene)
    cam = RecordingCamera(scene, display)
    cb = (lambda f, _m: progress.append(f)) if progress is not None else None
    cal = run_calibration(tmp, display, cam, "sim", FAST, progress=cb, sleep=_nosleep)
    return cam, cal


def test_successful_scan(tmp_path: Path) -> None:
    progress: list[float] = []
    cam, cal = _run(tmp_path, default_scene(), progress)
    assert cal.mode == "scan"  # type: ignore[attr-defined]
    assert cal.quality.coverage > 0.85  # type: ignore[attr-defined]
    assert cal.warnings == []  # type: ignore[attr-defined]
    d = tmp_path / "calibration" / cal.id  # type: ignore[attr-defined]
    for name in ("calibration.json", "proj_view.png", "p2c.npy", "measured_mask.png"):
        assert (d / name).is_file()
    assert np.load(d / "p2c.npy").shape == (1080, 1920, 2)
    img = cv2.imread(str(d / "proj_view.png"))
    assert img.shape == (1080, 1920, 3)
    assert load_calibration(tmp_path, cal.id) == cal  # type: ignore[attr-defined]
    assert progress == sorted(progress)
    assert progress[-1] == 1.0
    assert cam.closed == 1
    assert [p.name for p in (tmp_path / "calibration").iterdir()] == [cal.id]  # type: ignore[attr-defined]


def test_huge_occluder_fails(tmp_path: Path) -> None:
    scene = default_scene()
    scene.occluders_world = [(0, 0, 1600, 560)]
    with pytest.raises(FkError) as e:
        _run(tmp_path, scene)
    assert e.value.code == "SCAN_FAILED"
    cal_dir = tmp_path / "calibration"
    assert not cal_dir.exists() or list(cal_dir.iterdir()) == []


def test_medium_occluder_warns(tmp_path: Path) -> None:
    scene = default_scene()
    scene.occluders_world = [(0, 0, 1600, 280)]
    _, cal = _run(tmp_path, scene)
    assert "LOW_COVERAGE" in cal.warnings  # type: ignore[attr-defined]


def test_cancel(tmp_path: Path) -> None:
    scene = default_scene()
    display = SimDisplay(scene)
    cam = RecordingCamera(scene, display)
    with pytest.raises(FkError) as e:
        run_calibration(tmp_path, display, cam, "sim", FAST, cancel=lambda: True, sleep=_nosleep)
    assert e.value.code == "CANCELLED"
    assert cam.closed == 1


def test_manual(tmp_path: Path) -> None:
    cal = create_manual_calibration(tmp_path, ProjectorSpec(width=640, height=360))
    assert cal.mode == "manual"
    d = tmp_path / "calibration" / cal.id
    assert sorted(p.name for p in d.iterdir()) == ["calibration.json", "proj_view.png"]
    img = cv2.imread(str(d / "proj_view.png"))
    assert img.shape == (360, 640, 3)
    assert tuple(img[60, 60]) == (40, 40, 40)
    assert load_calibration(tmp_path, cal.id) == cal


def test_load_rejects_bad_ids(tmp_path: Path) -> None:
    for bad in ("../x", "cal_zzzzzzzz", "cal_00000000"):
        with pytest.raises(FkError) as e:
            load_calibration(tmp_path, bad)
        assert e.value.code == "NOT_FOUND"
