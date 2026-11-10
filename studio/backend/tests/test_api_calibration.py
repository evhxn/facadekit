from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.api.app import create_app

API = "/api/v1"
FAST = {"discard_frames": 0, "average_frames": 1}


@pytest.fixture
def sim_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as c:
        yield c


def _project(client: TestClient, projector: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "demo"}
    if projector is not None:
        body["projector"] = projector
    resp = client.post(f"{API}/projects", json=body)
    assert resp.status_code == 201, resp.text
    result: dict[str, Any] = resp.json()
    return result


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while True:
        job: dict[str, Any] = client.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline
        time.sleep(0.05)


def test_cameras_and_snapshot(sim_client: TestClient) -> None:
    cams = sim_client.get(f"{API}/devices/cameras").json()
    assert [c["id"] for c in cams] == ["sim"]
    assert (cams[0]["width"], cams[0]["height"]) == (1280, 720)
    resp = sim_client.get(f"{API}/devices/cameras/sim/snapshot.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"
    img = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (720, 1280)
    assert sim_client.get(f"{API}/devices/cameras/nope/snapshot.jpg").status_code == 404


def test_controls(sim_client: TestClient) -> None:
    resp = sim_client.post(f"{API}/devices/cameras/sim/controls", json={"auto_exposure": False})
    assert resp.status_code == 200
    assert resp.json() == {"applied": {"auto_exposure": True}}


def test_scan(sim_client: TestClient) -> None:
    project = _project(sim_client)
    resp = sim_client.post(
        f"{API}/projects/{project['id']}/calibrations",
        json={"mode": "scan", "camera_id": "sim", "settings": FAST},
    )
    assert resp.status_code == 202
    job = _wait(sim_client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    cid = job["result"]["calibration_id"]
    after = sim_client.get(f"{API}/projects/{project['id']}").json()
    assert after["calibration_id"] == cid
    assert after["revision"] > project["revision"]
    cal = sim_client.get(f"{API}/projects/{project['id']}/calibrations/{cid}").json()
    assert cal["mode"] == "scan"
    assert cal["quality"]["coverage"] > 0.85
    base = f"{API}/projects/{project['id']}/calibrations/{cid}"
    for name in ("proj_view.png", "measured_mask.png"):
        r = sim_client.get(f"{base}/{name}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG")
    # camera released after the job
    assert sim_client.get(f"{API}/devices/cameras/sim/snapshot.jpg").status_code == 200


def test_manual(sim_client: TestClient) -> None:
    project = _project(sim_client)
    resp = sim_client.post(f"{API}/projects/{project['id']}/calibrations", json={"mode": "manual"})
    assert resp.status_code == 202
    job = _wait(sim_client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    cid = job["result"]["calibration_id"]
    base = f"{API}/projects/{project['id']}/calibrations/{cid}"
    assert sim_client.get(f"{base}/proj_view.png").status_code == 200
    assert sim_client.get(f"{base}/measured_mask.png").status_code == 404


def test_scan_requires_camera(sim_client: TestClient) -> None:
    project = _project(sim_client)
    resp = sim_client.post(f"{API}/projects/{project['id']}/calibrations", json={"mode": "scan"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_resolution_mismatch(sim_client: TestClient) -> None:
    project = _project(sim_client, {"width": 1280, "height": 720, "fps": 30})
    resp = sim_client.post(
        f"{API}/projects/{project['id']}/calibrations",
        json={"mode": "scan", "camera_id": "sim"},
    )
    assert resp.json()["error"]["code"] == "RESOLUTION_MISMATCH"


def test_projector_not_connected(client: TestClient) -> None:
    project = _project(client)
    resp = client.post(
        f"{API}/projects/{project['id']}/calibrations",
        json={"mode": "scan", "camera_id": "0"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PROJECTOR_NOT_CONNECTED"


def test_snapshot_busy_while_acquired(sim_client: TestClient) -> None:
    cameras = sim_client.app.state.fk.cameras  # type: ignore[attr-defined]
    cameras.acquire("sim")
    try:
        resp = sim_client.get(f"{API}/devices/cameras/sim/snapshot.jpg")
        assert resp.status_code == 409
        assert resp.json()["error"] == {"code": "BAD_REQUEST", "message": "Camera is busy scanning"}
    finally:
        cameras.release("sim")
    assert sim_client.get(f"{API}/devices/cameras/sim/snapshot.jpg").status_code == 200
