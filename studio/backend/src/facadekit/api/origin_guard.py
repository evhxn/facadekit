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


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


class OriginGuardMiddleware:
    """Pure ASGI middleware rejecting foreign Host headers and cross-site Origins."""

    def __init__(self, app: ASGIApp, *, hostnames: set[str]) -> None:
        self.app = app
        self.hostnames = hostnames

    def _allowed(self, scope: Scope) -> bool:
        raw_host = _header(scope, b"host")
        host = host_of(raw_host) if raw_host is not None else None
        if host is None or host not in self.hostnames:
            return False
        origin = _header(scope, b"origin")
        if origin is None:
            return True
        checked = scope["type"] == "websocket" or str(scope["method"]) not in _SAFE_METHODS
        if not checked:
            return True
        try:
            parts = urlsplit(origin.strip())
            hostname = parts.hostname
        except ValueError:
            return False
        return (
            parts.scheme in ("http", "https")
            and hostname is not None
            and (hostname.lower() in self.hostnames)
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
