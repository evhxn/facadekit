from __future__ import annotations

import re
import secrets
import socket
import time
from collections.abc import Callable
from http.cookies import SimpleCookie
from ipaddress import IPv4Address, ip_address
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from facadekit.errors import BAD_REQUEST

COOKIE_NAME = "fk_remote"
SESSION_TTL_S = 12 * 3600
MAX_WRONG_ATTEMPTS = 5
LOCKOUT_S = 5 * 60
PAIR_MESSAGE = "Pair this device first"

# Starlette's TestClient reports the peer as "testclient". Real peers are always IP addresses,
# so treating this literal as loopback cannot widen access on a real socket.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "testclient"}

# What a paired (non-loopback) device may call: (method, path regex). "WS" is the handshake.
_PROJECT = re.compile(r"/api/v1/projects/[^/]+")
_REMOTE_ROUTES: list[tuple[str, re.Pattern[str]]] = [
    ("GET", _PROJECT),
    ("PUT", _PROJECT),
    ("POST", re.compile(r"/api/v1/projector/show")),
    ("GET", re.compile(r"/api/v1/projector/status")),
    ("GET", re.compile(r"/api/v1/health")),
    ("WS", re.compile(r"/ws/events")),
]


def is_loopback(host: str | None) -> bool:
    return host is not None and host in _LOOPBACK_HOSTS


def lan_addresses() -> list[str]:
    """Non-loopback IPv4 addresses of this machine, without duplicates."""
    found: list[str] = []

    def add(raw: str) -> None:
        try:
            addr = ip_address(raw)
        except ValueError:
            return
        if not isinstance(addr, IPv4Address) or addr.is_loopback or addr.is_link_local:
            return
        if addr.is_unspecified or raw in found:
            return
        found.append(raw)

    for target in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((target, 9))  # UDP connect sends nothing; it just picks a route
                add(str(sock.getsockname()[0]))
        except OSError:
            continue
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(str(info[4][0]))
    except OSError:
        pass
    return found


class RemoteAuth:
    """LAN-mode state: pairing code, in-memory sessions and per-address lockouts."""

    def __init__(
        self,
        *,
        lan: bool = False,
        code: str | None = None,
        client_addr_override: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_pair: Callable[[str], None] | None = None,
    ) -> None:
        self.lan = lan
        # The code exists only in LAN mode and is never logged; the CLI prints it once at startup.
        self.code: str | None = (code or f"{secrets.randbelow(10**6):06d}") if lan else None
        self._override = client_addr_override
        self._clock = clock
        self._on_pair = on_pair
        self._sessions: dict[str, float] = {}
        self._wrong: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def client_host(self, scope: Scope) -> str | None:
        if self._override is not None:
            return self._override()
        # Behind the Vite dev proxy the peer is always loopback; fine for development only.
        client = scope.get("client")
        return str(client[0]) if client else None

    def _valid_session(self, token: str | None) -> bool:
        if not self.lan or not token:
            return False
        expires = self._sessions.get(token)
        if expires is None:
            return False
        if expires <= self._clock():
            del self._sessions[token]
            return False
        return True

    def _cookie(self, scope: Scope) -> str | None:
        for key, value in scope.get("headers", []):
            if key == b"cookie":
                jar: SimpleCookie = SimpleCookie()
                try:
                    jar.load(value.decode("latin-1"))
                except Exception:
                    return None
                morsel = jar.get(COOKIE_NAME)
                return morsel.value if morsel else None
        return None

    def authorize(self, scope: Scope) -> bool:
        host = self.client_host(scope)
        if is_loopback(host):
            return True
        if not self._valid_session(self._cookie(scope)):
            return False
        kind = "WS" if scope["type"] == "websocket" else str(scope["method"])
        path = str(scope["path"])
        return any(m == kind and rx.fullmatch(path) for m, rx in _REMOTE_ROUTES)

    def pair(self, host: str, code: str) -> tuple[str | None, str]:
        """Returns (session token, "") on success, or (None, error message)."""
        if not self.lan or self.code is None:
            return None, PAIR_MESSAGE
        now = self._clock()
        if self._locked_until.get(host, 0.0) > now:
            return None, "Too many attempts"
        if not secrets.compare_digest(code.encode(), self.code.encode()):
            self._wrong[host] = self._wrong.get(host, 0) + 1
            if self._wrong[host] >= MAX_WRONG_ATTEMPTS:
                self._wrong[host] = 0
                self._locked_until[host] = now + LOCKOUT_S
                return None, "Too many attempts"
            return None, "Wrong code"
        self._wrong.pop(host, None)
        token = secrets.token_hex(16)
        self._sessions[token] = now + SESSION_TTL_S
        if self._on_pair is not None:
            self._on_pair(host)
        return token, ""


def _forbidden(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403, content={"error": {"code": BAD_REQUEST, "message": message}}
    )


class RemoteAccessMiddleware:
    """Pure ASGI middleware enforcing the loopback / paired-device access rule."""

    def __init__(self, app: ASGIApp, auth: RemoteAuth) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = str(scope["path"])
        is_api = path in ("/api", "/ws") or path.startswith(("/api/", "/ws/"))
        exempt = not is_api or (
            scope["type"] == "http" and scope["method"] == "POST" and path == "/api/v1/remote/pair"
        )
        if exempt or self.auth.authorize(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        await _forbidden(PAIR_MESSAGE)(scope, receive, send)


router = APIRouter()


class PairRequest(BaseModel):
    code: str


@router.get("/remote/info")
def remote_info(request: Request) -> Any:
    auth: RemoteAuth = request.app.state.remote
    if not is_loopback(auth.client_host(request.scope)):
        return _forbidden(PAIR_MESSAGE)
    if not auth.lan:
        return {"enabled": False, "code": None, "urls": []}
    server = request.scope.get("server")
    port = int(server[1]) if server and server[1] else 8137
    return {
        "enabled": True,
        "code": auth.code,
        "urls": [f"http://{ip}:{port}" for ip in lan_addresses()],
    }


@router.post("/remote/pair")
def remote_pair(body: PairRequest, request: Request) -> Any:
    auth: RemoteAuth = request.app.state.remote
    host = auth.client_host(request.scope) or "unknown"
    token, message = auth.pair(host, body.code)
    if token is None:
        return _forbidden(message)
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_S, httponly=True, samesite="strict")
    return response
