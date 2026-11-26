"""Fake PJLink class 1 projector(s) for tests and for trying the FPP plugin without hardware.

Standard library only, so it also runs as a plain script. Run it on the Mac and point a
projector entry in the FPP plugin at the Mac's Wi-Fi address::

    uv run facadekit fpp fake-pjlink --host 0.0.0.0 --port 4352 --count 2
    python -m facadekit.fpp.pjlink_fake --host 0.0.0.0 --port 4352
    python3 studio/backend/src/facadekit/fpp/pjlink_fake.py --port 4352

The default projector behaves like a laser projector (the owner's Panasonic PT-RZ570 is
said to have no warm-up or cool-down): POWR 1 and POWR 0 take effect at once. ``--warmup``
and ``--cooldown`` simulate a lamp projector, which reports 3 (warm-up) and 2 (cooling) for
that many seconds and answers ERR3 to commands it cannot take then (POWR 1 while cooling,
POWR 0 while warming up, INPT while warming up). Quirks seen on real projectors can be
switched on (``--quirk``): closing the socket after each answer (several Panasonic models),
answers split into small TCP writes, CR LF line ends, NUL padding, a slow greeting, a laser
model that answers ERR1 to LAMP, and silence (to test timeouts).

Protocol: JBMIA PJLink class 1. Greeting ``PJLINK 0`` (no authentication) or
``PJLINK 1 <8 hex>``; with authentication the first command of a connection must be prefixed
with ``md5(<random> + <password>)``; a wrong digest answers ``PJLINK ERRA``.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import socket
import socketserver
import sys
import threading
import time
from dataclasses import dataclass, field

QUIRKS = ("close-after-reply", "split-writes", "crlf", "nul-padding", "slow-greeting",
          "no-lamp", "silent", "lowercase-random", "err4")  # fmt: skip

POWER_STANDBY, POWER_ON, POWER_COOLING, POWER_WARMUP = "0", "1", "2", "3"


@dataclass
class FakeProjector:
    """One fake projector's state. Times are ``time.monotonic()`` seconds."""

    name: str = "Fake projector"
    password: str | None = None
    manufacturer: str = "Panasonic"
    model: str = "PT-RZ570"
    warmup_s: float = 0.0
    cooldown_s: float = 0.0
    input: str = "31"
    inputs: tuple[str, ...] = ("11", "12", "31", "32", "33", "51")
    erst: str = "000000"
    lamp_hours: int = 1234
    quirks: set[str] = field(default_factory=set)
    # recorded
    commands: list[str] = field(default_factory=list)
    auth_failures: int = 0
    power_on_while_cooling: int = 0
    connections: int = 0
    _power: str = POWER_STANDBY
    _until: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def power(self) -> str:
        """The current POWR value, following warm-up and cool-down timers."""
        now = time.monotonic()
        if self._power == POWER_WARMUP and now >= self._until:
            self._power = POWER_ON
        elif self._power == POWER_COOLING and now >= self._until:
            self._power = POWER_STANDBY
        return self._power

    def set_power(self, value: str, for_s: float = 0.0) -> None:
        """Force a state, for tests (``for_s`` for warm-up/cooling)."""
        with self._lock:
            self._power = value
            self._until = time.monotonic() + for_s

    def handle(self, cmd: str, param: str) -> str:
        """Answer one command (without the ``%1`` prefix)."""
        with self._lock:
            self.commands.append(f"{cmd} {param}")
            state = self.power()
            if cmd == "POWR":
                if param == "?":
                    return state
                if "err4" in self.quirks:
                    return "ERR4"
                if param == "1":
                    if state == POWER_COOLING:
                        self.power_on_while_cooling += 1
                        return "ERR3"
                    if state == POWER_STANDBY:
                        if self.warmup_s > 0:
                            self._power, self._until = (
                                POWER_WARMUP,
                                time.monotonic() + self.warmup_s,
                            )
                        else:
                            self._power = POWER_ON
                    return "OK"
                if param == "0":
                    if state == POWER_WARMUP:
                        return "ERR3"
                    if state == POWER_ON:
                        if self.cooldown_s > 0:
                            self._power = POWER_COOLING
                            self._until = time.monotonic() + self.cooldown_s
                        else:
                            self._power = POWER_STANDBY
                    return "OK"
                return "ERR2"
            if cmd == "INPT":
                if param == "?":
                    return self.input if state == POWER_ON else "ERR3"
                if param not in self.inputs:
                    return "ERR2"
                if state != POWER_ON:
                    return "ERR3"
                self.input = param
                return "OK"
            if param != "?":
                return "ERR2"
            if cmd == "ERST":
                return self.erst
            if cmd == "LAMP":
                if "no-lamp" in self.quirks:
                    return "ERR1"
                return f"{self.lamp_hours} {1 if state == POWER_ON else 0}"
            answers = {"NAME": self.name, "INF1": self.manufacturer, "INF2": self.model,
                       "INFO": "fake", "CLSS": "1", "AVMT": "30"}  # fmt: skip
            return answers.get(cmd, "ERR1")


class _Handler(socketserver.BaseRequestHandler):
    server: _Server

    def _send(self, text: str) -> None:
        proj = self.server.projector
        end = "\r\n" if "crlf" in proj.quirks else "\r"
        data = text.encode("ascii", "replace") + end.encode()
        if "nul-padding" in proj.quirks:
            data = b"\x00" + data
        if "split-writes" in proj.quirks:
            for i in range(0, len(data), 3):
                self.request.sendall(data[i : i + 3])
                time.sleep(0.005)
        else:
            self.request.sendall(data)

    def handle(self) -> None:
        proj = self.server.projector
        proj.connections += 1
        sock: socket.socket = self.request
        sock.settimeout(30)
        if "slow-greeting" in proj.quirks:
            time.sleep(0.5)
        random_hex = secrets.token_hex(4)
        if "lowercase-random" not in proj.quirks:
            random_hex = random_hex.upper()
        if proj.password is None:
            self._send("PJLINK 0")
        else:
            self._send(f"PJLINK 1 {random_hex}")
        authed = proj.password is None
        buf = b""
        while True:
            try:
                chunk = sock.recv(256)
            except (TimeoutError, OSError):
                return
            if not chunk:
                return
            buf += chunk
            while b"\r" in buf:
                raw, buf = buf.split(b"\r", 1)
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                if not authed:
                    expected = hashlib.md5(
                        (random_hex + (proj.password or "")).encode()
                    ).hexdigest()
                    if line[:32].lower() != expected or line[32:34] != "%1":
                        proj.auth_failures += 1
                        self._send("PJLINK ERRA")
                        return
                    authed = True
                    line = line[32:]
                if not line.startswith("%1") or len(line) < 6:
                    self._send("%1" + (line[2:6] if len(line) >= 6 else "XXXX") + "=ERR1")
                    continue
                cmd = line[2:6].upper()
                param = line[7:].strip() if len(line) > 6 else ""
                if "silent" in proj.quirks:
                    continue
                self._send(f"%1{cmd}={proj.handle(cmd, param)}")
                if "close-after-reply" in proj.quirks:
                    return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], projector: FakeProjector) -> None:
        self.projector = projector
        super().__init__(address, _Handler)


class FakePjlinkServer:
    """Serve one :class:`FakeProjector` on ``host:port`` (port 0 = any free port)."""

    def __init__(self, projector: FakeProjector, host: str = "127.0.0.1", port: int = 0) -> None:
        self.projector = projector
        self._server = _Server((host, port), projector)
        self.host = host
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> FakePjlinkServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakePjlinkServer:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fake-pjlink",
        description="Fake PJLink class 1 projectors (default: instant on/off like a laser model).",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="listen address; 0.0.0.0 to be reachable from the Pi"
    )
    parser.add_argument("--port", type=int, default=4352, help="first port (default 4352)")
    parser.add_argument("--count", type=int, default=1, help="projectors on consecutive ports")
    parser.add_argument("--password", default=None,
                        help="turn PJLink authentication on with this test password")  # fmt: skip
    parser.add_argument("--warmup", type=float, default=0.0, help="seconds of warm-up (lamp)")
    parser.add_argument("--cooldown", type=float, default=0.0, help="seconds of cooling (lamp)")
    parser.add_argument("--model", default="PT-RZ570")
    parser.add_argument("--quirk", action="append", default=[], choices=QUIRKS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    servers: list[FakePjlinkServer] = []
    try:
        for i in range(max(1, args.count)):
            proj = FakeProjector(name=f"Fake {i + 1}", password=args.password, model=args.model,
                                 warmup_s=args.warmup, cooldown_s=args.cooldown,
                                 quirks=set(args.quirk))  # fmt: skip
            servers.append(FakePjlinkServer(proj, args.host, args.port + i).start())
    except OSError as exc:
        print(f"Could not listen on {args.host}:{args.port}: {exc}", file=sys.stderr)
        for server in servers:
            server.stop()
        return 1
    for server in servers:
        auth = "password on" if args.password else "no password"
        print(f"{server.projector.name}: PJLink on {server.host}:{server.port} ({auth}, "
              f"warm-up {args.warmup:g} s, cooling {args.cooldown:g} s)", flush=True)  # fmt: skip
    print("Ctrl+C to stop. Power changes are printed below.", flush=True)
    last = {id(s): s.projector.power() for s in servers}
    try:
        while True:
            time.sleep(0.5)
            for server in servers:
                with server.projector._lock:
                    now = server.projector.power()
                if now != last[id(server)]:
                    names = {"0": "standby", "1": "on", "2": "cooling", "3": "warm-up"}
                    print(f"{time.strftime('%H:%M:%S')} {server.projector.name}: "
                          f"{names.get(now, now)}", flush=True)  # fmt: skip
                    last[id(server)] = now
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
