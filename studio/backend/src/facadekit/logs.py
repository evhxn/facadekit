"""Logging for `facadekit serve`: one rotating file under the data dir plus the terminal.

Every HTTP request gets a short id. The id is on each log line written while the request is
handled and comes back to the browser as the `X-Request-ID` header, so a message in the UI can
be matched to its lines in the log.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections import deque
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOG_NAME = "facadekit.log"
MAX_LOG_BYTES = 1_000_000
LOG_BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"
REQUEST_ID_HEADER = "X-Request-ID"
# Polled several times a second by the UI; logged only when they fail.
SLOW_REQUEST_S = 2.0

request_id_var: ContextVar[str] = ContextVar("fk_request_id", default="-")

log = logging.getLogger("facadekit.request")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / LOG_NAME


def setup_logging(data_dir: Path, *, console: bool = True) -> Path:
    """Send every `facadekit.*` log line to `<data dir>/logs/facadekit.log` (and stderr).

    Safe to call again: the handlers of an earlier call are replaced, not doubled.
    """
    path = log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("facadekit")
    for handler in list(root.handlers):
        if getattr(handler, "_facadekit", False):
            root.removeHandler(handler)
            handler.close()
    handlers: list[logging.Handler] = [
        RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.addFilter(_RequestIdFilter())
        handler._facadekit = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    return path


def redact(text: str) -> str:
    """Replace the home directory (it holds the account name) with `~`."""
    home = str(Path.home())
    return text.replace(home, "~") if len(home) > 1 else text


def tail(path: Path, max_lines: int = 200) -> list[str]:
    """The last lines of the log, home directory redacted; [] when there is no log."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = deque(fh, maxlen=max_lines)
    except OSError:
        return []
    return [redact(line.rstrip("\n")) for line in lines]


async def _send_crash(send: Send, exc: Exception, request_id: str) -> None:
    message = (
        f"Something went wrong inside FacadeKit ({type(exc).__name__}). "
        f"The log has the details under request {request_id}."
    )
    body = json.dumps({"error": {"code": "INTERNAL", "message": message}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestLogMiddleware:
    """Gives each request an id and logs requests that fail or are slow."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(4)
        token = request_id_var.set(request_id)
        started = time.monotonic()
        status = 0

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            # Answer here rather than in Starlette's outer error handler, which runs after
            # this middleware has finished and so no longer knows the request id.
            log.error("%s %s crashed", scope["method"], scope["path"], exc_info=exc)
            if status == 0:
                await _send_crash(send_with_id, exc, request_id)
        finally:
            elapsed = time.monotonic() - started
            line = "%s %s -> %s in %.0f ms"
            args = (scope["method"], scope["path"], status or "no response", elapsed * 1000)
            if status >= 500 or status == 0:
                log.error(line, *args)
            elif status >= 400:
                log.warning(line, *args)
            elif elapsed >= SLOW_REQUEST_S:
                log.info(line + " (slow)", *args)
            request_id_var.reset(token)
