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
