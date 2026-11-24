from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.cli import main
from facadekit.render.compositor import Compositor
from facadekit.samples import SAMPLE_DURATION_MS, create_sample
from facadekit.sim.scene import default_scene
from facadekit.store.project_store import load_project_dir

API = "/api/v1"


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.mark.parametrize("theme", ["halloween", "winter"])
def test_sample_project_is_complete_and_procedural(tmp_path: Path, theme: str) -> None:
    folder = tmp_path / f"{theme}.fkproj"
    project = create_sample(folder, theme, theme)  # type: ignore[arg-type]
    assert "simulated house" in project.name
    assert project.calibration_id is not None
    assert (folder / "calibration" / project.calibration_id / "proj_view.png").is_file()
    assert [s.name for s in project.surfaces] == ["Garage door", "Left window", "Right window"]

    # The door was found on the scan, close to where the simulated house has it.
    truth = np.array(default_scene().feature_quad_proj("garage_door"))
    found = np.array(project.surfaces[0].quad)
    assert float(np.max(np.linalg.norm(found - truth, axis=1))) < 1.0

    clips = [c for t in project.timeline.tracks for c in t.clips]
    assert project.timeline.duration_ms == SAMPLE_DURATION_MS
    assert all(c.start_ms + c.duration_ms <= SAMPLE_DURATION_MS for c in clips)
    # Only built-in effects and pictures drawn by FacadeKit: no video, no audio.
    assert {a.kind for a in project.assets} == {"image"}
    assert all((folder / a.filename).is_file() for a in project.assets)
    assert all((folder / "media" / f"{a.id}.thumb.jpg").is_file() for a in project.assets)
    assert project.timeline.audio_asset_id is None
    effects = {c.generator.type for c in clips if c.generator is not None}
    assert len(effects) >= 4

    reloaded = load_project_dir(folder)
    assert reloaded == project
    with Compositor(reloaded, folder, scale=0.25) as comp:
        frame = comp.frame(6000)
    assert frame.mean() > 5  # something is lit


def test_cli_new_sample(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proj = tmp_path / "try.fkproj"
    assert main(["new", str(proj), "--sample"]) == 0
    out = capsys.readouterr().out
    assert "simulated house" in out
    project = load_project_dir(proj)
    assert project.id == "try" and len(project.surfaces) == 3
    assert main(["render", str(proj), "--preset", "preview-540p", "--no-audio"]) == 0


def test_cli_new_still_needs_a_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", str(tmp_path / "x.fkproj")]) != 0
    assert "--name" in capsys.readouterr().err


def test_api_sample(client: TestClient) -> None:
    res = client.post(f"{API}/projects/samples", json={"theme": "winter"})
    assert res.status_code == 202, res.text
    body = res.json()
    job = _wait(client, body["job_id"])
    assert job["state"] == "succeeded", job
    assert job["result"] == {"project_id": body["project_id"]}
    project = client.get(f"{API}/projects/{body['project_id']}").json()
    assert project["name"] == "Sample: Winter (simulated house)"
    assert len(project["surfaces"]) == 3 and project["calibration_id"]
    frame = client.get(f"{API}/projects/{body['project_id']}/frame?t_ms=5000&scale=0.25")
    assert frame.status_code == 200


def test_api_sample_rejects_unknown_theme(client: TestClient) -> None:
    res = client.post(f"{API}/projects/samples", json={"theme": "easter"})
    assert res.status_code == 400 or res.status_code == 422
    assert client.get(f"{API}/projects").json() == []
