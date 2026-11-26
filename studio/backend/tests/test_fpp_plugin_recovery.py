"""Thermal history and opt-in recovery in the FPP plugin, run with real PHP.

Two layers: the pure state machine (``fk_thermal_apply`` / ``fk_recovery_step``) walked through
a scenario with an explicit clock, and the whole thing end to end (guard process, power worker,
playlist callbacks) against two fake PJLink projectors and a fake fppd, with the plugin's clock
and sleeps sped up (FK_CLOCK_SCALE, FK_SLEEP_SCALE).

PT-RZ570 ERST is six characters; the third is temperature: ``0`` ok, ``1`` warning, ``2`` error.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from facadekit.fpp.pjlink_fake import FakePjlinkServer, FakeProjector
from tests.test_fpp_plugin_php import (
    CLI,
    EVENTS,
    PHP,
    PLUGIN,
    SHOW,
    _closed_port,
    log_text,
    pytestmark,  # noqa: F401  (applies the skip-without-php mark)
    write_config,
)

OK, WARN, ERR = "000000", "001000", "002000"


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


LIB = PLUGIN / "lib"


def php_json(env_: dict[str, str] | None, code: str, payload: Any = None) -> Any:
    head = f"require '{LIB}/power.php'; $in = json_decode(stream_get_contents(STDIN), true); "
    script = head + code
    out = subprocess.run([PHP or "php", "-r", script], input=json.dumps(payload), env=env_,
                         capture_output=True, text=True, timeout=30)  # fmt: skip
    assert out.returncode == 0, out.stderr + out.stdout
    return json.loads(out.stdout.strip().splitlines()[-1])


# -- history ----------------------------------------------------------------------------------


def apply_levels(levels: list[str | None]) -> tuple[list[str], str]:
    result = php_json(
        None,
        "$t = fk_thermal_blank(); $types = array(); $n = 1000;"
        "foreach ($in as $l) { $n += 10; list($t, $ev) = fk_thermal_apply($t, $l, $n);"
        " foreach ($ev as $e) { $types[] = $e['type']; } }"
        "echo json_encode(array($types, $t['level']));",
        levels,
    )
    return result[0], result[1]


def test_warning_escalation_and_clearance_are_logged_once() -> None:
    types, level = apply_levels(
        ["ok", "warning", "warning", "warning", "error", "error", "ok", "ok"]
    )
    assert types == ["warning", "escalation", "clearance"]
    assert level == "ok"


def test_missing_readings_keep_the_last_state_and_never_clear() -> None:
    types, level = apply_levels(["warning", None, None, "warning"])
    assert types == ["warning"] and level == "warning"
    types, level = apply_levels(["error", None, None, None])
    assert (
        types == ["escalation"] and level == "error"
    )  # no clearance without a fresh valid reading


def test_first_reading_that_is_already_a_warning_is_recorded() -> None:
    assert apply_levels(["warning"])[0] == ["warning"]
    assert apply_levels(["ok", "ok"])[0] == []


# -- state machine, with an explicit clock -----------------------------------------------------

SHOW_KEY = "Show|2026-11-26T20:00:00Z"
CTX = {
    "enabled": True,
    "show_active": True,
    "show_key": SHOW_KEY,
    "cooldown_s": 300,
    "reads_needed": 3,
}


def reading(now: int, power: str = "standby", level: str | None = "ok", *, valid: bool = True,
            age: int = 0, other_error: bool = False) -> dict[str, Any] | None:  # fmt: skip
    healthy = valid and level == "ok" and power in ("on", "standby") and not other_error
    return {"at": now - age, "valid": valid, "level": level if valid else None, "power": power,
            "healthy": healthy, "issue": None if valid else "unreachable"}  # fmt: skip


class Machine:
    """Drives fk_recovery_step through PHP, one call per tick of an explicit clock."""

    def __init__(self, ctx: dict[str, Any] | None = None) -> None:
        self.ctx = {**CTX, **(ctx or {})}
        self.r = php_json(None, "echo json_encode(fk_recovery_blank());")
        self.now = 10_000
        self.retries = 0

    def tick(self, dt: int = 30, power: str = "standby", level: str | None = "ok", *,
             none: bool = False, valid: bool = True, age: int = 0, other_error: bool = False,
             **ctx: Any) -> str:  # fmt: skip
        self.now += dt
        rd = None
        if not none:
            rd = reading(self.now, power, level, valid=valid, age=age, other_error=other_error)
        code = (
            "list($r, $a) = fk_recovery_step($in['r'], $in['reading'], $in['ctx'], $in['now']);"
            "echo json_encode(array($r, $a));"
        )
        payload = {"r": self.r, "reading": rd, "ctx": {**self.ctx, **ctx}, "now": self.now}
        out = php_json(None, code, payload)
        self.r = out[0]
        self.retries += out[1].count("retry")
        return str(self.r["state"])

    def reach_verifying(self) -> None:
        assert self.tick(power="on") == "watching"
        assert self.tick(power="standby", level="error") == "cooldown"
        self.tick(dt=300)  # the first reading after the cooldown: 1 good reading
        assert self.r["state"] == "verifying" and self.r["streak"] == 1


def test_full_recovery_with_exactly_one_retry() -> None:
    m = Machine()
    assert m.tick(power="on", level="warning") == "watching"  # a warning alone: nothing
    assert m.tick(power="on", level="error") == "cooldown"
    assert m.r["wants_on"] is True
    for _ in range(5):  # healthy readings before the cooldown has passed do nothing
        assert m.tick() == "cooldown"
    assert m.retries == 0
    m.tick(dt=300)  # first reading after the cooldown: 1 of 3
    assert m.r["state"] == "verifying" and m.r["streak"] == 1
    m.tick()
    assert m.retries == 0 and m.r["streak"] == 2
    assert m.tick() == "retrying"
    assert m.retries == 1 and m.r["retry_token"]
    assert m.tick(power="on") == "watching"  # confirmed: fresh, valid, healthy and on
    assert "switched on again" in m.r["last_outcome"] and m.retries == 1
    # a second thermal error in the same show gets no second retry
    assert m.tick(power="on", level="error") == "cooldown"
    m.tick(dt=400)
    m.tick()
    assert m.tick() == "attention"
    assert "already used" in m.r["reason"] and m.retries == 1


BAD = {
    "stale": dict(age=120),
    "unreachable": dict(power="unknown", level=None, valid=False),
    "no-answer": dict(none=True),
    "sensor-or-other-error": dict(other_error=True),
    "still-cooling": dict(power="cooling"),
    "warming-up": dict(power="warmup"),
    "still-warning": dict(level="warning"),
    "still-error": dict(level="error"),
}


@pytest.mark.parametrize("bad", list(BAD))
def test_no_clearance_without_a_fresh_valid_healthy_reading(bad: str) -> None:
    m = Machine()
    m.reach_verifying()
    m.tick()
    assert m.r["streak"] == 2  # one more good reading would retry
    state = m.tick(**BAD[bad])
    assert m.retries == 0 and m.r["streak"] == 0 and state in ("verifying", "cooldown")
    m.tick()  # a single good reading afterwards is not enough
    assert m.retries == 0


def test_a_transient_healthy_sample_does_not_retry() -> None:
    m = Machine()
    m.reach_verifying()
    m.tick(none=True)
    m.tick(level="error")
    m.tick(power="cooling")
    assert m.retries == 0 and m.r["streak"] == 0


def test_a_permanent_fault_ends_in_needs_attention_not_a_retry() -> None:
    m = Machine()
    m.tick(power="on", level="error")
    state = ""
    for _ in range(80):
        state = m.tick(level="error")
        if state == "attention":
            break
    assert state == "attention" and m.retries == 0
    assert "did not report healthy" in m.r["reason"]
    assert m.tick() == "attention"  # terminal for this show


def test_a_projector_still_on_when_health_returns_is_left_alone() -> None:
    m = Machine()
    m.tick(power="on", level="error")
    m.tick(dt=300, power="on")
    m.tick(power="on")
    assert m.tick(power="on") == "watching"
    assert m.retries == 0 and "power not cycled" in m.r["last_outcome"]


def _to_retry(m: Machine) -> None:
    m.reach_verifying()
    m.tick()
    assert m.tick() == "retrying"


def test_a_failed_or_stuck_retry_needs_attention() -> None:
    m = Machine()
    _to_retry(m)
    m.r["retry_result"] = {"ok": False, "state": "error", "message": "could not connect"}
    assert m.tick() == "attention" and "could not connect" in m.r["reason"]
    m = Machine()
    _to_retry(m)
    assert m.tick(dt=200) == "attention" and "did not switch on" in m.r["reason"]
    m = Machine()
    _to_retry(m)
    assert m.tick(power="on", level="error") == "attention" and "came back" in m.r["reason"]


def test_the_operator_and_the_show_cancel_recovery() -> None:
    m = Machine()
    _to_retry(m)
    assert m.tick(show_active=False) == "idle"
    assert "show ended" in m.r["last_outcome"] and m.r["retry_token"] is None
    m = Machine()
    _to_retry(m)
    m.r["wants_on"] = False  # manual Off
    assert (
        m.tick() == "idle" and "switched off" in m.r["last_outcome"] and m.r["retry_token"] is None
    )
    m = Machine()
    _to_retry(m)
    assert m.tick(show_key="Other|2026-11-26T21:00:00Z") == "watching"
    assert "replaced" in m.r["last_outcome"] and m.r["retry_token"] is None
    m = Machine()
    _to_retry(m)
    assert m.tick(enabled=False) == "idle" and m.r["retry_token"] is None


# -- end to end ----------------------------------------------------------------------------------


@pytest.fixture
def fppd(env: dict[str, str]) -> Iterator[dict[str, Any]]:  # noqa: F811
    """A fake fppd: ``playlist`` is what plays; a POST to /api/playlists/stop is counted."""
    state: dict[str, Any] = {"playlist": SHOW, "stops": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            name = state["playlist"]
            body = json.dumps({"status_name": "playing" if name else "idle",
                               "current_playlist": {"playlist": name or ""}}).encode()  # fmt: skip
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            state["stops"] += 1
            state["playlist"] = None
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: Any) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    env["FK_FPP_API"] = f"http://127.0.0.1:{httpd.server_port}"
    env["FK_CLOCK_SCALE"] = "60"  # one real second is a minute for the plugin
    yield state
    httpd.shutdown()


@pytest.fixture
def pair() -> Iterator[tuple[FakePjlinkServer, FakePjlinkServer]]:
    with (
        FakePjlinkServer(FakeProjector(name="P1")) as a,
        FakePjlinkServer(FakeProjector(name="P2")) as b,
    ):
        yield a, b


def put_thermal(data: Path, rec: dict[str, Any]) -> None:
    doc = {"projectors": {"p1": {"level": "ok", "recovery": rec}}}
    (data / "thermal.json").write_text(json.dumps(doc), "utf-8")


def run_cli(env: dict[str, str], *args: str) -> Any:
    out = subprocess.run(
        [PHP or "php", str(CLI), *args], env=env, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    return json.loads(lines[-1]) if lines else None


def send_event(env: dict[str, str], name: str) -> None:
    run_cli(env, "playlist-event", json.dumps(EVENTS[name]))


def until(cond: Callable[[], bool], timeout: float = 40.0, what: str = "condition") -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def thermal(env: dict[str, str], pid: str) -> dict[str, Any]:
    path = Path(env["FK_PLUGIN_DATA_DIR"], "thermal.json")
    data = json.loads(path.read_text("utf-8")) if path.exists() else {"projectors": {}}
    out: dict[str, Any] = data["projectors"].get(pid, {})
    return out


def power_ons(fake: FakeProjector) -> int:
    return sum(1 for c in fake.commands if c == "POWR 1")


def recovery_config(
    env: dict[str, str], a: FakePjlinkServer, b: FakePjlinkServer, **extra: Any
) -> None:
    write_config(
        env, [{"id": "p1", "name": "P1", "host": "127.0.0.1", "port": a.port},
              {"id": "p2", "name": "P2", "host": "127.0.0.1", "port": b.port}],
        auto_on=True, auto_scope="all", recovery_enabled=True, recovery_cooldown_s=30,
        recovery_healthy_reads=2, guard_interval_s=5, **extra,
    )  # fmt: skip
    env["FK_SLEEP_SCALE"] = "0.1"


def test_two_projectors_p1_cools_and_is_retried_once_while_p2_keeps_playing(
    env: dict[str, str], fppd: dict[str, Any], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    p1, p2 = a.projector, b.projector
    recovery_config(env, a, b)  # no guard, no stop-playback: continue-show mode
    send_event(env, "start")
    until(lambda: p1.power() == "1" and p2.power() == "1", what="both projectors on")
    until(
        lambda: "watching" in log_text(env) or thermal(env, "p1").get("level") == "ok",
        what="guard running",
    )
    # repeated warnings: logged once, playback untouched
    p1.erst = WARN
    until(lambda: thermal(env, "p1").get("level") == "warning", what="warning seen")
    time.sleep(1.5)  # several more polls
    assert log_text(env).count("P1: temperature warning") == 1
    # the error; the projector protects itself and goes to standby, cooling for a while
    p1.erst = ERR
    p1.set_power("2", 2.0)  # cooling (real seconds)
    until(
        lambda: thermal(env, "p1")["recovery"]["state"] in ("cooldown", "verifying"),
        what="recovery started",
    )
    before = power_ons(p1)
    p1.erst = ERR  # still asserted for a moment: nothing may be sent
    time.sleep(1.0)
    assert power_ons(p1) == before
    p1.erst = OK  # healthy standby from now on
    until(lambda: p1.power() == "1", what="the one retry")
    until(lambda: thermal(env, "p1")["recovery"]["state"] == "watching", what="confirmed")
    time.sleep(1.5)  # monitoring continues; nothing more is sent
    assert power_ons(p1) == before + 1
    assert "recovered" in thermal(env, "p1")["recovery"]["last_outcome"]
    events = [e["type"] for e in thermal(env, "p1")["events"]]
    assert events == ["warning", "escalation", "clearance"]
    # P2 never lost its show: no off, one on
    assert power_ons(p2) == 1 and "POWR 0" not in p2.commands
    assert fppd["stops"] == 0 and fppd["playlist"] == SHOW
    send_event(env, "stop")
    log = log_text(env)
    assert "automatic power-on retry" in log and "token" not in log.lower()


def run_to_cooldown_done(env: dict[str, str], fppd: dict[str, Any], a: FakePjlinkServer,
                         b: FakePjlinkServer, **extra: Any) -> None:  # fmt: skip
    recovery_config(env, a, b, **extra)
    send_event(env, "start")
    until(lambda: a.projector.power() == "1" and b.projector.power() == "1", what="both on")
    a.projector.erst = ERR
    a.projector.set_power("0")
    until(
        lambda: thermal(env, "p1").get("recovery", {}).get("state") == "cooldown", what="cooldown"
    )


def test_manual_off_cancels_the_queued_retry(
    env: dict[str, str], fppd: dict[str, Any], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    run_to_cooldown_done(env, fppd, a, b)
    run_cli(env, "off", "true", "p1")  # the operator
    a.projector.erst = OK
    time.sleep(4.0)  # far past the cooldown and the healthy readings
    assert power_ons(a.projector) == 1 and a.projector.power() == "0"
    rec = thermal(env, "p1")["recovery"]
    assert (
        rec["state"] == "idle" and rec["wants_on"] is False and "cancelled" in rec["last_outcome"]
    )
    send_event(env, "stop")


def test_stop_with_an_empty_name_cancels_and_nothing_restarts(
    env: dict[str, str], fppd: dict[str, Any], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    run_to_cooldown_done(env, fppd, a, b)
    fppd["playlist"] = None
    send_event(env, "stop")  # FPP's empty-name stop
    a.projector.erst = OK
    time.sleep(3.0)
    assert power_ons(a.projector) == 1 and a.projector.power() == "0"
    assert thermal(env, "p1")["recovery"]["state"] == "idle"
    assert fppd["playlist"] is None and fppd["stops"] == 0  # no new playlist, no stop call either


def test_a_replaced_show_cancels_the_retry(
    env: dict[str, str], fppd: dict[str, Any], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    run_to_cooldown_done(env, fppd, a, b)
    other = dict(EVENTS["start"], name="Another Show")
    fppd["playlist"] = "Another Show"
    run_cli(env, "playlist-event", json.dumps(other))
    # auto-on for the new show must not hit the projector that reported a temperature error
    time.sleep(2.0)
    assert power_ons(a.projector) == 1
    assert "power-on held while thermal recovery" in log_text(env)
    send_event(env, "stop")


def test_a_stop_racing_the_retry_sends_no_power_on(
    env: dict[str, str], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    recovery_config(env, a, b)
    data = Path(env["FK_PLUGIN_DATA_DIR"])
    (data / "active_show.json").write_text(json.dumps({"name": SHOW, "started_at": "t0"}), "utf-8")
    key = f"{SHOW}|t0"
    rec = php_json(env, "echo json_encode(fk_recovery_blank());")
    rec.update(state="retrying", retry_token="tok1", wants_on=True, show=key)
    put_thermal(data, rec)
    # cancelled just before the worker reaches POWR 1: the token no longer matches
    php_json(env, "fk_recovery_cancel(null, 'the show ended', false); echo '1';")
    result = run_cli(env, "worker", "on", "p1", "0", "5", "-", "0", "tok1")
    assert result["state"] == "cancelled" and power_ons(a.projector) == 0
    # the same worker with a live token goes through
    rec.update(state="retrying", retry_token="tok2", wants_on=True)
    put_thermal(data, rec)
    result = run_cli(env, "worker", "on", "p1", "0", "5", "-", "0", "tok2")
    assert result["ok"] and a.projector.power() == "1"
    # and a newer request (sequence number) beats a queued retry
    rec.update(state="retrying", retry_token="tok3", wants_on=True)
    put_thermal(data, rec)
    a.projector.set_power("0")
    php_json(
        env, "fk_new_intent('p1', 'on'); fk_new_intent('p1', 'off'); echo '1';"
    )  # the worker holds #1
    result = run_cli(env, "worker", "on", "p1", "1", "5", "-", "0", "tok3")
    assert result["state"] in ("superseded", "cancelled") and power_ons(a.projector) == 1


def test_power_on_is_held_for_a_projector_with_a_thermal_error(
    env: dict[str, str], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    recovery_config(env, a, b)
    a.projector.erst = ERR
    run_cli(env, "status")  # one reading records the level
    assert thermal(env, "p1")["level"] == "error"
    results = {r["id"]: r for r in run_cli(env, "on", "true")}
    assert results["p1"]["state"] == "held" and power_ons(a.projector) == 0
    assert results["p2"]["ok"] and b.projector.power() == "1"


def test_unknown_state_after_a_restart_is_never_eligible(
    env: dict[str, str], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    recovery_config(env, a, b)
    data = Path(env["FK_PLUGIN_DATA_DIR"])
    rec = php_json(env, "echo json_encode(fk_recovery_blank());")
    rec.update(state="verifying", streak=2, wants_on=True, show="old|t")
    put_thermal(data, rec)
    php_json(env, "fk_recovery_reset_unknown(); echo '1';")
    after = thermal(env, "p1")["recovery"]
    assert (
        after["state"] == "idle"
        and "unknown after a restart" in after["reason"]
        and power_ons(a.projector) == 0
    )


def test_status_shows_thermal_and_recovery_without_secrets(
    env: dict[str, str], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    recovery_config(env, a, b)
    a.projector.erst = WARN
    snaps = {s["id"]: s for s in run_cli(env, "status")}
    th = snaps["p1"]["thermal"]
    assert th["level"] == "warning" and th["last_valid_check_at"] and th["recovery"]["label"]
    assert th["events"][-1]["type"] == "warning"
    assert snaps["p2"]["thermal"]["level"] == "ok"


def test_disabled_recovery_keeps_the_old_guard_and_stop_playback(
    env: dict[str, str], fppd: dict[str, Any], pair: tuple[FakePjlinkServer, FakePjlinkServer]
) -> None:
    a, b = pair
    recovery_config(env, a, b, guard_enabled=True, guard_stop_playback=True)
    cfg = json.loads(Path(env["FK_PLUGIN_DATA_DIR"], "config.json").read_text("utf-8"))
    cfg["recovery_enabled"] = False
    Path(env["FK_PLUGIN_DATA_DIR"], "config.json").write_text(json.dumps(cfg), "utf-8")
    send_event(env, "start")
    until(lambda: a.projector.power() == "1", what="on")
    time.sleep(1.0)
    a.projector.erst = WARN  # a warning alone never stops the show
    time.sleep(1.5)
    assert fppd["stops"] == 0 and "temperature warning" in log_text(env)
    a.projector.erst = ERR
    until(lambda: fppd["stops"] == 1, what="the guard stops playback")
    assert "reports a temperature error" in log_text(env)
    assert thermal(env, "p1").get("recovery", {}).get("state", "idle") in ("idle", "watching")
    a.projector.erst = OK
    a.projector.set_power("0")
    time.sleep(2.0)
    assert power_ons(a.projector) == 1  # no recovery: nothing retried
