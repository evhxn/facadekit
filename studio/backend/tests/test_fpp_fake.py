from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from facadekit.fpp.fake import create_fake_fpp
from tests.conftest import FakeFpp


def _upload(client: httpx.Client, dir_name: str, name: str, data: bytes, chunk: int) -> None:
    client.post(f"/api/file/{dir_name}")
    offset = 0
    while offset < len(data):
        piece = data[offset : offset + chunk]
        resp = client.patch(
            f"/api/file/{dir_name}",
            content=piece,
            headers={
                "Upload-Name": name,
                "Upload-Offset": str(offset),
                "Upload-Length": str(len(data)),
                "Content-Type": "application/offset+octet-stream",
            },
        )
        assert resp.status_code == 200, resp.text
        offset += len(piece)


FIXTURES = Path(__file__).parent / "fixtures" / "fpp"


def test_version(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        body = client.get("/api/fppd/version").json()
    # FPP 10.4 sends the version parts as strings (src/fppversion.sh, httpAPI.cpp) [V]
    assert body["majorVersion"] == "10"
    assert body["version"] == "10.4"
    assert body["fppdAPI"] == "v1"


def test_version_matches_real_fpp_10_4_reply(fake_fpp: FakeFpp) -> None:
    """The reply recorded from a real FPP 10.4 (owner's Pi, GET /api/fppd/version)."""
    real = json.loads((FIXTURES / "fppd_version_10_4.json").read_text("utf-8"))
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        body = client.get("/api/fppd/version").json()
    assert body == real


def test_version_is_derived_from_argument(tmp_path: Path) -> None:
    app = create_fake_fpp(tmp_path / "fpp", version="9.2")
    with TestClient(app) as client:
        body = client.get("/api/fppd/version").json()
    assert body["majorVersion"] == "9"
    assert body["minorVersion"] == "2"


def test_requests_are_recorded(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        client.get("/api/fppd/version")
    assert "GET /api/fppd/version" in fake_fpp.app.state.requests


def test_two_chunk_upload_is_listed_and_concatenated(fake_fpp: FakeFpp) -> None:
    data = bytes(range(256)) * 4
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        _upload(client, "videos", "clip.mp4", data, chunk=512)
        listing = client.get("/api/files/videos").json()
    assert listing["status"] == "ok"
    assert [(f["name"], f["sizeBytes"]) for f in listing["files"]] == [("clip.mp4", len(data))]
    assert (fake_fpp.state_dir / "videos" / "clip.mp4").read_bytes() == data
    assert not list((fake_fpp.state_dir / "videos").glob(".partial_*"))


def test_wrong_offset_returns_409(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        client.post("/api/file/videos")
        resp = client.patch(
            "/api/file/videos",
            content=b"abcd",
            headers={"Upload-Name": "clip.mp4", "Upload-Offset": "2", "Upload-Length": "8"},
        )
    assert resp.status_code == 409


def test_media_duration_404_before_upload_and_200_after(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        assert client.get("/api/media/clip.mp4/duration").status_code == 404
        _upload(client, "videos", "clip.mp4", b"0123456789", chunk=4)
        resp = client.get("/api/media/clip.mp4/duration")
    assert resp.status_code == 200
    assert resp.json() == {"clip.mp4": {"duration": 60.0}}


def test_playlist_save_then_get_round_trip(fake_fpp: FakeFpp) -> None:
    playlist = {
        "name": "garage",
        "mainPlaylist": [{"type": "media", "enabled": 1, "playOnce": 0, "mediaName": "clip.mp4"}],
    }
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        assert client.post("/api/playlist/garage", json=playlist).status_code == 200
        stored = client.get("/api/playlist/garage").json()
        assert {k: stored[k] for k in playlist} == playlist
        assert stored["version"] == 4
        assert stored["playlistInfo"]["total_items"] == 1
        assert client.get("/api/playlists").json() == ["garage"]
        assert client.get("/api/playlist/missing").status_code == 404


def test_playlist_bad_name_is_400(fake_fpp: FakeFpp) -> None:
    # FPP's PlaylistFilePath refuses "\\" (and "/", NUL); it does not check mainPlaylist [V]
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        resp = client.post("/api/playlist/a%5Cb", json={"name": "a\\b", "mainPlaylist": []})
    assert resp.status_code == 400
    assert resp.json()["Status"] == "Error"


def test_start_sets_playing_and_stop_returns_to_idle(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        client.post("/api/playlist/garage", json={"mainPlaylist": []})
        assert client.post("/api/playlist/garage/start").status_code == 200
        status = client.get("/api/fppd/status").json()
        assert status["status_name"] == "playing"
        assert status["current_playlist"]["playlist"] == "garage"

        assert client.post("/api/playlists/stop").status_code == 200
        status = client.get("/api/fppd/status").json()
        assert status["status_name"] == "idle"


def test_start_unknown_playlist_answers_200_and_plays_nothing(fake_fpp: FakeFpp) -> None:
    # fppd's Start Playlist command answers "Playlist Starting" either way [V]
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        resp = client.post("/api/playlist/nope/start")
        assert resp.status_code == 200
        assert resp.text == "Playlist Starting"
        assert client.get("/api/fppd/status").json()["status_name"] == "idle"


def test_dot_dot_in_name_is_400(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        resp = client.patch(
            "/api/file/videos",
            content=b"x",
            headers={"Upload-Name": "../escape.mp4", "Upload-Offset": "0", "Upload-Length": "1"},
        )
    assert resp.status_code == 400
    assert not (fake_fpp.state_dir.parent / "escape.mp4").exists()


def test_unknown_directory_is_200_invalid_directory(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        resp = client.get("/api/files/video")
        assert resp.status_code == 200
        assert resp.json() == {"status": "Invalid Directory"}
        assert client.get("/api/files/Videos").json()["status"] == "ok"


def test_upload_start_is_plain_text_and_chunk_reply_is_json(fake_fpp: FakeFpp) -> None:
    with httpx.Client(base_url=f"http://{fake_fpp.host}") as client:
        start = client.post("/api/file/videos")
        assert start.headers["content-type"].startswith("text/plain")
        resp = client.patch(
            "/api/file/videos",
            content=b"abc",
            headers={"Upload-Name": "a.mp4", "Upload-Offset": "0", "Upload-Length": "3"},
        )
    assert resp.json() == {"status": "OK", "file": "a.mp4", "dir": "videos", "size": 3}


def test_password_and_cross_site_gate(tmp_path: Path) -> None:
    app = create_fake_fpp(tmp_path / "fpp", password="secret")
    with TestClient(app) as client:
        assert client.get("/api/fppd/version").status_code == 401
        assert client.get("/api/fppd/version", auth=("admin", "secret")).status_code == 200
        assert client.get("/api/fppd/version", auth=("fpp", "secret")).status_code == 200
    app = create_fake_fpp(tmp_path / "fpp2")
    with TestClient(app) as client:
        resp = client.post("/api/playlists/stop", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403
        assert client.post("/api/playlists/stop").status_code == 200
