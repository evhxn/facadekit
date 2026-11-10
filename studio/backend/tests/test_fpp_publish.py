from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from facadekit.errors import FkError
from facadekit.fpp.fake import create_fake_fpp
from facadekit.fpp.publish import media_entry, probe_fpp, publish_render, sanitize_name
from tests.conftest import FakeFpp, _free_port


@pytest.fixture
def old_fpp(tmp_path: Path) -> Iterator[FakeFpp]:
    state_dir = tmp_path / "fpp94"
    app = create_fake_fpp(state_dir, version="9.4")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("fake FPP did not start")
        time.sleep(0.01)
    try:
        yield FakeFpp(host=f"127.0.0.1:{port}", state_dir=state_dir, app=app)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def mp4(tmp_path: Path) -> Path:
    path = tmp_path / "render.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def _playlist(fpp: FakeFpp, name: str) -> dict[str, object]:
    data = json.loads((fpp.state_dir / "playlists" / f"{name}.json").read_text("utf-8"))
    assert isinstance(data, dict)
    return data


def test_publish_start(fake_fpp: FakeFpp, mp4: Path) -> None:
    seen: list[tuple[float, str]] = []
    result = publish_render(
        fake_fpp.host,
        mp4,
        media_name="garage-2026.mp4",
        playlist_name="FacadeKit_Garage",
        start=True,
        progress=lambda f, m: seen.append((f, m)),
    )
    assert result == {
        "media_name": "fk_garage-2026.mp4",
        "playlist_name": "FacadeKit_Garage",
        "started": True,
    }
    assert (fake_fpp.state_dir / "videos" / "fk_garage-2026.mp4").is_file()
    entries = _playlist(fake_fpp, "FacadeKit_Garage")["mainPlaylist"]
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["type"] == "media"
    assert entries[0]["mediaName"] == "fk_garage-2026.mp4"
    assert entries[0]["videoOut"] == "--Default--"
    assert fake_fpp.app.state.playing == "FacadeKit_Garage"
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert {m for _, m in seen} >= {"Checking FPP", "Uploading", "Verifying", "Starting"}


def test_publish_no_start(fake_fpp: FakeFpp, mp4: Path) -> None:
    result = publish_render(fake_fpp.host, mp4, media_name="a.mp4", playlist_name="p", start=False)
    assert result["started"] is False
    assert fake_fpp.app.state.playing is None


def test_publish_sequence(fake_fpp: FakeFpp, mp4: Path) -> None:
    publish_render(
        fake_fpp.host,
        mp4,
        media_name="a.mp4",
        playlist_name="p",
        start=False,
        sequence_name="song.fseq",
    )
    entry = _playlist(fake_fpp, "p")["mainPlaylist"][0]  # type: ignore[index]
    assert entry["type"] == "both"
    assert entry["sequenceName"] == "song.fseq"


def test_too_old(old_fpp: FakeFpp, mp4: Path) -> None:
    with pytest.raises(FkError) as exc:
        publish_render(old_fpp.host, mp4, media_name="a.mp4", playlist_name="p", start=True)
    assert exc.value.code == "FPP_TOO_OLD"
    assert not (old_fpp.state_dir / "videos").exists()


def test_missing_local_file(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    with pytest.raises(FkError) as exc:
        publish_render(
            fake_fpp.host, tmp_path / "nope.mp4", media_name="a.mp4", playlist_name="p", start=True
        )
    assert exc.value.code == "NOT_FOUND"


def test_empty_playlist_name(fake_fpp: FakeFpp, mp4: Path) -> None:
    with pytest.raises(FkError) as exc:
        publish_render(fake_fpp.host, mp4, media_name="a.mp4", playlist_name=" ", start=False)
    assert exc.value.code == "BAD_REQUEST"


def test_probe(fake_fpp: FakeFpp, old_fpp: FakeFpp) -> None:
    result = probe_fpp(fake_fpp.host)
    assert result["reachable"] is True and result["ok"] is True
    assert result["major"] == 10 and result["problems"] == []
    closed = probe_fpp("127.0.0.1:9")
    assert closed["reachable"] is False and closed["ok"] is False
    assert len(closed["problems"]) == 1
    old = probe_fpp(old_fpp.host)
    assert old["reachable"] is True and old["ok"] is False and old["major"] == 9


def test_sanitize_name() -> None:
    assert sanitize_name("My Show (final).mp4") == "fk_My_Show_final_.mp4"
    assert sanitize_name("fk_x.mp4") == "fk_x.mp4"
    assert sanitize_name("äö.mp4") == "fk_show.mp4"


def test_media_entry_plain() -> None:
    assert "sequenceName" not in media_entry("fk_a.mp4")
