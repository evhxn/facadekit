"""The fake PJLink projector speaks PJLink class 1 as the plugin's PHP client expects."""

from __future__ import annotations

import hashlib
import socket
import time

import pytest

from facadekit.fpp.pjlink_fake import (
    POWER_COOLING,
    POWER_ON,
    POWER_STANDBY,
    FakePjlinkServer,
    FakeProjector,
    build_parser,
)


def _read_line(sock: socket.socket) -> str:
    buf = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            return buf.decode()
        if ch in b"\r\n":
            if buf:
                return buf.decode()
            continue
        if ch == b"\x00":
            continue
        buf += ch


def exchange(
    port: int, command: str, password: str | None = None, digest: str | None = None
) -> str:
    """One PJLink exchange the way the plugin does it: connect, greet, (auth), one command."""
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        greeting = _read_line(sock)
        prefix = ""
        if greeting.startswith("PJLINK 1 "):
            random_hex = greeting.split()[2]
            prefix = digest or hashlib.md5((random_hex + (password or "")).encode()).hexdigest()
        else:
            assert greeting == "PJLINK 0"
        sock.sendall(f"{prefix}%1{command}\r".encode())
        return _read_line(sock)


@pytest.fixture
def laser() -> FakePjlinkServer:
    server = FakePjlinkServer(FakeProjector()).start()
    yield server
    server.stop()


def test_default_is_instant_on_off(laser: FakePjlinkServer) -> None:
    assert exchange(laser.port, "POWR ?") == "%1POWR=0"
    assert exchange(laser.port, "POWR 1") == "%1POWR=OK"
    assert exchange(laser.port, "POWR ?") == "%1POWR=1"
    assert exchange(laser.port, "INPT 32") == "%1INPT=OK"
    assert exchange(laser.port, "INPT ?") == "%1INPT=32"
    assert exchange(laser.port, "POWR 0") == "%1POWR=OK"
    assert exchange(laser.port, "POWR ?") == "%1POWR=0"


def test_info_commands(laser: FakePjlinkServer) -> None:
    assert exchange(laser.port, "INF1 ?") == "%1INF1=Panasonic"
    assert exchange(laser.port, "INF2 ?") == "%1INF2=PT-RZ570"
    assert exchange(laser.port, "CLSS ?") == "%1CLSS=1"
    assert exchange(laser.port, "ERST ?") == "%1ERST=000000"
    assert exchange(laser.port, "LAMP ?") == "%1LAMP=1234 0"
    assert exchange(laser.port, "XXXX ?") == "%1XXXX=ERR1"
    assert exchange(laser.port, "INPT 99") == "%1INPT=ERR2"


def test_authentication() -> None:
    with FakePjlinkServer(FakeProjector(password="test1234")) as server:
        assert exchange(server.port, "POWR ?", password="test1234") == "%1POWR=0"
        assert exchange(server.port, "POWR ?", digest="0" * 32) == "PJLINK ERRA"
        assert server.projector.auth_failures == 1


def test_lamp_projector_warmup_and_cooling() -> None:
    proj = FakeProjector(warmup_s=0.3, cooldown_s=0.3)
    with FakePjlinkServer(proj) as server:
        assert exchange(server.port, "POWR 1") == "%1POWR=OK"
        assert exchange(server.port, "POWR ?") == "%1POWR=3"
        assert exchange(server.port, "INPT 31") == "%1INPT=ERR3"
        assert exchange(server.port, "POWR 0") == "%1POWR=ERR3"
        time.sleep(0.4)
        assert exchange(server.port, "POWR ?") == "%1POWR=1"
        assert exchange(server.port, "POWR 0") == "%1POWR=OK"
        assert exchange(server.port, "POWR ?") == "%1POWR=2"
        assert exchange(server.port, "POWR 1") == "%1POWR=ERR3"
        assert proj.power_on_while_cooling == 1
        time.sleep(0.4)
        assert exchange(server.port, "POWR ?") == "%1POWR=0"


@pytest.mark.parametrize("quirk", ["close-after-reply", "split-writes", "crlf", "nul-padding"])
def test_quirks_still_answer(quirk: str) -> None:
    with FakePjlinkServer(FakeProjector(quirks={quirk})) as server:
        assert exchange(server.port, "POWR ?") == "%1POWR=0"


def test_close_after_reply_closes() -> None:
    with FakePjlinkServer(FakeProjector(quirks={"close-after-reply"})) as server:
        with socket.create_connection(("127.0.0.1", server.port), timeout=3) as sock:
            _read_line(sock)
            sock.sendall(b"%1POWR ?\r")
            assert _read_line(sock) == "%1POWR=0"
            assert sock.recv(1) == b""


def test_silent_and_no_lamp_and_err4() -> None:
    with FakePjlinkServer(FakeProjector(quirks={"silent"})) as server:
        with socket.create_connection(("127.0.0.1", server.port), timeout=0.5) as sock:
            _read_line(sock)
            sock.sendall(b"%1POWR ?\r")
            with pytest.raises(TimeoutError):
                sock.recv(1)
    with FakePjlinkServer(FakeProjector(quirks={"no-lamp", "err4"})) as server:
        assert exchange(server.port, "LAMP ?") == "%1LAMP=ERR1"
        assert exchange(server.port, "POWR 1") == "%1POWR=ERR4"


def test_set_power_for_tests() -> None:
    proj = FakeProjector()
    proj.set_power(POWER_COOLING, 0.1)
    assert proj.power() == POWER_COOLING
    time.sleep(0.15)
    assert proj.power() == POWER_STANDBY
    proj.set_power(POWER_ON)
    assert proj.power() == POWER_ON


def test_cli_parser_defaults() -> None:
    args = build_parser().parse_args(["--host", "0.0.0.0", "--count", "2"])
    assert (args.host, args.port, args.count, args.warmup, args.cooldown) == (
        "0.0.0.0",
        4352,
        2,
        0.0,
        0.0,
    )
