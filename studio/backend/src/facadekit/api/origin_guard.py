from __future__ import annotations

import os
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from facadekit.api.remote_auth import lan_addresses
from facadekit.errors import BAD_REQUEST

BLOCKED_MESSAGE = "Request from an unknown site was blocked"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hostnames(*, lan: bool, extra: Iterable[str] = ()) -> set[str]:
    names = {"localhost", "127.0.0.1", "::1"}
    if lan:
        names.update(addr.lower() for addr in lan_addresses())
        name = socket.gethostname()
        names.add(name.lower())
        names.add(f"{name}.local".lower())
    names.update(item.strip().lower() for item in extra if item.strip())
    for item in os.environ.get("FACADEKIT_ALLOWED_HOSTS", "").split(","):
        item = item.strip().lower()
        if item:
            names.add(item)
    return names


def host_of(header_value: str) -> str | None:
    """Hostname (lower-case, no brackets or port) from a Host header value, or None."""
    value = header_value.strip()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        name = value[1:end]
        rest = value[end + 1 :]
        if not name or (rest and not (rest.startswith(":") and rest[1:].isdigit())):
            return None
        return name.lower()
    if value.count(":") > 1:
        return None
    name, sep, port = value.partition(":")
    if not name or (sep and not port.isdigit()):
        return None
    if any(ch in name for ch in "/@ \t"):
        return None
    return name.lower()


def port_of(header_value: str) -> int | None:
    """Port from a Host header value that `host_of` accepts; None when it names none."""
    value = header_value.strip()
    tail = value[value.find("]") + 1 :] if value.startswith("[") else value
    _, sep, port = tail.rpartition(":")
    return int(port) if sep and port.isdigit() else None


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


class OriginGuardMiddleware:
    """Pure ASGI middleware rejecting foreign Host headers and cross-site requests.

    Every request needs an allowed Host. An Origin that is not this request's own host and
    port is refused on sockets, on unsafe methods and on any /api or /ws path; /api requests
    a browser marks as ``Sec-Fetch-Site: cross-site`` are refused whatever the method.
    """

    def __init__(self, app: ASGIApp, *, hostnames: set[str]) -> None:
        self.app = app
        self.hostnames = hostnames

    def _allowed(self, scope: Scope) -> bool:
        raw_host = _header(scope, b"host")
        host = host_of(raw_host) if raw_host is not None else None
        if host is None or host not in self.hostnames:
            return False
        # API and socket paths are never meant to be loaded by another site, not even with GET:
        # some GETs have effects (camera snapshot opens the webcam, /fpp/status and
        # /fpp/sequences make the server call the given host). Page loads stay open so a link
        # to the app from elsewhere still works.
        path = str(scope.get("path", ""))
        is_api = path in ("/api", "/ws") or path.startswith(("/api/", "/ws/"))
        if is_api and scope["type"] == "http":
            fetch_site = _header(scope, b"sec-fetch-site")
            if fetch_site is not None and fetch_site.strip().lower() == "cross-site":
                return False
        origin = _header(scope, b"origin")
        if origin is None:
            return True
        checked = (
            is_api or scope["type"] == "websocket" or str(scope["method"]) not in _SAFE_METHODS
        )
        if not checked:
            return True
        # Same origin only: the Origin must name the very host and port this request was sent
        # to. Comparing host names alone would let any other web server on this machine (a
        # dev tool on localhost:3000, say) drive the API. The Vite dev server passes because
        # its proxy keeps the browser's Host header (localhost:5173) next to the same Origin.
        try:
            parts = urlsplit(origin.strip())
            hostname = parts.hostname
            origin_port = parts.port
        except ValueError:
            return False
        if parts.scheme not in ("http", "https") or hostname is None:
            return False
        default_port = 443 if parts.scheme == "https" else 80
        assert raw_host is not None
        host_port = port_of(raw_host)
        return (
            hostname.lower() == host
            and (origin_port or default_port) == (host_port or default_port)
            and host in self.hostnames
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            status_code=403, content={"error": {"code": BAD_REQUEST, "message": BLOCKED_MESSAGE}}
        )
        await response(scope, receive, send)
