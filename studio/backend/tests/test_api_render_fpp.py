from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi.testclient import TestClient

from tests.conftest import FakeFpp

API = "/api/v1"


def _closed_host() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{sock.getsockname()[1]}"


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _make_project(client: TestClient, tmp_path: Path) -> str:
    resp = client.post(
        f"{API}/projects",
        json={"name": "Show", "projector": {"width": 320, "height": 180, "fps": 30}},
    )
    assert resp.status_code in (200, 201), resp.text
    pid: str = resp.json()["id"]
    img = np.zeros((40, 40, 3), np.uint8)
    img[:] = (0, 0, 255)
    png = tmp_path / "red.png"
    cv2.imwrite(str(png), img)
    with png.open("rb") as handle:
        up = client.post(f"{API}/projects/{pid}/assets", files={"file": ("red.png", handle)})
    asset_id = up.json()["asset"]["id"]
    project = client.get(f"{API}/projects/{pid}").json()
    project["surfaces"] = [
        {
            "id": "srf_1",
            "name": "Wall",
            "quad": [[80, 30], [240, 30], [240, 150], [80, 150]],
            "aspect": 1.0,
        }
    ]
    project["timeline"]["duration_ms"] = 1000
    project["timeline"]["tracks"] = [
        {
            "id": "trk_1",
            "name": "T",
            "clips": [
                {
                    "id": "clp_1",
                    "asset_id": asset_id,
                    "surface_id": "srf_1",
                    "start_ms": 0,
                    "duration_ms": 1000,
                    "fit": "stretch",
                }
            ],
        }
    ]
    assert client.put(f"{API}/projects/{pid}", json=project).status_code == 200
    return pid


def _render(client: TestClient, pid: str, kind: str = "show") -> dict[str, Any]:
    resp = client.post(
        f"{API}/projects/{pid}/renders",
        json={"preset": "preview-540p", "include_audio": True, "kind": kind},
    )
    assert resp.status_code == 202
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    result: dict[str, Any] = job["result"]
    return result


def test_frame(client: TestClient, tmp_path: Path) -> None:
    pid = _make_project(client, tmp_path)
    resp = client.get(f"{API}/projects/{pid}/frame", params={"t_ms": 0, "scale": 1})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"
    img = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (180, 320, 3)
    b, g, r = (int(v) for v in img[90, 160])
    assert r > 200 and g < 60 and b < 60
    for params in ({"t_ms": 0, "scale": 5}, {"t_ms": -1}, {"t_ms": 0, "scale": 0.01}):
        bad = client.get(f"{API}/projects/{pid}/frame", params=params)
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "BAD_REQUEST"


def test_render_flow(client: TestClient, tmp_path: Path) -> None:
    pid = _make_project(client, tmp_path)
    assert client.get(f"{API}/projects/{pid}/renders").json() == []
    result = _render(client, pid)
    assert result["render_id"].startswith("rnd_")
    listing = client.get(f"{API}/projects/{pid}/renders").json()
    assert [r["render_id"] for r in listing] == [result["render_id"]]
    assert listing[0]["size_bytes"] == result["size_bytes"] > 0
    url = f"{API}/projects/{pid}/renders/{result['render_id']}/file"
    full = client.get(url)
    assert full.status_code == 200
    assert full.headers["content-type"] == "video/mp4"
    assert full.content[4:8] == b"ftyp"
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100
    assert client.get(f"{API}/projects/{pid}/renders/rnd_00000000/file").status_code == 404
    assert client.get(f"{API}/projects/{pid}/renders/..%2Fproject/file").status_code == 404


def test_unknown_preset(client: TestClient, tmp_path: Path) -> None:
    pid = _make_project(client, tmp_path)
    resp = client.post(
        f"{API}/projects/{pid}/renders",
        json={"preset": "nope", "include_audio": True, "kind": "show"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNKNOWN_PRESET"


def test_alignment_render(client: TestClient, tmp_path: Path) -> None:
    pid = _make_project(client, tmp_path)
    result = _render(client, pid, "alignment_check")
    assert result["size_bytes"] > 0


def test_probe_and_status(client: TestClient, fake_fpp: FakeFpp) -> None:
    resp = client.post(f"{API}/fpp/probe", json={"host": fake_fpp.host})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    closed = client.post(f"{API}/fpp/probe", json={"host": _closed_host()})
    assert closed.status_code == 200
    assert closed.json()["reachable"] is False
    status = client.get(f"{API}/fpp/status", params={"host": fake_fpp.host})
    assert status.status_code == 200
    assert status.json()["status_name"] == "idle"
    dead = client.get(f"{API}/fpp/status", params={"host": _closed_host()})
    assert dead.status_code == 200
    assert dead.json() == {"status_name": None, "current_playlist": None, "seconds_played": None}


def test_publish(client: TestClient, tmp_path: Path, fake_fpp: FakeFpp) -> None:
    pid = _make_project(client, tmp_path)
    show = _render(client, pid)
    resp = client.post(
        f"{API}/projects/{pid}/publish",
        json={"host": fake_fpp.host, "render_id": show["render_id"], "start": True},
    )
    assert resp.status_code == 202
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    assert job["result"]["media_name"] == f"fk_{pid}.mp4"
    assert (fake_fpp.state_dir / "videos" / f"fk_{pid}.mp4").is_file()
    assert job["result"]["playlist_name"] == "FacadeKit - Show"
    status = client.get(f"{API}/fpp/status", params={"host": fake_fpp.host}).json()
    assert status["status_name"] == "playing"
    assert status["current_playlist"] == "FacadeKit - Show"
    project = client.get(f"{API}/projects/{pid}").json()
    assert project["fpp"]["host"] == fake_fpp.host
    assert project["fpp"]["playlist_name"] == "FacadeKit - Show"

    align = _render(client, pid, "alignment_check")
    resp = client.post(
        f"{API}/projects/{pid}/publish",
        json={"host": fake_fpp.host, "render_id": align["render_id"], "start": False},
    )
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    assert job["result"]["media_name"].endswith("_align.mp4")
    assert job["result"]["playlist_name"] == "FacadeKit - Show (alignment)"


def test_publish_validation(client: TestClient, tmp_path: Path, fake_fpp: FakeFpp) -> None:
    pid = _make_project(client, tmp_path)
    resp = client.post(
        f"{API}/projects/{pid}/publish",
        json={"host": fake_fpp.host, "render_id": "rnd_deadbeef", "start": True},
    )
    assert resp.status_code == 404
    show = _render(client, pid)
    resp = client.post(
        f"{API}/projects/{pid}/publish",
        json={"host": "a/b", "render_id": show["render_id"], "start": True},
    )
    assert resp.status_code == 422
    assert client.post(f"{API}/fpp/probe", json={"host": "u@h"}).status_code == 422
