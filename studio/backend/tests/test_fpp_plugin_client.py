"""FacadeKit's side of the FPP plugin, against the fake FPP with and without the fake plugin."""

from __future__ import annotations

import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from facadekit.cli import format_plugin_summary, main
from facadekit.fpp.contract import PASS, SKIP, run_contract
from facadekit.fpp.fake import create_fake_fpp
from facadekit.fpp.plugin_client import (
    TOKEN_ENV,
    FppPluginClient,
    PluginError,
    plugin_summary,
    store_token,
    stored_token,
)
from facadekit.fpp.plugin_fake import FakePluginState
from facadekit.fpp.publish import publish_render
from tests.conftest import FakeFpp, _free_port

TOKEN = "fkp_" + "ab12" * 12
API = "/api/v1"


@pytest.fixture
def plugin_fpp(tmp_path: Path) -> Iterator[tuple[FakeFpp, FakePluginState]]:
    state = FakePluginState(token=TOKEN)
    state_dir = tmp_path / "fpp_plugin"
    app = create_fake_fpp(state_dir, plugin=state)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    try:
        yield FakeFpp(host=f"127.0.0.1:{port}", state_dir=state_dir, app=app), state
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def mp4(tmp_path: Path) -> Path:
    path = tmp_path / "render.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def test_absent_plugin_is_reported_not_raised(fake_fpp: FakeFpp) -> None:
    summary = plugin_summary(fake_fpp.host)
    assert summary["present"] is False and summary["reason"] == "absent"
    with FppPluginClient(fake_fpp.host, token=TOKEN) as client, pytest.raises(PluginError) as exc:
        client.health()
    assert exc.value.reason == "absent"


def test_unreachable(tmp_path: Path) -> None:
    summary = plugin_summary(f"127.0.0.1:{_free_port()}")
    assert summary["present"] is False and summary["reason"] == "unreachable"


def test_summary_paired_and_unpaired(plugin_fpp: tuple[FakeFpp, FakePluginState]) -> None:
    fpp, state = plugin_fpp
    unpaired = plugin_summary(fpp.host)
    assert unpaired["present"] and not unpaired["paired"] and unpaired["reason"] == "not_paired"
    assert "health" not in state.calls
    paired = plugin_summary(fpp.host, token=TOKEN)
    assert paired["paired"] and paired["health"]["summary"]["total"] == 2
    wrong = plugin_summary(fpp.host, token="fkp_wrongwrong")
    assert not wrong["paired"] and wrong["reason"] == "bad_token"
    state.token = None
    assert plugin_summary(fpp.host, token=TOKEN)["reason"] == "not_paired"


def test_token_store(plugin_fpp: tuple[FakeFpp, FakePluginState], tmp_path: Path) -> None:
    fpp, _ = plugin_fpp
    store_token(fpp.host, TOKEN)
    assert stored_token(fpp.host) == TOKEN
    assert stored_token("http://" + fpp.host + "/") == TOKEN
    path = stored_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert plugin_summary(fpp.host)["paired"]
    store_token(fpp.host, None)
    assert stored_token(fpp.host) is None


def stored_path() -> Path:
    from facadekit.fpp.plugin_client import _token_path

    return _token_path()


def test_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    assert stored_token("anything") == TOKEN


def test_power_and_shows(plugin_fpp: tuple[FakeFpp, FakePluginState]) -> None:
    fpp, state = plugin_fpp
    with FppPluginClient(fpp.host, token=TOKEN) as client:
        answer = client.power("on", wait=True)
        assert answer["ok"] and all(p["power"] == "on" for p in state.projectors)
        client.register_show({"playlist": "FacadeKit - A", "video": "fk_a.mp4"})
        assert [s["playlist"] for s in client.shows()] == ["FacadeKit - A"]
        with pytest.raises(PluginError):
            client.register_show({"playlist": "a/b", "video": "fk_a.mp4"})
        client.delete_show("FacadeKit - A")
        assert client.shows() == []
        with pytest.raises(PluginError):
            client.power("on", timeout_s=5000)


def test_publish_registers_show_and_powers_on(
    plugin_fpp: tuple[FakeFpp, FakePluginState], mp4: Path
) -> None:
    fpp, state = plugin_fpp
    store_token(fpp.host, TOKEN)
    result = publish_render(fpp.host, mp4, media_name="show.mp4", playlist_name="FacadeKit - S",
                            start=True, manifest={"project": "S"}, power_on=True)  # fmt: skip
    assert result["started"] and result["plugin"]["status"] == "ok"
    assert state.shows[0]["playlist"] == "FacadeKit - S"
    assert state.shows[0]["video"] == "fk_show.mp4"
    assert all(p["power"] == "on" for p in state.projectors)
    assert state.calls.index("register") < state.calls.index("power on")


def test_publish_without_plugin_still_works(fake_fpp: FakeFpp, mp4: Path) -> None:
    result = publish_render(fake_fpp.host, mp4, media_name="x.mp4", playlist_name="P",
                            start=True, manifest={"project": "P"}, power_on=True)  # fmt: skip
    assert result["started"] and result["plugin"]["status"] == "absent"
    assert fake_fpp.app.state.playing == "P"


def test_publish_with_unreachable_projector_reports(
    plugin_fpp: tuple[FakeFpp, FakePluginState], mp4: Path
) -> None:
    fpp, state = plugin_fpp
    store_token(fpp.host, TOKEN)
    state.unreachable = {"p2"}
    result = publish_render(fpp.host, mp4, media_name="x.mp4", playlist_name="P", start=False,
                            manifest={}, power_on=True)  # fmt: skip
    assert result["plugin"]["status"] == "partial"
    assert any("Right" in p for p in result["plugin"]["problems"])


def test_api_routes(client: TestClient, plugin_fpp: tuple[FakeFpp, FakePluginState]) -> None:
    fpp, state = plugin_fpp
    assert client.get(f"{API}/fpp/plugin", params={"host": fpp.host}).json()["paired"] is False
    bad = client.post(f"{API}/fpp/plugin/pair", json={"host": fpp.host, "token": "x"})
    assert bad.status_code == 422
    wrong = client.post(f"{API}/fpp/plugin/pair", json={"host": fpp.host, "token": "fkp_nope1234"})
    assert wrong.json()["paired"] is False
    paired = client.post(f"{API}/fpp/plugin/pair", json={"host": fpp.host, "token": TOKEN}).json()
    assert paired["paired"] and paired["token_stored"]
    assert TOKEN not in str(paired)
    on = client.post(f"{API}/fpp/plugin/projectors",
                     json={"host": fpp.host, "action": "on", "wait": True})  # fmt: skip
    assert on.json()["ok"] and state.projectors[0]["power"] == "on"
    assert client.post(f"{API}/fpp/plugin/projectors",
                       json={"host": fpp.host, "action": "x"}).status_code == 422  # fmt: skip
    gone = client.delete(f"{API}/fpp/plugin/pair", params={"host": fpp.host}).json()
    assert gone["paired"] is False


def test_api_publish_sends_manifest(
    client: TestClient, tmp_path: Path, plugin_fpp: tuple[FakeFpp, FakePluginState]
) -> None:
    from tests.test_api_render_fpp import _make_project, _render, _wait

    fpp, state = plugin_fpp
    store_token(fpp.host, TOKEN)
    pid = _make_project(client, tmp_path)
    show = _render(client, pid)
    resp = client.post(f"{API}/projects/{pid}/publish",
                       json={"host": fpp.host, "render_id": show["render_id"], "start": False,
                             "power_on": True})  # fmt: skip
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    assert job["result"]["plugin"]["registered"] is True
    manifest = state.shows[0]
    assert manifest["project"] == "Show" and manifest["project_id"] == pid
    assert manifest["video"] == f"fk_{pid}.mp4" and manifest["calibration_age_days"] is None


def test_cli_plugin_status(
    plugin_fpp: tuple[FakeFpp, FakePluginState], capsys: pytest.CaptureFixture[str]
) -> None:
    fpp, _ = plugin_fpp
    assert main(["fpp", "plugin-status", fpp.host]) == 0  # present, no token stored
    assert "not paired" in capsys.readouterr().out
    store_token(fpp.host, TOKEN)
    assert main(["fpp", "plugin-status", fpp.host]) == 0
    out = capsys.readouterr().out
    assert "healthy" in out and "projector Left: standby" in out
    assert "shows published by FacadeKit: 0" in out


def test_cli_plugin_status_absent(fake_fpp: FakeFpp, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["fpp", "plugin-status", fake_fpp.host]) == 1
    assert "plain FPP API" in capsys.readouterr().out
    assert "not available" in format_plugin_summary("h", plugin_summary(fake_fpp.host))


def test_contract_checks_plugin(
    plugin_fpp: tuple[FakeFpp, FakePluginState], fake_fpp: FakeFpp, tmp_path: Path
) -> None:
    absent = {s.name: s for s in run_contract(fake_fpp.host, tmp_path / "a")}
    assert absent["plugin"].verdict == SKIP
    fpp, _ = plugin_fpp
    steps = {s.name: s for s in run_contract(fpp.host, tmp_path / "b")}
    assert steps["plugin"].verdict == PASS
    assert steps["plugin health"].verdict == SKIP
    store_token(fpp.host, TOKEN)
    steps = {s.name: s for s in run_contract(fpp.host, tmp_path / "c")}
    assert steps["plugin health"].verdict == PASS
    saved = (tmp_path / "c" / "plugin_health.json").read_text("utf-8")
    assert "192.168.50.10" not in saved and TOKEN not in saved
    assert not any(c.startswith("power") or c == "register" for c in plugin_fpp[1].calls)
