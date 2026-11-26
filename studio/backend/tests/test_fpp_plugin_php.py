"""The plugin's PHP run for real: ``php -l`` on every file, the PJLink client and the power
sequences against the fake PJLink projector, and the REST API through ``php -S``.

Skipped when ``php`` (php-cli 8) is not installed; set FK_REQUIRE_PHP=1 to make that a
failure. On macOS: ``brew install php``. Nothing here talks to a real projector or FPP: the
projectors are pjlink_fake servers on 127.0.0.1 and FPP's API is a closed local port.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from facadekit.fpp.pjlink_fake import POWER_COOLING, FakePjlinkServer, FakeProjector

PLUGIN = Path(__file__).resolve().parents[3] / "integrations" / "fpp-plugin"
CLI = PLUGIN / "lib" / "cli.php"
PHP = shutil.which("php")

pytestmark = pytest.mark.skipif(
    PHP is None and os.environ.get("FK_REQUIRE_PHP") != "1",
    reason="php is not installed (brew install php); PHP covered by test_fpp_plugin_static.py",
)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    data = tmp_path / "plugindata"
    data.mkdir()
    return {
        "PATH": os.environ.get("PATH", ""),
        "FK_PLUGIN_DATA_DIR": str(data),
        "FK_PLUGIN_LOG": str(tmp_path / "plugin.log"),
        "FK_FPP_API": f"http://127.0.0.1:{_closed_port()}",
        "FK_SLEEP_SCALE": "0.1",
        "FK_PHP": PHP or "php",
    }


def write_config(env: dict[str, str], projectors: list[dict[str, Any]], **settings: Any) -> None:
    config = {"connect_timeout_s": 1, "io_timeout_s": 1, **settings, "projectors": projectors}
    Path(env["FK_PLUGIN_DATA_DIR"], "config.json").write_text(json.dumps(config), "utf-8")


def proj(server: FakePjlinkServer, pid: str = "p1", **extra: Any) -> dict[str, Any]:
    return {"id": pid, "name": pid.upper(), "host": "127.0.0.1", "port": server.port, **extra}


def cli(env: dict[str, str], *args: str, timeout: float = 60) -> Any:
    out = subprocess.run([PHP or "php", str(CLI), *args], env=env, capture_output=True,
                         text=True, timeout=timeout)  # fmt: skip
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def log_text(env: dict[str, str]) -> str:
    path = Path(env["FK_PLUGIN_LOG"])
    return path.read_text("utf-8") if path.exists() else ""


@pytest.fixture
def laser() -> Iterator[FakePjlinkServer]:
    with FakePjlinkServer(FakeProjector()) as server:
        yield server


def test_php_lint() -> None:
    for path in sorted(PLUGIN.rglob("*")):
        if path.suffix in (".php", ".inc"):
            out = subprocess.run([PHP or "php", "-l", str(path)], capture_output=True, text=True)
            assert out.returncode == 0, out.stdout + out.stderr


def test_on_and_off_instant(env: dict[str, str], laser: FakePjlinkServer) -> None:
    write_config(env, [proj(laser, input="32")])
    results = cli(env, "on", "true")
    assert results[0]["ok"] and results[0]["state"] == "on"
    assert laser.projector.power() == "1" and laser.projector.input == "32"
    results = cli(env, "off", "true")
    assert results[0]["ok"] and laser.projector.power() == "0"


def test_status_snapshot(env: dict[str, str], laser: FakePjlinkServer) -> None:
    write_config(env, [proj(laser)])
    snap = cli(env, "status")[0]
    assert snap["ok"] and snap["power"] == "standby"
    assert snap["manufacturer"] == "Panasonic" and snap["model"] == "PT-RZ570"
    assert snap["lamps"] == [{"hours": 1234, "on": False}]
    assert snap["errors"]["fan"] == "ok"
    state = json.loads(Path(env["FK_PLUGIN_DATA_DIR"], "state.json").read_text("utf-8"))
    assert state["projectors"]["p1"]["power"] == "standby"


def test_password_auth_and_redaction(env: dict[str, str]) -> None:
    with FakePjlinkServer(FakeProjector(password="test1234")) as good:
        write_config(env, [proj(good, password="test1234")])
        assert cli(env, "on", "true")[0]["ok"]
        write_config(env, [proj(good, password="wrong999")])
        result = cli(env, "on", "true")[0]
        assert not result["ok"] and "password" in result["message"]
        write_config(env, [proj(good)])
        assert "asks for a PJLink password" in cli(env, "status")[0]["error"]
    assert "test1234" not in log_text(env) and "wrong999" not in log_text(env)


def test_never_powers_on_while_cooling(env: dict[str, str]) -> None:
    fake = FakeProjector(cooldown_s=0.0)
    fake.set_power(POWER_COOLING, 30)
    with FakePjlinkServer(fake) as server:
        write_config(env, [proj(server, cooldown_timeout_s=1)])
        result = cli(env, "on", "true")[0]
        assert not result["ok"] and result["state"] == "cooling"
        assert fake.power_on_while_cooling == 0
        assert not any(c == "POWR 1" for c in fake.commands)


def test_waits_for_reported_cooling_then_powers_on(env: dict[str, str]) -> None:
    fake = FakeProjector()
    fake.set_power(POWER_COOLING, 0.5)
    with FakePjlinkServer(fake) as server:
        write_config(env, [proj(server, cooldown_timeout_s=10)])
        assert cli(env, "on", "true")[0]["state"] == "on"
        assert fake.power_on_while_cooling == 0


def test_lamp_projector_warmup_then_input(env: dict[str, str]) -> None:
    fake = FakeProjector(warmup_s=0.8)
    with FakePjlinkServer(fake) as server:
        write_config(env, [proj(server, input="32", ready_timeout_s=10)])
        result = cli(env, "on", "true")[0]
        assert result["ok"] and result["state"] == "on" and fake.input == "32"


def test_parallel_with_one_dead_projector(env: dict[str, str], laser: FakePjlinkServer) -> None:
    dead = {"id": "p2", "name": "P2", "host": "127.0.0.1", "port": _closed_port()}
    write_config(env, [proj(laser), dead])
    start = time.monotonic()
    results = {r["id"]: r for r in cli(env, "on", "true")}
    assert results["p1"]["ok"] and not results["p2"]["ok"]
    assert time.monotonic() - start < 20


def test_silent_projector_times_out(env: dict[str, str]) -> None:
    with FakePjlinkServer(FakeProjector(quirks={"silent"})) as server:
        write_config(env, [proj(server)])
        start = time.monotonic()
        snap = cli(env, "status")[0]
        assert not snap["ok"] and snap["error_kind"] == "timeout"
        assert time.monotonic() - start < 10


@pytest.mark.parametrize(
    "quirk", ["close-after-reply", "split-writes", "crlf", "nul-padding", "lowercase-random"]
)
def test_quirks(env: dict[str, str], quirk: str) -> None:
    with FakePjlinkServer(FakeProjector(password="test1234", quirks={quirk})) as server:
        write_config(env, [proj(server, password="test1234")])
        assert cli(env, "on", "true")[0]["ok"]


def test_laser_without_lamp_hours(env: dict[str, str]) -> None:
    with FakePjlinkServer(FakeProjector(quirks={"no-lamp"})) as server:
        write_config(env, [proj(server)])
        snap = cli(env, "status")[0]
        assert snap["ok"] and snap["lamps"] is None


def test_unconfigured_is_inert(env: dict[str, str]) -> None:
    out = subprocess.run([PHP or "php", str(CLI), "on", "true"], env=env, capture_output=True,
                         text=True, timeout=30)  # fmt: skip
    assert out.returncode == 0
    assert "no projectors set up" in log_text(env)


# -- REST API through php -S ---------------------------------------------------------------


@pytest.fixture
def api(env: dict[str, str]) -> Iterator[str]:
    port = _closed_port()
    router = str(PLUGIN / "dev" / "router.php")
    proc = subprocess.Popen(
        [PHP or "php", "-S", f"127.0.0.1:{port}", router],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}/api/plugin/fpp-plugin-FacadeKit"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base}/version", timeout=1)
            break
        except httpx.TransportError:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def _token(env: dict[str, str]) -> str:
    code = "require 'lib/api_core.php'; echo fk_token_create();"
    out = subprocess.run([PHP or "php", "-r", code], env=env, cwd=PLUGIN, capture_output=True,
                         text=True, check=True)  # fmt: skip
    return out.stdout.strip()


def test_api_token_rules(env: dict[str, str], api: str, laser: FakePjlinkServer) -> None:
    write_config(env, [proj(laser)])
    version = httpx.get(f"{api}/version").json()
    assert version["plugin"] == "fpp-plugin-FacadeKit" and version["paired"] is False
    assert httpx.get(f"{api}/status").status_code == 403
    token = _token(env)
    assert httpx.get(f"{api}/status").status_code == 401
    assert httpx.get(f"{api}/status", headers={"X-FacadeKit-Token": "fkp_wrong"}).status_code == 401
    ok = httpx.get(f"{api}/status", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and ok.json()["projectors"][0]["power"] == "standby"
    stored = Path(env["FK_PLUGIN_DATA_DIR"], "token.json").read_text("utf-8")
    assert token not in stored
    assert token not in log_text(env)


def test_api_power_and_shows(env: dict[str, str], api: str, laser: FakePjlinkServer) -> None:
    write_config(env, [proj(laser)])
    headers = {"X-FacadeKit-Token": _token(env)}
    on = httpx.post(f"{api}/projectors/on", json={"wait": True}, headers=headers, timeout=60)
    assert on.json()["ok"] and laser.projector.power() == "1"
    bad = {"playlist": "a/b", "video": "x.mp4"}
    assert httpx.post(f"{api}/shows", json=bad, headers=headers).status_code == 400
    show = {"playlist": "FacadeKit - Show", "video": "fk_prj.mp4", "project": "Show",
            "calibration_age_days": 2.5}  # fmt: skip
    assert httpx.post(f"{api}/shows", json=show, headers=headers).json()["ok"]
    health = httpx.get(f"{api}/health", headers=headers).json()
    assert health["shows"][0]["playlist"] == "FacadeKit - Show"
    assert health["player"] is None  # FPP's API is not running in this test
    gone = httpx.delete(f"{api}/shows/FacadeKit%20-%20Show", headers=headers)
    assert gone.json()["ok"]


@pytest.mark.parametrize(
    ("remote", "server", "expected"),
    [
        # a Mac reaching fpp.local over its global IPv6 address, same /64 as the player
        (
            "2600:1700:ffc1:3350:1497:c70b:b4e1:fae0",
            "2600:1700:ffc1:3350:dea6:32ff:fec8:d936",
            True,
        ),
        ("192.168.1.214", "192.168.1.132", True),
        ("::ffff:192.168.1.214", "192.168.1.132", True),
        ("2600:1700:ffc1:9999::1", "2600:1700:ffc1:3350:dea6:32ff:fec8:d936", False),
        ("203.0.113.7", "192.168.1.132", False),
        ("203.0.113.7", "", False),
        ("not-an-ip", "192.168.1.132", False),
    ],
)
def test_same_link_accepts_only_the_players_own_subnet(
    remote: str, server: str, expected: bool
) -> None:
    code = (
        f"require {json.dumps(str(PLUGIN / 'lib' / 'common.php'))};"
        f"echo json_encode(fk_same_link({json.dumps(remote)}, {json.dumps(server)}));"
    )
    out = subprocess.run([PHP or "php", "-r", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) is expected


# -- playlist events: FPP 10.4 sends the stop with an empty playlist name ---------------------

EVENTS = json.loads(
    (Path(__file__).parent / "fixtures" / "fpp_playlist_events.json").read_text("utf-8")
)
SHOW = EVENTS["start"]["name"]


def event(env: dict[str, str], name: str) -> None:
    out = subprocess.run([PHP or "php", str(CLI), "playlist-event", json.dumps(EVENTS[name])],
                         env=env, capture_output=True, text=True, timeout=60)  # fmt: skip
    assert out.returncode == 0, out.stderr


def active_show(env: dict[str, str]) -> str | None:
    path = Path(env["FK_PLUGIN_DATA_DIR"], "active_show.json")
    return json.loads(path.read_text("utf-8"))["name"] if path.exists() else None


def wait_power(server: FakePjlinkServer, wanted: str, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.projector.power() == wanted:
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def fpp_status(env: dict[str, str]) -> Iterator[dict[str, Any]]:
    """A fake fppd /api/fppd/status; set ``playlist`` to what the player is playing."""
    state: dict[str, Any] = {"playlist": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            name = state["playlist"]
            body = json.dumps({"status_name": "playing" if name else "idle",
                               "current_playlist": {"playlist": name or ""}}).encode()  # fmt: skip
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    env["FK_FPP_API"] = f"http://127.0.0.1:{httpd.server_port}"
    yield state
    httpd.shutdown()


def test_empty_name_stop_switches_projectors_off(
    env: dict[str, str], laser: FakePjlinkServer, fpp_status: dict[str, Any]
) -> None:
    write_config(env, [proj(laser)], auto_on=True, auto_off=True, auto_scope="all")
    event(env, "start")
    assert wait_power(laser, "1") and active_show(env) == SHOW
    event(env, "stop")  # name "": the exact payload FPP 10.4 sends
    assert wait_power(laser, "0")
    assert active_show(env) is None
    assert f'"{SHOW}"' in log_text(env) and "FPP sends an empty one" in log_text(env)


def test_stop_without_a_recorded_show_is_ignored(
    env: dict[str, str], laser: FakePjlinkServer, fpp_status: dict[str, Any]
) -> None:
    write_config(env, [proj(laser)], auto_off=True, auto_scope="all")
    laser.projector.set_power("1", 0)
    event(env, "stop")
    time.sleep(1)
    assert laser.projector.power() == "1" and "switching projectors off" not in log_text(env)


def test_second_start_replaces_the_record(
    env: dict[str, str], laser: FakePjlinkServer, fpp_status: dict[str, Any]
) -> None:
    write_config(env, [proj(laser)], auto_scope="all")
    event(env, "start")
    other = dict(EVENTS["start"], name="Another Show")
    subprocess.run([PHP or "php", str(CLI), "playlist-event", json.dumps(other)], env=env,
                   check=True, capture_output=True, timeout=60)  # fmt: skip
    assert active_show(env) == "Another Show"


def test_stop_while_another_show_plays_keeps_projectors_on(
    env: dict[str, str], laser: FakePjlinkServer, fpp_status: dict[str, Any]
) -> None:
    write_config(env, [proj(laser)], auto_on=True, auto_off=True, auto_scope="all")
    event(env, "start")
    assert wait_power(laser, "1")
    fpp_status["playlist"] = "Next Show"
    event(env, "stop")
    time.sleep(1)
    assert laser.projector.power() == "1"
    assert active_show(env) == SHOW  # still recorded; the real stop comes later
    fpp_status["playlist"] = None
    event(env, "stop")
    assert wait_power(laser, "0") and active_show(env) is None


def test_log_uses_one_time_zone_in_every_process(env: dict[str, str]) -> None:
    code = f"require {json.dumps(str(PLUGIN / 'lib' / 'common.php'))}; fk_log('INFO', 'tz');"
    zone = "Pacific/Auckland"
    out = subprocess.run([PHP or "php", "-d", "date.timezone=UTC", "-r", code],
                         env={**env, "TZ": zone}, capture_output=True, text=True)  # fmt: skip
    assert out.returncode == 0, out.stderr
    stamp = datetime.strptime(log_text(env)[:19], "%Y-%m-%d %H:%M:%S")
    local = datetime.now(ZoneInfo(zone)).replace(tzinfo=None)
    assert abs((local - stamp).total_seconds()) < 120


def test_log_follows_fpp_time_zone_setting(env: dict[str, str], tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "settings").write_text('HostName = "FPP"\nTimeZone = "Pacific/Auckland"\n', "utf-8")
    code = f"require {json.dumps(str(PLUGIN / 'lib' / 'common.php'))}; fk_log('INFO', 'tz');"
    out = subprocess.run([PHP or "php", "-d", "date.timezone=UTC", "-r", code],
                         env={**env, "MEDIADIR": str(media), "TZ": "Europe/Paris"},
                         capture_output=True, text=True)  # fmt: skip
    assert out.returncode == 0, out.stderr
    stamp = datetime.strptime(log_text(env)[:19], "%Y-%m-%d %H:%M:%S")
    local = datetime.now(ZoneInfo("Pacific/Auckland")).replace(tzinfo=None)
    assert abs((local - stamp).total_seconds()) < 120
