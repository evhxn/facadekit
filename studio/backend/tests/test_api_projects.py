from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from facadekit.cli import main

API = "/api/v1"


def test_health(client: TestClient) -> None:
    body = client.get(f"{API}/health").json()
    assert body["status"] == "ok"
    assert body["sim"] is False
    assert isinstance(body["version"], str)


def test_project_crud(client: TestClient) -> None:
    resp = client.post(f"{API}/projects", json={"name": "My Show"})
    assert resp.status_code == 201
    project = resp.json()
    assert project["id"] == "my-show"
    assert project["revision"] == 0

    assert [p["id"] for p in client.get(f"{API}/projects").json()] == ["my-show"]
    assert client.get(f"{API}/projects/my-show").json() == project

    project["name"] = "Renamed"
    saved = client.put(f"{API}/projects/my-show", json=project)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["name"] == "Renamed"

    stale = client.put(f"{API}/projects/my-show", json=project)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

    assert client.delete(f"{API}/projects/my-show").status_code == 204
    gone = client.get(f"{API}/projects/my-show")
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "NOT_FOUND"
    assert isinstance(gone.json()["error"]["message"], str)


def test_put_mismatched_id(client: TestClient) -> None:
    project = client.post(f"{API}/projects", json={"name": "A"}).json()
    resp = client.put(f"{API}/projects/other", json=project)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_put_invalid_project(client: TestClient) -> None:
    project: dict[str, Any] = client.post(f"{API}/projects", json={"name": "A"}).json()
    project["timeline"]["clips"] = [
        {
            "id": "clip_1",
            "asset_id": "asset_x",
            "surface_id": "srf_missing",
            "start": 0,
            "duration": 1,
        }
    ]
    resp = client.put(f"{API}/projects/{project['id']}", json=project)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_PROJECT"


def test_create_validation_error(client: TestClient) -> None:
    resp = client.post(f"{API}/projects", json={"projector": {}})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_unknown_api_path_is_json_404(client: TestClient) -> None:
    resp = client.get(f"{API}/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_spa_path(client: TestClient) -> None:
    assert client.get("/some/spa/path").status_code == 200


def test_missing_job(client: TestClient) -> None:
    resp = client.get(f"{API}/jobs/job_missing")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_job_runs_to_success(client: TestClient) -> None:
    job = client.app.state.fk.jobs.submit("analyze", lambda progress, cancelled: {"ok": True})
    deadline = time.monotonic() + 5
    state = ""
    while time.monotonic() < deadline:
        state = client.get(f"{API}/jobs/{job.id}").json()["state"]
        if state == "succeeded":
            break
        time.sleep(0.02)
    assert state == "succeeded"


def test_cli_new_twice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "demo.fkproj"
    assert main(["new", str(target), "--name", "Demo"]) == 0
    assert (target / "project.json").is_file()
    assert (target / "media").is_dir()
    assert main(["new", str(target), "--name", "Demo"]) == 1
    assert "BAD_REQUEST" in capsys.readouterr().err
    assert main(["new", str(tmp_path / "x"), "--name", "Demo"]) == 1
