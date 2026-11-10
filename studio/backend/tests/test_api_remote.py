from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from facadekit.api import remote_auth
from facadekit.api.app import create_app
from facadekit.cli import build_parser

CODE = "123456"
PHONE = "192.168.1.50"


class Addr:
    host = "127.0.0.1"

    def __call__(self) -> str:
        return self.host


def _make(tmp_path: Path, *, lan: bool) -> tuple[TestClient, Addr]:
    addr = Addr()
    app = create_app(data_dir=tmp_path, lan=lan, pairing_code=CODE, client_addr_override=addr)
    return TestClient(app), addr


@pytest.fixture
def lan(tmp_path: Path) -> Iterator[tuple[TestClient, Addr]]:
    c, addr = _make(tmp_path, lan=True)
    with c:
        yield c, addr


@pytest.fixture
def nolan(tmp_path: Path) -> Iterator[tuple[TestClient, Addr]]:
    c, addr = _make(tmp_path, lan=False)
    with c:
        yield c, addr


def _project(c: TestClient) -> str:
    pid: str = c.post("/api/v1/projects", json={"name": "Yard"}).json()["id"]
    return pid


def test_cli_default_is_loopback_and_lan_flag_is_opt_in() -> None:
    parser = build_parser()
    assert parser.parse_args(["serve"]).host == "127.0.0.1"
    assert parser.parse_args(["serve"]).lan is False
    assert parser.parse_args(["serve", "--lan"]).lan is True


def test_loopback_works_without_cookie_in_both_modes(
    lan: tuple[TestClient, Addr], nolan: tuple[TestClient, Addr]
) -> None:
    for c, _ in (lan, nolan):
        assert c.get("/api/v1/health").status_code == 200
        pid = _project(c)
        assert c.get(f"/api/v1/projects/{pid}").status_code == 200


def test_info_modes(lan: tuple[TestClient, Addr], nolan: tuple[TestClient, Addr]) -> None:
    assert nolan[0].get("/api/v1/remote/info").json() == {
        "enabled": False,
        "code": None,
        "urls": [],
    }
    info = lan[0].get("/api/v1/remote/info").json()
    assert info["enabled"] is True
    assert info["code"] == CODE
    assert len(info["urls"]) == len(set(info["urls"]))


def test_info_forbidden_from_non_loopback(lan: tuple[TestClient, Addr]) -> None:
    c, addr = lan
    addr.host = PHONE
    r = c.get("/api/v1/remote/info")
    assert r.status_code == 403
    assert CODE not in r.text
    c.post("/api/v1/remote/pair", json={"code": CODE})
    assert c.get("/api/v1/remote/info").status_code == 403


def test_non_loopback_without_cookie_is_forbidden(lan: tuple[TestClient, Addr]) -> None:
    c, addr = lan
    pid = _project(c)
    addr.host = PHONE
    r = c.get(f"/api/v1/projects/{pid}")
    assert r.status_code == 403
    assert r.json()["error"] == {"code": "BAD_REQUEST", "message": "Pair this device first"}
    assert c.get("/some/ui/page").status_code == 200  # web UI is exempt


def test_pair_then_restricted_routes(lan: tuple[TestClient, Addr]) -> None:
    c, addr = lan
    pid = _project(c)
    addr.host = PHONE
    r = c.post("/api/v1/remote/pair", json={"code": CODE})
    assert r.status_code == 200 and r.json() == {"ok": True}
    cookie = r.headers["set-cookie"]
    assert "fk_remote=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    project = c.get(f"/api/v1/projects/{pid}")
    assert project.status_code == 200
    assert c.put(f"/api/v1/projects/{pid}", json=project.json()).status_code == 200
    assert c.get("/api/v1/health").status_code == 200
    assert c.get("/api/v1/projector/status").status_code == 200
    assert c.post(f"/api/v1/projects/{pid}/renders", json={}).status_code == 403
    assert c.delete(f"/api/v1/projects/{pid}").status_code == 403
    assert c.get("/api/v1/projects").status_code == 403
    assert c.post("/api/v1/fpp/probe", json={"host": "x"}).status_code == 403
    with c.websocket_connect("/ws/events"):
        pass
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/projector"):
            pass


def test_websocket_requires_cookie(lan: tuple[TestClient, Addr]) -> None:
    c, addr = lan
    addr.host = PHONE
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/events"):
            pass


def test_lockout_after_five_wrong_attempts(lan: tuple[TestClient, Addr]) -> None:
    c, addr = lan
    addr.host = PHONE
    for _ in range(5):
        assert c.post("/api/v1/remote/pair", json={"code": "000000"}).status_code == 403
    r = c.post("/api/v1/remote/pair", json={"code": CODE})
    assert r.status_code == 403
    assert r.json()["error"]["message"] == "Too many attempts"
    addr.host = "192.168.1.51"  # other addresses are unaffected
    assert c.post("/api/v1/remote/pair", json={"code": CODE}).status_code == 200


def test_lockout_expires(tmp_path: Path) -> None:
    now = [0.0]
    auth = remote_auth.RemoteAuth(lan=True, code=CODE, clock=lambda: now[0])
    for _ in range(5):
        assert auth.pair(PHONE, "000000")[0] is None
    assert auth.pair(PHONE, CODE) == (None, "Too many attempts")
    now[0] += remote_auth.LOCKOUT_S + 1
    assert auth.pair(PHONE, CODE)[0] is not None


def test_pairing_disabled_without_lan(nolan: tuple[TestClient, Addr]) -> None:
    c, addr = nolan
    addr.host = PHONE
    assert c.post("/api/v1/remote/pair", json={"code": CODE}).status_code == 403
    assert c.get("/api/v1/health").status_code == 403


def test_session_expires(tmp_path: Path) -> None:
    now = [0.0]
    auth = remote_auth.RemoteAuth(lan=True, code=CODE, clock=lambda: now[0])
    token, _ = auth.pair(PHONE, CODE)
    assert auth._valid_session(token)
    now[0] += remote_auth.SESSION_TTL_S + 1
    assert not auth._valid_session(token)
