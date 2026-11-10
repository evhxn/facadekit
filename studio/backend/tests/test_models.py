from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from facadekit.errors import FkError
from facadekit.models.calibration import Calibration
from facadekit.models.jobs import Job, utc_now_iso
from facadekit.models.project import Project, parse_project

FIXTURE = Path(__file__).parent / "fixtures" / "project_example.json"


def _example() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _invalid(data: dict[str, Any]) -> None:
    with pytest.raises(FkError) as info:
        parse_project(data)
    assert info.value.code == "INVALID_PROJECT"


def test_fixture_round_trips() -> None:
    data = _example()
    project = parse_project(data)
    assert project.model_dump(mode="json") == data


def test_defaults_build() -> None:
    project = Project(id="a", name="A")
    assert project.projector.width == 1920
    assert project.timeline.duration_ms == 60000
    assert project.surfaces == []
    assert project.fpp.host is None


def test_unknown_field_rejected() -> None:
    data = _example()
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        Project.model_validate(data)


def test_numeric_ranges_enforced() -> None:
    data = _example()
    data["projector"]["fps"] = 29
    with pytest.raises(ValidationError):
        Project.model_validate(data)


def test_rule1_duplicate_clip_id() -> None:
    data = _example()
    track = data["timeline"]["tracks"][0]
    second = copy.deepcopy(track["clips"][0])
    second["start_ms"] = 60000
    second["id"] = track["clips"][0]["id"]
    track["clips"].append(second)
    data["timeline"]["duration_ms"] = 120000
    _invalid(data)


def test_rule2_unknown_surface() -> None:
    data = _example()
    data["timeline"]["tracks"][0]["clips"][0]["surface_id"] = "srf_missing"
    _invalid(data)


def test_rule3_audio_asset_on_clip() -> None:
    data = _example()
    data["assets"].append(
        {
            "id": "ast_55555555",
            "kind": "audio",
            "filename": "media/ast_55555555.mp3",
            "original_name": "song.mp3",
            "width": None,
            "height": None,
            "duration_ms": 60000,
            "fps": None,
        }
    )
    data["timeline"]["tracks"][0]["clips"][0]["asset_id"] = "ast_55555555"
    _invalid(data)


def test_rule3_audio_asset_id_must_have_audio() -> None:
    data = _example()
    data["assets"][0]["has_audio"] = False
    data["timeline"]["audio_asset_id"] = "ast_22222222"
    _invalid(data)


def test_rule4_asset_and_generator_both_set() -> None:
    data = _example()
    data["timeline"]["tracks"][0]["clips"][0]["generator"] = {"type": "solid", "params": {}}
    _invalid(data)


def test_rule5_overlapping_clips() -> None:
    data = _example()
    track = data["timeline"]["tracks"][0]
    second = copy.deepcopy(track["clips"][0])
    second["id"] = "clp_66666666"
    second["start_ms"] = 30000
    track["clips"].append(second)
    _invalid(data)


def test_rule6_canvas_surface_id_reserved() -> None:
    data = _example()
    data["surfaces"][0]["id"] = "canvas"
    data["timeline"]["tracks"][0]["clips"][0]["surface_id"] = "canvas"
    _invalid(data)


def test_rule6_mesh_offset_count() -> None:
    data = _example()
    data["surfaces"][0]["mesh"] = {
        "cols": 2,
        "rows": 2,
        "offsets": [[0.0, 0.0]] * 8,
    }
    _invalid(data)


def test_calibration_and_job_models() -> None:
    cal = Calibration(
        id="cal_0a1b2c3d",
        created_at=utc_now_iso(),
        mode="manual",
        projector_width=1920,
        projector_height=1080,
        camera_id=None,
        camera_width=None,
        camera_height=None,
        settings=None,
        quality=None,
        h_c2p=None,
    )
    assert cal.warnings == []
    job = Job(id="job_00000001", kind="render", state="queued", created_at=utc_now_iso())
    assert job.progress == 0.0
    assert job.error is None
    stamp = utc_now_iso()
    assert len(stamp) == 20 and stamp.endswith("Z")


def test_projector_minimum_allows_320x180() -> None:
    from facadekit.models.project import ProjectorSpec

    spec = ProjectorSpec(width=320, height=180)
    assert (spec.width, spec.height) == (320, 180)
    with pytest.raises(ValidationError):
        ProjectorSpec(width=159, height=180)
    with pytest.raises(ValidationError):
        ProjectorSpec(width=320, height=89)
