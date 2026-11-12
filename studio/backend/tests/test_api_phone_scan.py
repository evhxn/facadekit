"""T-208: phone scan sessions and the video upload route (docs/08 §4).

The light show is monkeypatched to a no-op (it holds the projector for about a minute); the
uploaded video is a synthetic phone recording from `make_phone_video` at 15 fps.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from facadekit.api import routes_calibration
from facadekit.api.app import create_app
from facadekit.calib import phone
from facadekit.calib.patterns import PatternSpec
from facadekit.sim.phone_video import make_phone_video
from facadekit.sim.scene import far_scene

API = "/api/v1"
CODE = "123456"
PHONE_HOST = "192.168.1.50"
FPS = 15
PREROLL_S = 11.3
SMALL_CAP = 1024 * 1024  # stands in for 4 GB in the stream-cap tests


class Addr:
    host = "127.0.0.1"

    def __call__(self) -> str:
        return self.host


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    scene = far_scene(0.9, cam_size=(1920, 1080), world_scale=2.0)
    out = tmp_path_factory.mktemp("phone_api") / "phone.mp4"
    make_phone_video(scene, PatternSpec(*scene.proj_size), out, preroll_s=PREROLL_S, fps=FPS)
    return out


@pytest.fixture(autouse=True)
def _no_light_show(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phone, "play_light_show", lambda *_a, **_k: None)
    monkeypatch.delenv("FACADEKIT_KEEP_PHONE_VIDEO", raising=False)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as c:
        yield c


def _app(c: TestClient) -> FastAPI:
    app = c.app
    assert isinstance(app, FastAPI)
    return app


def _project(c: TestClient) -> str:
    resp = c.post(f"{API}/projects", json={"name": "demo"})
    assert resp.status_code == 201, resp.text
    pid: str = resp.json()["id"]
    return pid


def _phone_dir(c: TestClient, pid: str) -> Path:
    return Path(_app(c).state.fk.store.project_dir(pid)) / "calibration" / ".phone"


def _wait(c: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while True:
        job: dict[str, Any] = c.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline, job
        time.sleep(0.05)


def _start(c: TestClient, pid: str, **body: Any) -> str:
    resp = c.post(f"{API}/projects/{pid}/phone-scans", json=body)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert _wait(c, data["job_id"])["state"] == "succeeded"
    sid: str = data["session_id"]
    return sid


def _upload(c: TestClient, pid: str, sid: str, content: bytes, name: str = "IMG_0001.MOV") -> Any:
    return c.post(
        f"{API}/projects/{pid}/phone-scans/{sid}/video",
        files={"file": (name, content, "video/quicktime")},
    )


def _leftovers(c: TestClient, pid: str) -> list[str]:
    folder = _phone_dir(c, pid)
    return (
        sorted(p.name for p in folder.iterdir() if p.suffix != ".json") if folder.exists() else []
    )


# --- start --------------------------------------------------------------------------------


def test_start_writes_session_and_job_succeeds(client: TestClient) -> None:
    pid = _project(client)
    resp = client.post(f"{API}/projects/{pid}/phone-scans", json={"countdown_s": 20})
    assert resp.status_code == 202
    data = resp.json()
    assert set(data) == {"session_id", "job_id"}
    sid = data["session_id"]
    assert sid.startswith("phs_") and len(sid) == 12
    job = _wait(client, data["job_id"])
    assert job["state"] == "succeeded" and job["kind"] == "calibration"
    assert job["result"] == {"session_id": sid}
    session = json.loads((_phone_dir(client, pid) / f"{sid}.json").read_text())
    assert set(session) == {
        "id", "created_at", "proj_w", "proj_h", "skip_lsb", "min_conf", "strategy", "learn",
    }  # fmt: skip
    assert (session["id"], session["proj_w"], session["proj_h"]) == (sid, 1920, 1080)
    assert session["learn"] is True and session["strategy"] == "standard"


def test_start_without_learning_uses_defaults(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = _project(client)
    sid = _start(client, pid, learn=False)
    session = json.loads((_phone_dir(client, pid) / f"{sid}.json").read_text())
    assert session["strategy"] is None and session["skip_lsb"] == 2 and session["learn"] is False
    monkeypatch.setenv("FACADEKIT_LEARN", "0")
    sid = _start(client, pid)
    assert json.loads((_phone_dir(client, pid) / f"{sid}.json").read_text())["strategy"] is None


@pytest.mark.parametrize("body", [{"countdown_s": 4}, {"countdown_s": 61}, {"extra": 1}])
def test_start_validates_body(client: TestClient, body: dict[str, Any]) -> None:
    pid = _project(client)
    assert client.post(f"{API}/projects/{pid}/phone-scans", json=body).status_code == 422
    assert not _phone_dir(client, pid).exists()


def test_start_unknown_project(client: TestClient) -> None:
    assert client.post(f"{API}/projects/nope/phone-scans", json={}).status_code == 404


def test_start_needs_projector(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path)) as c:
        pid = _project(c)
        resp = c.post(f"{API}/projects/{pid}/phone-scans", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "PROJECTOR_NOT_CONNECTED"


def test_second_light_show_is_refused_while_one_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()
    monkeypatch.setattr(phone, "play_light_show", lambda *_a, **_k: gate.wait(30))
    pid = _project(client)
    first = client.post(f"{API}/projects/{pid}/phone-scans", json={}).json()
    second = client.post(f"{API}/projects/{pid}/phone-scans", json={})
    assert second.status_code == 409
    assert second.json()["error"]["message"] == "A light show is already running."
    gate.set()
    assert _wait(client, first["job_id"])["state"] == "succeeded"
    assert client.post(f"{API}/projects/{pid}/phone-scans", json={}).status_code == 202


def test_cancelled_light_show_frees_the_projector(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def show(*_a: Any, cancel: Any = None, **_k: Any) -> None:
        while not cancel():
            time.sleep(0.01)
        raise phone.FkError("CANCELLED", "Light show cancelled")

    monkeypatch.setattr(phone, "play_light_show", show)
    pid = _project(client)
    job_id = client.post(f"{API}/projects/{pid}/phone-scans", json={}).json()["job_id"]
    client.post(f"{API}/jobs/{job_id}/cancel")
    assert _wait(client, job_id)["state"] == "cancelled"
    monkeypatch.setattr(phone, "play_light_show", lambda *_a, **_k: None)
    assert client.post(f"{API}/projects/{pid}/phone-scans", json={}).status_code == 202


# --- upload: happy path and failures ------------------------------------------------------


def test_upload_attaches_calibration_and_deletes_video(client: TestClient, video: Path) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    resp = _upload(client, pid, sid, video.read_bytes())
    assert resp.status_code == 202, resp.text
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    assert job["kind"] == "calibration"
    cal_id = job["result"]["calibration_id"]
    assert client.get(f"{API}/projects/{pid}").json()["calibration_id"] == cal_id
    cal = client.get(f"{API}/projects/{pid}/calibrations/{cal_id}").json()
    assert cal["camera_id"] == "phone" and cal["strategy"] == "standard"
    assert list(_phone_dir(client, pid).iterdir()) == []  # upload and session both deleted
    # the session is used up
    assert _upload(client, pid, sid, video.read_bytes()).status_code == 404


def test_keep_phone_video_env(
    client: TestClient, video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FACADEKIT_KEEP_PHONE_VIDEO", "1")
    monkeypatch.setattr(phone, "run_phone_calibration", _raise_unreadable)
    pid = _project(client)
    sid = _start(client, pid)
    job = _wait(client, _upload(client, pid, sid, video.read_bytes()).json()["job_id"])
    assert job["state"] == "failed"
    assert _leftovers(client, pid) == [f"{sid}.upload"]


def _raise_unreadable(*_a: Any, **_k: Any) -> Any:
    raise phone.FkError("PHONE_VIDEO_UNREADABLE", phone.MSG_UNREADABLE)


def test_failed_analysis_deletes_upload_and_keeps_session(client: TestClient, video: Path) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    data = video.read_bytes()
    truncated = data[: len(data) // 2]  # valid container magic, no index: ffmpeg cannot read it
    resp = _upload(client, pid, sid, truncated)
    assert resp.status_code == 202, resp.text
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] == "PHONE_VIDEO_UNREADABLE"
    assert _leftovers(client, pid) == []
    assert (_phone_dir(client, pid) / f"{sid}.json").is_file()  # the user can send another video
    assert client.get(f"{API}/projects/{pid}").json()["calibration_id"] is None


def test_unknown_and_expired_sessions(client: TestClient, video: Path) -> None:
    pid = _project(client)
    resp = _upload(client, pid, "phs_0123abcd", b"x")
    assert resp.status_code == 404
    sid = _start(client, pid)
    path = _phone_dir(client, pid) / f"{sid}.json"
    session = json.loads(path.read_text())
    session["created_at"] = "2020-01-01T00:00:00Z"
    path.write_text(json.dumps(session))
    resp = _upload(client, pid, sid, video.read_bytes())
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "This light show has expired. Start it again."
    assert not path.exists()
    assert _leftovers(client, pid) == []


def test_corrupt_session_file_is_not_found(client: TestClient) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    (_phone_dir(client, pid) / f"{sid}.json").write_text('{"id": "phs_00000000"')
    assert _upload(client, pid, sid, b"x").status_code == 404


@pytest.mark.parametrize(
    "sid",
    [
        "phs_0123ABCD",
        "phs_0123abc",
        "phs_0123abcde",
        "phs_0123abcg",
        "PHS_0123abcd",
        "..",
        "phs_0123abcd.json",
        "phs_0123abcd%00",
        "%2e%2e%2fphs_0123abcd",
        "phs_0123abcd%2f..",
        "..%5c..%5cphs_0123abcd",
    ],
)
def test_bad_session_ids_never_write(client: TestClient, sid: str, tmp_path: Path) -> None:
    pid = _project(client)
    _start(client, pid)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    resp = _upload(client, pid, sid, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    assert resp.status_code in (404, 405, 422), (sid, resp.status_code)  # 405: no route matched
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def test_unknown_project_upload(client: TestClient) -> None:
    assert _upload(client, "nope", "phs_0123abcd", b"x").status_code == 404


# --- upload: hostile bodies ---------------------------------------------------------------


def test_oversized_content_length_is_refused(client: TestClient) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    resp = client.post(
        f"{API}/projects/{pid}/phone-scans/{sid}/video",
        content=b"--b\r\n",
        headers={
            "content-type": "multipart/form-data; boundary=b",
            "content-length": str(5 * 1024**3),
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == {
        "code": "BAD_REQUEST",
        "message": "The video is larger than 4 GB.",
    }
    assert _leftovers(client, pid) == []


@pytest.mark.parametrize("length", ["abc", "-1", "1e9", "99999999999999999999999"])
def test_malformed_content_length(client: TestClient, length: str) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    resp = client.post(
        f"{API}/projects/{pid}/phone-scans/{sid}/video",
        content=b"--b--\r\n",
        headers={"content-type": "multipart/form-data; boundary=b", "content-length": length},
    )
    assert resp.status_code == 400


def _multipart(payload: bytes, name: str = "clip.mp4", boundary: str = "fkb") -> bytes:
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: video/mp4\r\n\r\n"
    ).encode()
    return head + payload + f"\r\n--{boundary}--\r\n".encode()


def test_lying_content_length_hits_the_stream_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phone, "MAX_VIDEO_BYTES", SMALL_CAP)
    pid = _project(client)
    sid = _start(client, pid)
    body = _multipart(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (3 * SMALL_CAP))
    resp = client.post(
        f"{API}/projects/{pid}/phone-scans/{sid}/video",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=fkb", "content-length": "1000"},
    )
    assert resp.status_code == 400  # over the cap, and more bytes than declared
    assert resp.json()["error"]["message"] in (
        "The video is larger than 4 GB.",
        routes_calibration.MSG_BAD_UPLOAD,
    )
    assert _leftovers(client, pid) == []


def test_chunked_upload_without_length_hits_the_stream_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phone, "MAX_VIDEO_BYTES", SMALL_CAP)
    pid = _project(client)
    sid = _start(client, pid)
    body = _multipart(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (3 * SMALL_CAP))
    sent = [0]

    def chunks() -> Iterator[bytes]:
        for i in range(0, len(body), 64 * 1024):
            sent[0] += 1
            yield body[i : i + 64 * 1024]

    resp = client.post(
        f"{API}/projects/{pid}/phone-scans/{sid}/video",
        content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=fkb"},
    )
    assert resp.status_code == 400
    assert _leftovers(client, pid) == []


def test_file_part_over_cap_is_refused_even_within_body_slack(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phone, "MAX_VIDEO_BYTES", SMALL_CAP)
    pid = _project(client)
    sid = _start(client, pid)
    resp = _upload(client, pid, sid, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * SMALL_CAP)
    assert resp.status_code == 400
    assert _leftovers(client, pid) == []


def test_low_disk_space_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    real = shutil.disk_usage

    def tiny(path: Any) -> Any:
        u = real(path)
        return type(u)(u.total, u.total - 10, 512 * 1024**2)

    monkeypatch.setattr(routes_calibration.shutil, "disk_usage", tiny)
    resp = _upload(client, pid, sid, b"\x00\x00\x00\x18ftypmp42")
    assert resp.status_code == 400
    assert "disk space" in resp.json()["error"]["message"]
    assert _leftovers(client, pid) == []


@pytest.mark.parametrize("name", ["clip.txt", "clip.m3u8", "clip", "clip.mp4.exe", "../x.sh"])
def test_wrong_file_name_is_refused(client: TestClient, name: str) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    resp = _upload(client, pid, sid, b"\x00\x00\x00\x18ftypmp42", name=name)
    assert resp.status_code == 400
    assert _leftovers(client, pid) == []


@pytest.mark.parametrize(
    "content",
    [
        b"hello, I am a text file\n" * 20,
        b"#EXTM3U\n#EXT-X-TARGETDURATION:10\nhttp://example.invalid/a.ts\n",
        b"ffconcat version 1.0\nfile '/etc/passwd'\n",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
        b"",
    ],
)
def test_wrong_container_is_refused_before_ffmpeg(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    called: list[Any] = []
    monkeypatch.setattr(phone, "run_phone_calibration", lambda *a, **k: called.append(a))
    pid = _project(client)
    sid = _start(client, pid)
    resp = _upload(client, pid, sid, content, name="clip.mp4")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PHONE_VIDEO_UNREADABLE"
    assert called == []
    assert _leftovers(client, pid) == []


@pytest.mark.parametrize(
    ("ctype", "body"),
    [
        ("application/octet-stream", b"\x00\x00\x00\x18ftypmp42"),
        ("multipart/form-data", b"--x\r\n"),  # no boundary
        ("multipart/form-data; boundary=fkb", b"garbage without boundaries"),
        ("multipart/form-data; boundary=fkb", _multipart(b"abc")[:-10]),  # cut short
        (
            "multipart/form-data; boundary=fkb",
            b'--fkb\r\nContent-Disposition: form-data; name="other"\r\n\r\nx\r\n--fkb--\r\n',
        ),
        (
            "multipart/form-data; boundary=fkb",
            b'--fkb\r\nContent-Disposition: form-data; name="file"\r\n\r\nx\r\n--fkb--\r\n',
        ),
        (
            "multipart/form-data; boundary=fkb",
            _multipart(b"\x00\x00\x00\x18ftypmp42")[: -len(b"--\r\n")]
            + b'\r\nContent-Disposition: form-data; name="file"; filename="b.mp4"\r\n\r\n'
            + b"\x00\x00\x00\x18ftypmp42\r\n--fkb--\r\n",
        ),  # two files
    ],
)
def test_malformed_multipart_is_refused(client: TestClient, ctype: str, body: bytes) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    resp = client.post(
        f"{API}/projects/{pid}/phone-scans/{sid}/video",
        content=body,
        headers={"content-type": ctype},
    )
    assert resp.status_code == 400, resp.text
    assert _leftovers(client, pid) == []


def test_abort_mid_upload_deletes_partial_file(client: TestClient, video: Path) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    body = _multipart(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (3 * 1024 * 1024))
    partial_seen: list[int] = []
    upload = _phone_dir(client, pid) / f"{sid}.upload"
    messages: list[dict[str, Any]] = [
        {"type": "http.request", "body": body[: 2 * 1024 * 1024 + 4096], "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if len(messages) == 1:
            parts = list(_phone_dir(client, pid).glob("*.part"))
            partial_seen.append(parts[0].stat().st_size if parts else -1)
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"{API}/projects/{pid}/phone-scans/{sid}/video",
        "raw_path": f"{API}/projects/{pid}/phone-scans/{sid}/video".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"multipart/form-data; boundary=fkb"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    anyio.run(_app(client), scope, receive, send)
    assert partial_seen and partial_seen[0] >= 1024 * 1024  # bytes had reached the disk
    assert not upload.exists()
    assert _leftovers(client, pid) == []
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 400
    # The slot is free again: a complete upload is accepted.
    resp = _upload(client, pid, sid, video.read_bytes())
    assert resp.status_code == 202
    assert _wait(client, resp.json()["job_id"])["state"] == "succeeded"


def test_one_analysis_at_a_time_on_the_server(
    client: TestClient, video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()
    real = phone.run_phone_calibration

    def slow(*a: Any, **k: Any) -> Any:
        gate.wait(30)
        return real(*a, **k)

    monkeypatch.setattr(phone, "run_phone_calibration", slow)
    pid = _project(client)
    other = _project(client)
    sid = _start(client, pid)
    sid_other = _start(client, other)
    data = video.read_bytes()
    first = _upload(client, pid, sid, data)
    assert first.status_code == 202
    upload = _phone_dir(client, pid) / f"{sid}.upload"
    before = upload.read_bytes()
    # the same session again: refused, and the file being analysed is not overwritten
    same = _upload(client, pid, sid, b"\x00\x00\x00\x18ftypmp42")
    assert same.status_code == 409
    assert same.json()["error"]["code"] == "BAD_REQUEST"
    assert upload.read_bytes() == before
    # another project: also refused, one analysis at a time on the whole server
    assert _upload(client, other, sid_other, data).status_code == 409
    assert _leftovers(client, other) == []
    gate.set()
    assert _wait(client, first.json()["job_id"])["state"] == "succeeded"
    again = _upload(client, other, sid_other, data)
    assert again.status_code == 202
    assert _wait(client, again.json()["job_id"])["state"] == "succeeded"


def test_symlinked_phone_folder_is_refused(client: TestClient, tmp_path: Path) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    folder = _phone_dir(client, pid)
    shutil.rmtree(folder)
    outside = tmp_path / "outside"
    outside.mkdir()
    folder.symlink_to(outside, target_is_directory=True)
    assert _upload(client, pid, sid, b"\x00\x00\x00\x18ftypmp42").status_code == 422
    assert client.post(f"{API}/projects/{pid}/phone-scans", json={}).status_code == 422
    assert list(outside.iterdir()) == []


def test_symlinked_session_file_is_not_followed(client: TestClient, tmp_path: Path) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    path = _phone_dir(client, pid) / f"{sid}.json"
    real = tmp_path / "elsewhere.json"
    real.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(real)
    assert _upload(client, pid, sid, b"\x00\x00\x00\x18ftypmp42").status_code == 404


def test_stale_files_are_swept(client: TestClient) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    folder = _phone_dir(client, pid)
    old = time.time() - 25 * 3600
    stale = [folder / "phs_0000aaaa.json", folder / "phs_0000aaaa.upload", folder / "x.part"]
    for p in stale:
        p.write_bytes(b"old")
        os.utime(p, (old, old))
    _start(client, pid)
    assert not any(p.exists() for p in stale)
    assert (folder / f"{sid}.json").is_file()


def test_cancelled_queued_analysis_frees_the_slot(
    client: TestClient, video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()
    monkeypatch.setattr(phone, "play_light_show", lambda *_a, **_k: gate.wait(30))
    pid = _project(client)
    show = client.post(f"{API}/projects/{pid}/phone-scans", json={}).json()  # occupies the runner
    sid = show["session_id"]
    queued = _upload(client, pid, sid, video.read_bytes()).json()["job_id"]
    client.post(f"{API}/jobs/{queued}/cancel")
    gate.set()
    _wait(client, show["job_id"])
    # The cancelled job never ran, so its upload is still there until the next upload sweeps it.
    assert _leftovers(client, pid) == [f"{sid}.upload"]
    bad = _upload(client, pid, sid, b"not a video", name="clip.mp4")
    assert bad.status_code == 422
    assert _leftovers(client, pid) == []
    resp = _upload(client, pid, sid, video.read_bytes())
    assert resp.status_code == 202
    assert _wait(client, resp.json()["job_id"])["state"] == "succeeded"


# --- LAN mode: existing guards are unchanged ------------------------------------------------


@pytest.fixture
def lan(tmp_path: Path) -> Iterator[tuple[TestClient, Addr]]:
    addr = Addr()
    app = create_app(
        data_dir=tmp_path, sim=True, lan=True, pairing_code=CODE, client_addr_override=addr
    )
    with TestClient(app) as c:
        yield c, addr


def test_laptop_works_in_lan_mode(lan: tuple[TestClient, Addr], video: Path) -> None:
    c, _ = lan
    pid = _project(c)
    sid = _start(c, pid)
    resp = _upload(c, pid, sid, video.read_bytes())
    assert resp.status_code == 202
    assert _wait(c, resp.json()["job_id"])["state"] == "succeeded"


def test_lan_clients_cannot_use_phone_routes_without_owner_allowlist(
    lan: tuple[TestClient, Addr],
) -> None:
    """The remote allowlist is not widened by T-208 (owner decision pending, see the PR)."""
    c, addr = lan
    pid = _project(c)
    sid = _start(c, pid)
    job_id = c.post(f"{API}/projects/{pid}/phone-scans", json={}).json()["job_id"]
    addr.host = PHONE_HOST
    # unpaired
    assert c.post(f"{API}/projects/{pid}/phone-scans", json={}).status_code == 403
    assert _upload(c, pid, sid, b"\x00\x00\x00\x18ftypmp42").status_code == 403
    # paired: still only the T-117 routes
    assert c.post(f"{API}/remote/pair", json={"code": CODE}).status_code == 200
    assert c.get(f"{API}/projects/{pid}").status_code == 200
    assert c.post(f"{API}/projects/{pid}/phone-scans", json={}).status_code == 403
    assert _upload(c, pid, sid, b"\x00\x00\x00\x18ftypmp42").status_code == 403
    assert c.get(f"{API}/jobs/{job_id}").status_code == 403
    assert c.post(f"{API}/projects/{pid}/renders", json={}).status_code == 403
    assert _leftovers(c, pid) == []


def test_cross_site_upload_is_blocked(client: TestClient) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    resp = client.post(
        f"{API}/projects/{pid}/phone-scans/{sid}/video",
        files={"file": ("a.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
        headers={"origin": "http://evil.example"},
    )
    assert resp.status_code == 403
    resp = client.post(
        f"{API}/projects/{pid}/phone-scans",
        json={},
        headers={"origin": "http://evil.example"},
    )
    assert resp.status_code == 403
    assert _leftovers(client, pid) == []


# --- security review 2: stalls, leftovers, TE+CL, early name check, job errors -------------


def _asgi(
    c: TestClient, path: str, headers: list[tuple[bytes, bytes]], receive: Any
) -> tuple[int, dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    anyio.run(_app(c), scope, receive, send)
    status = next(m for m in sent if m["type"] == "http.response.start")["status"]
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return int(status), json.loads(body)


_MP = [(b"content-type", b"multipart/form-data; boundary=fkb")]


def _head(name: str = "clip.mp4") -> bytes:
    return (
        f'--fkb\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n\r\n'
    ).encode() + b"\x00\x00\x00\x18ftypmp42"


def _assert_slot_free(c: TestClient, pid: str, sid: str) -> None:
    # A wrong container is refused with 422 after taking the slot; 409 would mean it is held.
    assert _upload(c, pid, sid, b"not a video", name="clip.mp4").status_code == 422


def test_stalled_upload_is_dropped_and_frees_the_slot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes_calibration, "UPLOAD_IDLE_TIMEOUT_S", 0.3)
    pid = _project(client)
    sid = _start(client, pid)
    calls = [0]

    async def receive() -> dict[str, Any]:
        calls[0] += 1
        if calls[0] == 1:
            return {"type": "http.request", "body": _head() + b"\x00" * 4096, "more_body": True}
        await anyio.sleep(30)  # the client goes silent
        return {"type": "http.disconnect"}

    t0 = time.monotonic()
    status, body = _asgi(client, f"{API}/projects/{pid}/phone-scans/{sid}/video", _MP, receive)
    assert time.monotonic() - t0 < 10
    assert status == 400
    assert body["error"]["message"] == routes_calibration.MSG_UPLOAD_STALLED
    assert _leftovers(client, pid) == []
    _assert_slot_free(client, pid, sid)


def test_slow_drip_upload_hits_the_overall_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes_calibration, "UPLOAD_MAX_S", 0.5)
    pid = _project(client)
    sid = _start(client, pid)
    first = [True]

    async def receive() -> dict[str, Any]:
        if first[0]:
            first[0] = False
            return {"type": "http.request", "body": _head(), "more_body": True}
        await anyio.sleep(0.05)  # never idle long enough for the idle timeout
        return {"type": "http.request", "body": b"\x00", "more_body": True}

    t0 = time.monotonic()
    status, body = _asgi(client, f"{API}/projects/{pid}/phone-scans/{sid}/video", _MP, receive)
    assert time.monotonic() - t0 < 10
    assert status == 400
    assert body["error"]["message"] == routes_calibration.MSG_UPLOAD_STALLED
    assert _leftovers(client, pid) == []
    _assert_slot_free(client, pid, sid)


def test_transfer_encoding_with_content_length_does_not_skip_the_disk_check(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    real = shutil.disk_usage

    def two_gb(path: Any) -> Any:
        u = real(path)
        return type(u)(u.total, u.used, 2 * 1024**3)

    monkeypatch.setattr(routes_calibration.shutil, "disk_usage", two_gb)
    body = _multipart(b"\x00\x00\x00\x18ftypmp42")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [*_MP, (b"content-length", b"10"), (b"transfer-encoding", b"chunked")]
    status, out = _asgi(client, f"{API}/projects/{pid}/phone-scans/{sid}/video", headers, receive)
    assert status == 400  # the 10-byte length is ignored, so 4 GB + 1 GB is required
    assert "disk space" in out["error"]["message"]
    assert _leftovers(client, pid) == []


def test_more_bytes_than_declared_is_refused(client: TestClient) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    body = _multipart(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [*_MP, (b"content-length", b"100")]
    status, _out = _asgi(client, f"{API}/projects/{pid}/phone-scans/{sid}/video", headers, receive)
    assert status == 400
    assert _leftovers(client, pid) == []


def test_bad_extension_fails_before_the_body_is_streamed(client: TestClient) -> None:
    pid = _project(client)
    sid = _start(client, pid)
    calls = [0]

    async def receive() -> dict[str, Any]:
        calls[0] += 1
        if calls[0] == 1:
            return {"type": "http.request", "body": _head("payload.sh"), "more_body": True}
        return {"type": "http.request", "body": b"\x00" * 65536, "more_body": True}

    status, _out = _asgi(client, f"{API}/projects/{pid}/phone-scans/{sid}/video", _MP, receive)
    assert status == 400
    assert calls[0] == 1  # refused at the part headers, not after reading the body
    assert _leftovers(client, pid) == []


def test_unexpected_job_errors_do_not_leak_details(
    client: TestClient, video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "/Users/someone/private/path.mp4"

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"cannot open {secret}")

    monkeypatch.setattr(phone, "run_phone_calibration", boom)
    pid = _project(client)
    sid = _start(client, pid)
    job = _wait(client, _upload(client, pid, sid, video.read_bytes()).json()["job_id"])
    assert job["state"] == "failed"
    assert job["error"] == {
        "code": "INTERNAL",
        "message": routes_calibration.MSG_PHONE_JOB_FAILED,
    }
    assert _leftovers(client, pid) == []

    monkeypatch.setattr(phone, "play_light_show", boom)
    resp = client.post(f"{API}/projects/{pid}/phone-scans", json={})
    job = _wait(client, resp.json()["job_id"])
    assert job["error"] == {"code": "INTERNAL", "message": routes_calibration.MSG_SHOW_JOB_FAILED}
    assert secret not in json.dumps(job)
