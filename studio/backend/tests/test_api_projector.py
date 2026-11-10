from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from facadekit.api.app import create_app
from facadekit.api.deps import AppState
from facadekit.devices.display_ws import WsDisplay
from facadekit.errors import BAD_IMAGE_SIZE, PROJECTOR_TIMEOUT, FkError

API = "/api/v1"


def _state(client: TestClient) -> AppState:
    state: AppState = client.app.state.fk  # type: ignore[attr-defined]
    return state


def _hello(ws: Any, width: int = 960, height: int = 540, dpr: float = 2) -> None:
    ws.send_json({"type": "hello", "width": width, "height": height, "dpr": dpr})


def _wait_width(client: TestClient, width: int | None) -> None:
    deadline = time.monotonic() + 5
    while client.get(f"{API}/projector/status").json()["width"] != width:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_no_page(client: TestClient) -> None:
    status = client.get(f"{API}/projector/status").json()
    assert status["connected"] is False
    resp = client.post(f"{API}/projector/show", json={"mode": "white"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PROJECTOR_NOT_CONNECTED"


def test_hello_and_show_grid(client: TestClient) -> None:
    with client.websocket_connect("/ws/projector") as ws:
        _hello(ws)
        _wait_width(client, 1920)
        status = client.get(f"{API}/projector/status").json()
        assert (status["connected"], status["width"], status["height"]) == (True, 1920, 1080)
        assert status["dpr"] == 2
        resp = client.post(f"{API}/projector/show", json={"mode": "grid"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "grid"
        msg = ws.receive_json()
        assert msg["type"] == "grid"
        assert isinstance(msg["seq"], int)
    _wait_width(client, None)
    assert client.get(f"{API}/projector/status").json()["connected"] is False


def test_outlines(client: TestClient) -> None:
    project = client.post(f"{API}/projects", json={"name": "Show"}).json()
    project["surfaces"] = [
        {
            "id": "srf_1",
            "name": "Garage door",
            "quad": [[0, 0], [100, 0], [100, 50], [0, 50]],
            "aspect": 2.0,
        }
    ]
    saved = client.put(f"{API}/projects/show", json=project)
    assert saved.status_code == 200, saved.text
    with client.websocket_connect("/ws/projector") as ws:
        _hello(ws)
        _wait_width(client, 1920)
        resp = client.post(
            f"{API}/projector/show",
            json={"mode": "outlines", "project_id": "show", "selected_id": "srf_1"},
        )
        assert resp.status_code == 200
        msg = ws.receive_json()
        assert msg["type"] == "outlines"
        assert (msg["width"], msg["height"]) == (1920, 1080)
        assert [(s["id"], s["name"], s["selected"]) for s in msg["surfaces"]] == [
            ("srf_1", "Garage door", True)
        ]
        client.post(f"{API}/projector/show", json={"mode": "outlines", "project_id": "show"})
        assert ws.receive_json()["surfaces"][0]["selected"] is False
        assert "grid" not in msg["surfaces"][0]
        bad = client.post(f"{API}/projector/show", json={"mode": "compositor"})
        assert bad.status_code == 422


def test_outlines_grids_pass_through(client: TestClient) -> None:
    project = client.post(f"{API}/projects", json={"name": "Grid"}).json()
    project["surfaces"] = [
        {
            "id": "srf_1",
            "name": "A",
            "quad": [[0, 0], [100, 0], [100, 50], [0, 50]],
            "aspect": 2.0,
        },
        {
            "id": "srf_2",
            "name": "B",
            "quad": [[0, 0], [100, 0], [100, 50], [0, 50]],
            "aspect": 2.0,
        },
    ]
    assert client.put(f"{API}/projects/grid", json=project).status_code == 200
    grid = [[[0.0, 0.0], [10.0, 5.0]], [[0.0, 10.0], [10.0, 12.5]]]
    with client.websocket_connect("/ws/projector") as ws:
        _hello(ws)
        _wait_width(client, 1920)
        resp = client.post(
            f"{API}/projector/show",
            json={"mode": "outlines", "project_id": "grid", "grids": {"srf_1": grid}},
        )
        assert resp.status_code == 200
        surfaces = ws.receive_json()["surfaces"]
        assert surfaces[0]["grid"] == grid
        assert "grid" not in surfaces[1]


def test_ws_display(client: TestClient) -> None:
    state = _state(client)
    display = WsDisplay(state.projector)
    with client.websocket_connect("/ws/projector") as ws:
        _hello(ws, 1920, 1080, 1)
        _wait_width(client, 1920)
        assert display.size == (1920, 1080)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                display.show(np.zeros((1080, 1920), np.uint8))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        msg = ws.receive_json()
        assert msg["type"] == "image"
        assert thread.is_alive()
        png = client.get(msg["url"])
        assert png.status_code == 200
        assert png.headers["content-type"] == "image/png"
        decoded = cv2.imdecode(np.frombuffer(png.content, np.uint8), cv2.IMREAD_UNCHANGED)
        assert decoded.shape[:2] == (1080, 1920)
        ws.send_json({"type": "ack", "seq": msg["seq"]})
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert errors == []
        assert client.get(f"{API}/projector/status").json()["mode"] == "image"

        with pytest.raises(FkError) as bad:
            display.show(np.zeros((10, 10), np.uint8))
        assert bad.value.code == BAD_IMAGE_SIZE

        state.projector.ack_timeout = 0.3
        with pytest.raises(FkError) as timeout:
            display.show(np.zeros((1080, 1920, 3), np.uint8))
        assert timeout.value.code == PROJECTOR_TIMEOUT


def test_image_store_keeps_latest_four(client: TestClient) -> None:
    hub = _state(client).projector
    seqs = [hub.put_image(b"x") for _ in range(6)]
    with pytest.raises(FkError):
        hub.get_image(seqs[0])
    assert hub.get_image(seqs[-1]) == b"x"
    assert client.get(f"{API}/projector/image/{seqs[0]}.png").status_code == 404


def test_second_connection_replaces_first(client: TestClient) -> None:
    with client.websocket_connect("/ws/projector") as first:
        _hello(first)
        _wait_width(client, 1920)
        with client.websocket_connect("/ws/projector") as second:
            with pytest.raises(WebSocketDisconnect) as closed:
                first.receive_json()
            assert closed.value.code == 4000
            _hello(second, 640, 360, 1)
            _wait_width(client, 640)
            assert client.get(f"{API}/projector/status").json()["connected"] is True


def test_events_socket(client: TestClient) -> None:
    with client.websocket_connect("/ws/events") as events:
        with client.websocket_connect("/ws/projector") as ws:
            _hello(ws)
            msg = events.receive_json()
            assert msg["type"] == "projector"
            assert msg["status"]["connected"] is True
            assert msg["status"]["width"] == 1920
            _wait_width(client, 1920)
        assert events.receive_json()["status"]["connected"] is False

        _state(client).jobs.submit("render", lambda progress, cancelled: {})
        seen = events.receive_json()
        assert seen["type"] == "job"
        assert seen["job"]["kind"] == "render"


def test_transport(client: TestClient) -> None:
    body = {"project_id": "p", "playing": True, "t_ms": 1200, "revision": 4}
    assert client.post(f"{API}/projector/transport", json=body).status_code == 204
    with client.websocket_connect("/ws/projector") as ws:
        _hello(ws)
        _wait_width(client, 1920)
        assert client.post(f"{API}/projector/transport", json=body).status_code == 204
        msg = ws.receive_json()
        assert msg["type"] == "transport"
        assert (msg["playing"], msg["t_ms"], msg["revision"]) == (True, 1200, 4)


def test_sim_mode(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as sim_client:
        status = sim_client.get(f"{API}/projector/status").json()
        assert (status["connected"], status["width"], status["height"]) == (True, 1920, 1080)
        assert sim_client.post(f"{API}/projector/show", json={"mode": "grid"}).status_code == 200
