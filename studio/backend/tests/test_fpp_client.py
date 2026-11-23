from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from facadekit.errors import FkError
from facadekit.fpp.client import FppClient
from tests.conftest import FakeFpp


def _code(exc: pytest.ExceptionInfo[FkError]) -> str:
    return exc.value.code


def _make_file(path: Path, size: int) -> None:
    block = os.urandom(1024 * 1024)
    with path.open("wb") as handle:
        for _ in range(size // len(block)):
            handle.write(block)


def test_version(fake_fpp: FakeFpp) -> None:
    client = FppClient(fake_fpp.host)
    version = client.version()
    assert (version.major, version.minor) == (10, 4)
    client.close()


def test_unreachable() -> None:
    client = FppClient("127.0.0.1:9", timeout=1)
    with pytest.raises(FkError) as exc:
        client.version()
    assert _code(exc) == "FPP_UNREACHABLE"


def test_host_normalised(fake_fpp: FakeFpp) -> None:
    assert FppClient(f"http://{fake_fpp.host}/").version().major == 10
    with pytest.raises(FkError) as exc:
        FppClient("  ")
    assert _code(exc) == "BAD_REQUEST"


def test_upload_and_duration(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    src = tmp_path / "big.mp4"
    _make_file(src, 20 * 1024 * 1024)
    client = FppClient(fake_fpp.host)
    seen: list[float] = []
    client.upload("videos", src, "big.mp4", progress=lambda f, _m: seen.append(f))
    patches = [r for r in fake_fpp.app.state.requests if r.startswith("PATCH")]
    assert len(patches) == 3
    assert seen[-1] == 1.0
    stored = fake_fpp.state_dir / "videos" / "big.mp4"
    assert hashlib.sha256(stored.read_bytes()).digest() == hashlib.sha256(src.read_bytes()).digest()
    assert "big.mp4" in client.list_files("videos")
    assert client.media_duration("big.mp4") == 60.0
    assert client.media_duration("missing.mp4") is None


def test_upload_cancel(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    src = tmp_path / "big.mp4"
    _make_file(src, 20 * 1024 * 1024)
    client = FppClient(fake_fpp.host)
    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls == 2

    with pytest.raises(FkError) as exc:
        client.upload("videos", src, "big.mp4", cancel=cancel)
    assert _code(exc) == "CANCELLED"


def test_upload_bad_name(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    with pytest.raises(FkError) as exc:
        FppClient(fake_fpp.host).upload("videos", src, "a b.mp4")
    assert _code(exc) == "BAD_REQUEST"


def test_upload_empty_file(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    src = tmp_path / "e.mp4"
    src.write_bytes(b"")
    with pytest.raises(FkError) as exc:
        FppClient(fake_fpp.host).upload("videos", src, "e.mp4")
    assert _code(exc) == "BAD_REQUEST"


def test_playlist_roundtrip(fake_fpp: FakeFpp) -> None:
    client = FppClient(fake_fpp.host)
    entry = {"type": "media", "mediaName": "x.mp4"}
    name = "FacadeKit - Test"
    client.put_playlist(name, [entry])
    import json

    stored = json.loads((fake_fpp.state_dir / "playlists" / f"{name}.json").read_text())
    assert stored["mainPlaylist"] == [entry]
    assert stored["playlistInfo"]["total_items"] == 1
    client.start_playlist(name)
    status = client.status()
    assert status["status_name"] == "playing"
    assert status["current_playlist"] == name
    assert status["seconds_played"] == 0.0
    client.stop()
    status = client.status()
    assert status["status_name"] == "idle"
    assert status["current_playlist"] is None


def test_start_missing_playlist(fake_fpp: FakeFpp) -> None:
    with pytest.raises(FkError) as exc:
        FppClient(fake_fpp.host).start_playlist("missing")
    assert _code(exc) == "FPP_BAD_RESPONSE"


# -- FPP 10.4 contract and error reporting (docs/17-fpp-integration-audit.md) ---------------

import errno as _errno  # noqa: E402
import logging  # noqa: E402
import socket  # noqa: E402
from collections.abc import Callable  # noqa: E402

import httpx  # noqa: E402

from facadekit.fpp import client as client_mod  # noqa: E402
from facadekit.fpp.client import FppError  # noqa: E402
from facadekit.fpp.fake import create_fake_fpp  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "fpp"


def _mocked(handler: Callable[[httpx.Request], httpx.Response], **kw: object) -> FppClient:
    client = FppClient("192.168.1.50", **kw)  # type: ignore[arg-type]
    client._client = httpx.Client(
        base_url="http://192.168.1.50",
        transport=httpx.MockTransport(handler),
        auth=client._client.auth,
    )
    return client


def _raising(exc: Exception) -> FppClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return _mocked(handler)


def _reason(client: FppClient, call: Callable[[FppClient], object] = FppClient.version) -> FppError:
    with pytest.raises(FppError) as exc:
        call(client)
    return exc.value


def test_real_10_4_version_reply_parses() -> None:
    body = (FIXTURES / "fppd_version_10_4.json").read_text("utf-8")
    client = _mocked(lambda r: httpx.Response(200, text=body))
    version = client.version()
    assert (version.version, version.major, version.minor, version.branch) == (
        "10.4", 10, 4, "v10.4"
    )  # fmt: skip


@pytest.mark.parametrize(
    ("reply", "major", "minor"),
    [
        ({"version": "10.4-12-gabc1234-dirty", "majorVersion": "10", "minorVersion": "4"}, 10, 4),
        ({"version": "10.1.2", "majorVersion": "10", "minorVersion": "1"}, 10, 1),
        ({"version": "10.x-master-57-g1", "majorVersion": "10", "minorVersion": "1057"}, 10, 1057),
        ({"version": "10.4", "majorVersion": 10, "minorVersion": 4}, 10, 4),
        ({"version": "9.5.3"}, 9, 5),
    ],
)
def test_version_formats(reply: dict[str, object], major: int, minor: int) -> None:
    client = _mocked(lambda r: httpx.Response(200, json=reply))
    version = client.version()
    assert (version.major, version.minor) == (major, minor)


def test_version_unknown_is_bad_version() -> None:
    reply = {"version": "Unknown", "majorVersion": "Unknown", "minorVersion": "Unknown"}
    err = _reason(_mocked(lambda r: httpx.Response(200, json=reply)))
    assert err.code == "FPP_BAD_RESPONSE" and err.reason == "bad_version"


def test_not_fpp_html_and_foreign_json() -> None:
    html = _mocked(lambda r: httpx.Response(200, html="<html>router</html>"))
    err = _reason(html)
    assert err.reason == "not_fpp" and "not like Falcon Player" in err.message
    other = _mocked(lambda r: httpx.Response(200, json={"name": "some other device"}))
    assert _reason(other).reason == "not_fpp"


def test_http_error_status_keeps_body_excerpt() -> None:
    err = _reason(_mocked(lambda r: httpx.Response(500, text="fppd is not running")))
    assert err.code == "FPP_BAD_RESPONSE" and err.reason == "http_status"
    assert "500" in err.message and "fppd is not running" in err.message


def test_non_json_reply() -> None:
    client = _mocked(lambda r: httpx.Response(200, text="oops"))
    err = _reason(client, FppClient.system_info)
    assert err.reason == "not_json"


def test_transport_errors_are_told_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "_is_macos", lambda: False)
    dns = httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
    dns.__cause__ = socket.gaierror(8, "nodename nor servname provided, or not known")
    refused = httpx.ConnectError("refused")
    refused.__cause__ = ConnectionRefusedError(_errno.ECONNREFUSED, "Connection refused")
    no_route = httpx.ConnectError("no route")
    no_route.__cause__ = OSError(_errno.EHOSTUNREACH, "No route to host")
    cases: list[tuple[Exception, str]] = [
        (dns, "dns"),
        (refused, "refused"),
        (no_route, "no_route"),
        (httpx.ConnectTimeout("t"), "timeout"),
        (httpx.ReadTimeout("t"), "timeout"),
        (httpx.RemoteProtocolError("bad"), "not_http"),
    ]
    for exc, reason in cases:
        err = _reason(_raising(exc))
        assert (err.code, err.reason) == ("FPP_UNREACHABLE", reason), exc
        assert "Local Network" not in err.message


def test_local_network_hint_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "_is_macos", lambda: True)
    no_route = httpx.ConnectError("no route")
    no_route.__cause__ = OSError(_errno.EHOSTUNREACH, "No route to host")
    err = _reason(_raising(no_route))
    assert "Privacy & Security > Local Network" in err.message
    assert "Terminal" in err.message
    refused = httpx.ConnectError("refused")
    refused.__cause__ = ConnectionRefusedError(_errno.ECONNREFUSED, "Connection refused")
    assert "Local Network" not in _reason(_raising(refused)).message


def test_dot_local_dns_hint() -> None:
    dns = httpx.ConnectError("x")
    dns.__cause__ = socket.gaierror(8, "nodename nor servname provided, or not known")
    client = _raising(dns)
    client.host = "fpp.local"
    err = _reason(client)
    assert err.reason == "dns" and "mDNS" in err.message and "fpp.local" in err.message


def test_real_refused_connection_and_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="facadekit.fpp.client"):
        err = _reason(FppClient("127.0.0.1:9", timeout=1))
    assert err.code == "FPP_UNREACHABLE" and err.reason in ("refused", "timeout")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ConnectError" in logged or "Timeout" in logged


def test_password_required_rejected_and_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import serve_app

    monkeypatch.delenv(client_mod.PASSWORD_ENV, raising=False)
    with serve_app(create_fake_fpp(tmp_path / "fpp", password="falcon2")) as host:
        err = _reason(FppClient(host))
        assert (err.code, err.reason) == ("FPP_AUTH_REQUIRED", "auth_required")
        assert client_mod.PASSWORD_ENV in err.message
        assert _reason(FppClient(host, password="wrong")).reason == "auth_rejected"
        assert FppClient(host, password="falcon2").version().major == 10
        monkeypatch.setenv(client_mod.PASSWORD_ENV, "falcon2")
        assert FppClient(host).version().major == 10


def test_no_origin_header_is_sent(fake_fpp: FakeFpp) -> None:
    client = FppClient(fake_fpp.host)
    client.stop()
    client.close()
    assert all("origin" not in headers for headers in fake_fpp.app.state.headers)


def test_https_prefix_is_kept() -> None:
    client = FppClient("https://192.168.1.50:8443/")
    assert client.scheme == "https" and client.host == "192.168.1.50:8443"
    assert str(client._client.base_url).startswith("https://192.168.1.50:8443")
    assert FppClient("http://192.168.1.50").scheme == "http"


def test_unknown_directory_is_reported(fake_fpp: FakeFpp) -> None:
    err = _reason(FppClient(fake_fpp.host), lambda c: c.list_files("video"))
    assert err.reason == "bad_directory" and "'video'" in err.message


def test_list_files_skips_directories(fake_fpp: FakeFpp) -> None:
    (fake_fpp.state_dir / "videos" / "sub").mkdir(parents=True)
    (fake_fpp.state_dir / "videos" / "a.mp4").write_bytes(b"x")
    assert FppClient(fake_fpp.host).list_files("videos") == ["a.mp4"]


def test_list_files_name_only_shape() -> None:
    client = _mocked(lambda r: httpx.Response(200, json=["a.mp4", "b.mp4"]))
    assert client.list_files("videos") == ["a.mp4", "b.mp4"]


def test_playlist_write_error_in_200_body() -> None:
    reply = {"name": "p", "Status": "Error", "Message": "Unable to open file for writing"}
    client = _mocked(lambda r: httpx.Response(200, json=reply))
    err = _reason(client, lambda c: c.put_playlist("p", []))
    assert err.reason == "bad_status" and "Unable to open file" in err.message


def test_playlist_saved_reply_is_accepted(fake_fpp: FakeFpp) -> None:
    client = FppClient(fake_fpp.host)
    client.put_playlist("p", [{"type": "media", "mediaName": "a.mp4"}])
    stored = client.get_playlist("p")
    assert stored is not None and stored["version"] == 4
    assert client.list_playlists() == ["p"]
    client.delete_playlist("p")
    assert client.get_playlist("p") is None


def test_upload_last_chunk_status_is_checked(tmp_path: Path) -> None:
    src = tmp_path / "a.mp4"
    src.write_bytes(b"abc")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, text="abc.123")
        return httpx.Response(200, json={"status": "OK", "file": "a.mp4", "size": 2})

    with pytest.raises(FppError) as exc:
        _mocked(handler).upload("videos", src, "a.mp4")
    assert exc.value.code == "FPP_UPLOAD_FAILED" and "2 of 3" in exc.value.message


def test_upload_refuses_dot_dot(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    with pytest.raises(FkError) as exc:
        FppClient(fake_fpp.host).upload("videos", src, "a..b.mp4")
    assert exc.value.code == "BAD_REQUEST"


def test_upload_and_delete_file(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    src = tmp_path / "a.mp4"
    src.write_bytes(b"abc")
    client = FppClient(fake_fpp.host)
    client.upload("videos", src, "a.mp4")
    client.delete_file("videos", "a.mp4")
    assert client.list_files("videos") == []
    with pytest.raises(FppError):
        client.delete_file("videos", "a.mp4")


def test_start_is_confirmed_by_status(fake_fpp: FakeFpp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "START_CONFIRM_S", 0.3)
    client = FppClient(fake_fpp.host)
    err = _reason(client, lambda c: c.start_playlist("missing"))
    assert err.reason == "not_started"
    client.start_playlist("missing", confirm=False)  # the bare reply is a plain 200
