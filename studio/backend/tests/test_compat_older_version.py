"""Files written by a newer version load in an older one, and new files stay readable by the
previous release (unknown fields ignored on load, new optional fields omitted when None)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from facadekit.calib.learn import ScanMemory, ScanRecord
from facadekit.calib.pipeline import load_calibration
from facadekit.models.calibration import CalibQuality, Calibration, CalibSettings, CameraRoi
from facadekit.models.project import parse_project
from facadekit.store.project_store import PROJECT_FILE, PROJECT_SUFFIX, ProjectStore

FIXTURE = Path(__file__).parent / "fixtures" / "project_example.json"


def _cal(**kw: Any) -> Calibration:
    base: dict[str, Any] = {
        "id": "cal_0000000a",
        "created_at": "2026-01-01T00:00:00Z",
        "mode": "scan",
        "projector_width": 320,
        "projector_height": 180,
        "camera_id": "sim",
        "camera_width": 640,
        "camera_height": 480,
        "settings": CalibSettings(),
        "quality": CalibQuality(coverage=0.9, planarity=0.1, median_contrast=50, lit_fraction=0.5),
        "h_c2p": None,
    }
    return Calibration(**{**base, **kw})


def _record(**kw: Any) -> ScanRecord:
    base: dict[str, Any] = {
        "id": "s1",
        "at": "2026-01-01T00:00:00Z",
        "camera_id": "sim",
        "bucket": "medium",
        "median_contrast": 40.0,
        "lit_fraction": 0.5,
        "strategy": "standard",
        "ok": True,
    }
    return ScanRecord(**{**base, **kw})


def _write_cal(tmp_path: Path, cal: Calibration) -> dict[str, Any]:
    folder = tmp_path / "calibration" / cal.id
    folder.mkdir(parents=True)
    (folder / "calibration.json").write_text(cal.to_file_json(), encoding="utf-8")
    data: dict[str, Any] = json.loads((folder / "calibration.json").read_text(encoding="utf-8"))
    return data


def test_calibration_ignores_unknown_fields(tmp_path: Path) -> None:
    data = json.loads(_cal().to_file_json())
    data["future"] = 1
    data["settings"]["future"] = 2
    data["quality"]["future"] = 3
    folder = tmp_path / "calibration" / "cal_0000000a"
    folder.mkdir(parents=True)
    (folder / "calibration.json").write_text(json.dumps(data), encoding="utf-8")
    assert load_calibration(tmp_path, "cal_0000000a") == _cal()
    roi = {"x": 0, "y": 0, "w": 64, "h": 64, "frame_width": 64, "frame_height": 64, "z": 1}
    assert CameraRoi.model_validate(roi).w == 64


def test_calibration_omits_none_new_fields(tmp_path: Path) -> None:
    data = _write_cal(tmp_path, _cal())
    assert "roi" not in data["settings"]
    assert "strategy" not in data
    for key in ("px_per_proj_px", "snr", "verdict"):
        assert key not in data["quality"]
    assert data["camera_id"] == "sim"  # old required fields stay, even when null
    assert "h_c2p" in data
    assert load_calibration(tmp_path, "cal_0000000a") == _cal()


def test_calibration_round_trips_populated_values(tmp_path: Path) -> None:
    roi = CameraRoi(x=0, y=0, w=128, h=128, frame_width=640, frame_height=480, bin=2)
    cal = _cal(
        settings=CalibSettings(roi=roi),
        quality=CalibQuality(
            coverage=0.9,
            planarity=0.1,
            median_contrast=50,
            lit_fraction=0.5,
            px_per_proj_px=1.5,
            snr=12.0,
            verdict="good",
        ),
        strategy="standard",
    )
    data = _write_cal(tmp_path, cal)
    assert data["settings"]["roi"]["bin"] == 2
    assert data["strategy"] == "standard"
    assert load_calibration(tmp_path, "cal_0000000a") == cal


def test_scan_record_unknown_fields_and_none_scale(tmp_path: Path) -> None:
    memory = ScanMemory(tmp_path / "scans.jsonl")
    memory.append(_record())
    memory.append(_record(id="s2", scale="fine"))
    lines = [json.loads(x) for x in (tmp_path / "scans.jsonl").read_text().splitlines()]
    assert "scale" not in lines[0]
    assert lines[1]["scale"] == "fine"
    lines[0]["future"] = True
    (tmp_path / "scans.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    assert memory.records() == [_record(), _record(id="s2", scale="fine")]


def test_project_file_with_unknown_fields_loads(tmp_path: Path) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = parse_project(data)
    data["future_top"] = 1
    data["projector"]["future"] = 2
    data["surfaces"][0]["future"] = 3
    data["timeline"]["tracks"][0]["clips"][0]["future"] = 4
    folder = tmp_path / f"{expected.id}{PROJECT_SUFFIX}"
    folder.mkdir()
    (folder / PROJECT_FILE).write_text(json.dumps(data), encoding="utf-8")
    assert ProjectStore(tmp_path).load(expected.id) == expected
