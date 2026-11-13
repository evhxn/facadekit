from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from facadekit.api import origin_guard
from facadekit.api.app import create_app

API = "/api/v1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("localhost:8137", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]:8137", "::1"),
        ("EXAMPLE.com:80", "example.com"),
        ("", None),
        ("[::1", None),
        ("host:abc", None),
    ],
)
def test_host_of(value: str, expected: str | None) -> None:
    assert origin_guard.host_of(value) == expected


def test_foreign_host_is_rejected(client: TestClient) -> None:
    resp = client.get(f"{API}/health", headers={"Host": "evil.example.com"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    assert client.get(f"{API}/health", headers={"Host": "localhost:8137"}).status_code == 200


def test_foreign_origin_on_write_is_rejected(client: TestClient) -> None:
    headers = {"Host": "localhost:8137", "Origin": "http://evil.example.com"}
    assert client.post(f"{API}/projects", json={"name": "A"}, headers=headers).status_code == 403
    headers["Origin"] = "null"
    assert client.post(f"{API}/projects", json={"name": "A"}, headers=headers).status_code == 403
    headers["Origin"] = "http://localhost:5173"
    assert client.post(f"{API}/projects", json={"name": "A"}, headers=headers).status_code == 201


def test_foreign_origin_on_api_read_is_rejected(client: TestClient) -> None:
    """GETs with effects (camera snapshot, /fpp/status) must not be triggered by other sites."""
    headers = {"Host": "localhost:8137", "Origin": "http://evil.example.com"}
    for path in ("/health", "/projects", "/fpp/status?host=192.168.1.1"):
        resp = client.get(f"{API}{path}", headers=headers)
        assert resp.status_code == 403, path
        assert resp.json()["error"]["message"] == origin_guard.BLOCKED_MESSAGE
    headers["Origin"] = "http://localhost:5173"
    assert client.get(f"{API}/health", headers=headers).status_code == 200


def test_cross_site_fetch_of_api_is_rejected(client: TestClient) -> None:
    """<img src> and no-cors fetches send no Origin; browsers mark them Sec-Fetch-Site."""
    base = {"Host": "localhost:8137"}
    cross = {**base, "Sec-Fetch-Site": "cross-site"}
    assert client.get(f"{API}/devices/cameras/sim/snapshot.jpg", headers=cross).status_code == 403
    assert client.get(f"{API}/fpp/status?host=192.168.1.1", headers=cross).status_code == 403
    assert client.post(f"{API}/projects", json={"name": "x"}, headers=cross).status_code == 403
    for site in ("same-origin", "same-site", "none"):
        ok = client.get(f"{API}/health", headers={**base, "Sec-Fetch-Site": site})
        assert ok.status_code == 200, site
    assert client.get(f"{API}/health", headers=base).status_code == 200  # curl, scripts


def test_cross_site_link_to_a_page_still_loads(client: TestClient) -> None:
    headers = {
        "Host": "localhost:8137",
        "Sec-Fetch-Site": "cross-site",
        "Origin": "http://evil.example.com",
    }
    assert client.get("/", headers=headers).status_code == 200
    assert client.get("/p/demo/setup", headers=headers).status_code == 200


def test_websocket_origin(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as info:
        with client.websocket_connect(
            "/ws/events", headers={"Origin": "http://evil.example.com"}
        ) as ws:
            ws.receive_text()
    assert info.value.code == 1008
    with client.websocket_connect("/ws/events"):
        pass


def test_extra_hosts_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACADEKIT_ALLOWED_HOSTS", " Studio.lan ,, ")
    with TestClient(create_app(data_dir=tmp_path)) as c:
        assert c.get(f"{API}/health", headers={"Host": "studio.lan"}).status_code == 200
        assert c.get(f"{API}/health", headers={"Host": "other.lan"}).status_code == 403


def test_lan_address_is_accepted_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(origin_guard, "lan_addresses", lambda: ["192.168.1.20"])
    with TestClient(create_app(data_dir=tmp_path, lan=True)) as c:
        assert c.get(f"{API}/health", headers={"Host": "192.168.1.20:8137"}).status_code == 200
