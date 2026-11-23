"""`facadekit spike fpp-contract` (spike S-1 recorder) against the fake FPP and a mismatched one."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from facadekit.cli import main
from facadekit.fpp.client import PASSWORD_ENV, FppClient
from facadekit.fpp.contract import FAIL, PASS, SKIP, WARN, Redactor, Step, run_contract
from facadekit.fpp.fake import create_fake_fpp
from facadekit.fpp.publish import media_entry
from tests.conftest import FakeFpp, serve_app


def _by_name(steps: list[Step]) -> dict[str, Step]:
    return {step.name: step for step in steps}


def _seed(fpp: FakeFpp) -> None:
    (fpp.state_dir / "videos").mkdir(parents=True, exist_ok=True)
    (fpp.state_dir / "videos" / "Smith Family Intro.mp4").write_bytes(b"x" * 10)
    client = FppClient(fpp.host)
    entry = media_entry("Smith Family Intro.mp4") | {"fileMode": "single", "streamSlot": 1}
    client.put_playlist("Smith Christmas", [entry])
    client.close()


def _fake_clip(path: Path) -> None:
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)


@pytest.fixture(autouse=True)
def _no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PASSWORD_ENV, raising=False)


def test_read_only_run_against_fake(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    _seed(fake_fpp)
    before = len(fake_fpp.app.state.requests)
    out = tmp_path / "out"
    steps = _by_name(run_contract(fake_fpp.host, out))
    assert all(s.verdict != FAIL for s in steps.values()), steps
    for name in ("reachable", "password", "version", "system info", "videos folder",
                 "sequences folder", "playlists", "playlist entry shape", "status"):  # fmt: skip
        assert steps[name].verdict == PASS, (name, steps[name])
    assert steps["API help"].verdict == PASS
    assert steps["media list"].verdict == PASS
    assert steps["write test"].verdict == SKIP
    methods = {r.split()[0] for r in fake_fpp.app.state.requests[before:]}
    assert methods == {"GET"}, "the default run must only read"
    for name in ("fppd_version", "system_info", "files_videos", "playlists", "playlist_sample",
                 "fppd_status", "report"):  # fmt: skip
        assert (out / f"{name}.json").is_file(), name
    assert json.loads((out / "fppd_version.json").read_text())["majorVersion"] == "10"


def test_fixtures_are_redacted(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    _seed(fake_fpp)
    out = tmp_path / "out"
    run_contract(fake_fpp.host, out)
    text = "\n".join(p.read_text() for p in out.iterdir())
    for secret in ("127.0.0.1", "192.168.1.50", "FakeFPP", "M1-FAKE0000", "Smith"):
        assert secret not in text, secret
    sample = json.loads((out / "playlist_sample.json").read_text())
    entry = sample["mainPlaylist"][0]
    assert entry["mediaName"].startswith("<name-") and entry["mediaName"].endswith(".mp4")
    assert entry["videoOut"] == "--Default--"


def test_write_test_needs_yes(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    prompts: list[str] = []

    def say_no(prompt: str) -> str:
        prompts.append(prompt)
        return "y"

    steps = _by_name(run_contract(fake_fpp.host, tmp_path / "o", write_test=True, confirm=say_no))
    assert steps["write test"].verdict == SKIP
    assert "WRITE TEST" in prompts[0] and "Type yes" in prompts[0]
    assert not any(r.split()[0] != "GET" for r in fake_fpp.app.state.requests)


def test_write_test_uploads_reads_back_and_cleans_up(fake_fpp: FakeFpp, tmp_path: Path) -> None:
    steps = _by_name(
        run_contract(
            fake_fpp.host,
            tmp_path / "o",
            write_test=True,
            confirm=lambda _p: "yes",
            make_clip=_fake_clip,
        )
    )
    assert steps["write: upload"].verdict == PASS
    assert steps["write: playlist"].verdict == PASS
    assert steps["write: clean up"].verdict == PASS
    assert list((fake_fpp.state_dir / "videos").iterdir()) == []
    assert FppClient(fake_fpp.host).list_playlists() == []
    assert fake_fpp.app.state.playing is None
    assert not any("/start" in r for r in fake_fpp.app.state.requests)


def test_unreachable(tmp_path: Path) -> None:
    steps = run_contract("127.0.0.1:9", tmp_path / "o", timeout=1)
    assert steps[0].name == "reachable" and steps[0].verdict == FAIL
    report = (tmp_path / "o" / "report.txt").read_text()
    assert "127.0.0.1" not in report


def test_password_needed_and_given(tmp_path: Path) -> None:
    with serve_app(create_fake_fpp(tmp_path / "pw", password="s3cret")) as host:
        steps = _by_name(run_contract(host, tmp_path / "a"))
        assert steps["password"].verdict == FAIL
        assert PASSWORD_ENV in steps["password"].detail
        steps = _by_name(run_contract(host, tmp_path / "b", password="s3cret"))
        assert steps["password"].verdict == PASS
        assert steps["version"].verdict == PASS
    assert "s3cret" not in "".join(p.read_text() for p in (tmp_path / "b").iterdir())


def _mismatched_fpp() -> FastAPI:
    """Answers FacadeKit's GETs with shapes that differ from the recorded contract."""
    app = FastAPI()

    @app.get("/api/fppd/version")
    async def version() -> JSONResponse:
        return JSONResponse({"version": "10.4", "majorVersion": 10.0, "minorVersion": "4"})

    @app.get("/api/system/info")
    async def info() -> JSONResponse:
        return JSONResponse(["not", "an", "object"])

    @app.get("/api/files/{key}")
    async def files(key: str) -> JSONResponse:
        if key == "sequences":
            return JSONResponse({"status": "Invalid Directory"})
        return JSONResponse({"status": "ok", "files": [{"filename": "a.mp4", "size": 3}]})

    @app.get("/api/media")
    async def media() -> PlainTextResponse:
        return PlainTextResponse("a.mp4")

    @app.get("/api/playlists")
    async def playlists() -> JSONResponse:
        return JSONResponse({"names": []})

    @app.get("/api/fppd/status")
    async def status() -> JSONResponse:
        return JSONResponse({"state": "idle", "current_playlist": {"playlist": 3}})

    return app


def test_mismatched_shapes_are_reported(tmp_path: Path) -> None:
    with serve_app(_mismatched_fpp()) as host:
        steps = _by_name(run_contract(host, tmp_path / "o"))
    assert steps["version"].verdict == WARN
    assert any("majorVersion" in d for d in steps["version"].differences)
    assert steps["system info"].verdict == FAIL
    assert steps["videos folder"].verdict == WARN
    assert any("missing key 'name'" in d for d in steps["videos folder"].differences)
    assert steps["sequences folder"].verdict == FAIL
    assert "no folder 'sequences'" in steps["sequences folder"].detail
    assert steps["media list"].verdict == WARN  # not used by FacadeKit: never a FAIL
    assert steps["playlists"].verdict == FAIL
    assert steps["status"].verdict == FAIL
    assert any("status_name" in d for d in steps["status"].differences)
    assert steps["API help"].verdict == WARN


def test_redactor() -> None:
    redact = Redactor("fpp.local")
    data = {
        "HostName": "garage-pi",
        "IPs": ["192.168.1.50"],
        "wifi": {"ssid": "HomeNet"},
        "note": "eth0 b8:27:eb:12:34:56 at 10.0.0.7 via fpp.local",
        "mainPlaylist": [{"mediaName": "Jones.mp4"}, {"mediaName": "Jones.mp4"}],
        "uuid": "M1-123",
        "password": "x",
    }
    out = redact(data)
    assert out["HostName"] == out["IPs"] == out["wifi"] == out["uuid"] == "<redacted>"
    assert out["password"] == "<redacted>"
    assert out["note"] == "eth0 <mac> at <ip> via <host>"
    assert out["mainPlaylist"][0]["mediaName"] == out["mainPlaylist"][1]["mediaName"]
    assert out["mainPlaylist"][0]["mediaName"] == "<name-1>.mp4"
    assert redact({"mediaName": "fk_show.mp4"})["mediaName"] == "fk_show.mp4"


def test_cli(fake_fpp: FakeFpp, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = tmp_path / "s1.zip"
    code = main(
        ["spike", "fpp-contract", fake_fpp.host, "--out", str(tmp_path / "o"),
         "--bundle", str(bundle)]
    )  # fmt: skip
    printed = capsys.readouterr().out
    assert code == 0
    assert "reachable" in printed and "PASS" in printed
    with zipfile.ZipFile(bundle) as archive:
        assert "fpp-contract/report.txt" in archive.namelist()
    assert main(["spike", "fpp-contract", "127.0.0.1:9", "--out", str(tmp_path / "x"),
                 "--timeout", "1"]) == 1  # fmt: skip


def test_cli_write_test_without_terminal_does_not_write(
    fake_fpp: FakeFpp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    code = main(["spike", "fpp-contract", fake_fpp.host, "--out", str(tmp_path / "o"),
                 "--write-test"])  # fmt: skip
    assert code == 0
    assert not any(r.split()[0] != "GET" for r in fake_fpp.app.state.requests)
