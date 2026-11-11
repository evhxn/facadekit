from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.api.app import create_app
from facadekit.config import camera_size
from facadekit.sim.scene import SimScene, scene_far_too_coarse

API = "/api/v1"


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while True:
        job: dict[str, Any] = client.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline
        time.sleep(0.05)


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[[SimScene | None], TestClient]]:
    clients: list[TestClient] = []

    def make(scene: SimScene | None = None) -> TestClient:
        c = TestClient(
            create_app(data_dir=tmp_path / f"d{len(clients)}", sim=True, sim_scene=scene)
        )
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def _check(client: TestClient, roi: dict[str, Any] | None = None) -> dict[str, Any]:
    pid = client.post(f"{API}/projects", json={"name": "demo"}).json()["id"]
    resp = client.post(f"{API}/projects/{pid}/camera-check", json={"camera_id": "sim", "roi": roi})
    assert resp.status_code == 202, resp.text
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    check: dict[str, Any] = job["result"]["check"]
    return check


def _inside(inner: dict[str, Any], outer: dict[str, Any]) -> bool:
    return (
        inner["x"] >= outer["x"]
        and inner["y"] >= outer["y"]
        and inner["x"] + inner["w"] <= outer["x"] + outer["w"]
        and inner["y"] + inner["h"] <= outer["y"] + outer["h"]
    )


def test_default_scene_is_good(make_client: Callable[[SimScene | None], TestClient]) -> None:
    check = _check(make_client(None))
    assert check["verdict"] == "good"
    assert (check["camera_width"], check["camera_height"]) == (1280, 720)
    box = check["lit_box"]
    assert box is not None and box["x"] >= 0 and box["y"] >= 0
    assert box["x"] + box["w"] <= 1280 and box["y"] + box["h"] <= 720
    sug = check["suggested_roi"]
    assert sug is None or sug["w"] * sug["h"] <= 0.6 * 1280 * 720
    assert set(check["stripe_modulation"]) == {"2", "3", "4"}
    assert check["proj_px_per_cam_px"] == round(1 / check["px_per_proj_px"], 1)
    assert check["suggest_1080p"] is False


def test_check_does_not_create_calibration(
    make_client: Callable[[SimScene | None], TestClient],
) -> None:
    client = make_client(None)
    _check(client)
    pid = client.get(f"{API}/projects").json()[0]["id"]
    assert client.get(f"{API}/projects/{pid}").json()["calibration_id"] is None


def test_too_coarse_scene_and_roi_round_trip(
    make_client: Callable[[SimScene | None], TestClient],
) -> None:
    client = make_client(scene_far_too_coarse())
    check = _check(client)
    assert check["verdict"] == "too_coarse"
    assert check["reasons"][0] == "SMALL_IN_FRAME"
    assert check["suggest_1080p"] is True  # 1280x720 sim frame, verdict not good
    lit, sug = check["lit_box"], check["suggested_roi"]
    assert lit is not None and sug is not None and _inside(lit, sug)
    again = _check(client, roi=sug)
    lit2 = again["lit_box"]
    assert lit2 is not None
    for key in ("x", "y", "w", "h"):
        assert abs(lit2[key] - lit[key]) <= 2, (key, lit, lit2)
    assert (again["camera_width"], again["camera_height"]) == (1280, 720)


def test_camera_busy_is_409(make_client: Callable[[SimScene | None], TestClient]) -> None:
    client = make_client(None)
    pid = client.post(f"{API}/projects", json={"name": "demo"}).json()["id"]
    cameras = client.app.state.fk.cameras  # type: ignore[attr-defined]
    cameras.acquire("sim")
    try:
        resp = client.post(f"{API}/projects/{pid}/camera-check", json={"camera_id": "sim"})
        assert resp.status_code == 409
    finally:
        cameras.release("sim")
    resp = client.post(f"{API}/projects/{pid}/camera-check", json={"camera_id": "sim"})
    assert resp.status_code == 202
    assert _wait(client, resp.json()["job_id"])["state"] == "succeeded"


def test_unknown_camera_is_404(make_client: Callable[[SimScene | None], TestClient]) -> None:
    client = make_client(None)
    pid = client.post(f"{API}/projects", json={"name": "demo"}).json()["id"]
    resp = client.post(f"{API}/projects/{pid}/camera-check", json={"camera_id": "nope"})
    assert resp.status_code == 404


def test_snapshot_crop(make_client: Callable[[SimScene | None], TestClient]) -> None:
    client = make_client(None)
    resp = client.get(f"{API}/devices/cameras/sim/snapshot.jpg?crop=10,20,100,50")
    assert resp.status_code == 200
    img = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (50, 100)
    clamped = client.get(f"{API}/devices/cameras/sim/snapshot.jpg?crop=1200,700,500,500")
    img = cv2.imdecode(np.frombuffer(clamped.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (20, 80)
    for bad in ("abc", "1,2,3", "-1,0,10,10", "1,2,3,4,5", "5000,0,10,10"):
        resp = client.get(f"{API}/devices/cameras/sim/snapshot.jpg?crop={bad}")
        # BAD_REQUEST maps to HTTP 422 everywhere in this API (errors.HTTP_STATUS).
        assert resp.status_code == 422, bad
        assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_camera_size_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACADEKIT_CAMERA_SIZE", raising=False)
    assert camera_size() == (1280, 720)  # owner override: 1080p is opt-in
    monkeypatch.setenv("FACADEKIT_CAMERA_SIZE", "1920x1080")
    assert camera_size() == (1920, 1080)
    monkeypatch.setenv("FACADEKIT_CAMERA_SIZE", "1280x720")
    assert camera_size() == (1280, 720)
    for bad in ("big", "0x0", "1920", "x1080"):
        monkeypatch.setenv("FACADEKIT_CAMERA_SIZE", bad)
        assert camera_size() == (1280, 720)
