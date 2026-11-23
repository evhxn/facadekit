"""HTTP client for Falcon Player (FPP). The only module that speaks HTTP to FPP.

The request and response shapes were checked against the FPP 10.4 source (tag ``10.4``,
commit 123b8ca; see docs/17-fpp-integration-audit.md). Parsing stays defensive because a
real player has not been recorded yet (spike S-1), and the directory name is always a
caller-supplied argument.

Every failure is an :class:`FppError` (a :class:`FkError`) whose ``reason`` says what went
wrong in a word (``dns``, ``refused``, ``timeout``, ``no_route``, ``http_status``,
``not_json``, ``not_fpp``, ``auth_required``, ``auth_rejected``, ``forbidden``, ...) and
whose message tells the user what to do. The underlying exception class is logged.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from facadekit.errors import (
    FPP_AUTH_REQUIRED,
    FPP_BAD_RESPONSE,
    FPP_UNREACHABLE,
    FPP_UPLOAD_FAILED,
    FkError,
)

log = logging.getLogger(__name__)

CHUNK_SIZE = 8 * 1024 * 1024
UPLOAD_TIMEOUT = 120.0
# FPP joins all chunks into the final file while it answers the last PATCH
# (PatchFileAssemble in www/api/controllers/files.php), so that answer can take as long
# as copying the whole file on the Pi's SD card. Allow this many bytes per second on top
# of UPLOAD_TIMEOUT [assumption: a slow SD card writes at least 5 MB/s].
ASSEMBLE_BYTES_PER_S = 5 * 1024 * 1024
PASSWORD_ENV = "FACADEKIT_FPP_PASSWORD"
USER_ENV = "FACADEKIT_FPP_USER"
DEFAULT_USER = "admin"  # FPP writes the UI password for users "admin" and "fpp" [V]
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)")
_MAX_ERR_BODY = 200
START_CONFIRM_S = 3.0  # how long to wait for FPP's status to show a started playlist

LOCAL_NETWORK_HINT = (
    "On macOS, the app that started FacadeKit needs Local Network access: System Settings > "
    "Privacy & Security > Local Network, turn it on for that app (Terminal, for example), "
    "or start FacadeKit from the Terminal app."
)


@dataclass
class FppVersion:
    version: str
    major: int
    minor: int
    branch: str


class FppError(FkError):
    """An FPP failure. ``reason`` is a short machine-readable cause."""

    def __init__(self, code: str, message: str, *, reason: str) -> None:
        super().__init__(code, message)
        self.reason = reason


def _seg(value: str) -> str:
    return quote(value, safe="")


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _chain(exc: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    out: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        out.append(current)
        out.extend(arg for arg in getattr(current, "args", ()) if isinstance(arg, BaseException))
        current = current.__cause__ or current.__context__
    return out


def _errno_of(exc: BaseException) -> int | None:
    """The first OS error number in the exception's cause chain."""
    for item in _chain(exc):
        if isinstance(item, OSError) and isinstance(item.errno, int):
            return item.errno
    text = str(exc)
    match = re.search(r"\[Errno (\d+)\]", text)
    return int(match.group(1)) if match else None


_DNS_MARKERS = (
    "nodename nor servname",
    "name or service not known",
    "name resolution",
    "getaddrinfo failed",
    "no address associated",
)


def _has_dns_error(exc: BaseException) -> bool:
    for item in _chain(exc):
        if isinstance(item, socket.gaierror):
            return True
        if any(marker in str(item).lower() for marker in _DNS_MARKERS):
            return True
    return False


def describe_transport_error(host: str, exc: httpx.TransportError) -> tuple[str, str]:
    """Return ``(reason, message)`` for a failure to talk to FPP at all."""
    number = _errno_of(exc)
    mac_hint = f" {LOCAL_NETWORK_HINT}" if _is_macos() else ""
    bare = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if isinstance(exc, httpx.TimeoutException):
        what = "to connect" if isinstance(exc, httpx.ConnectTimeout) else "to answer"
        return "timeout", (
            f"FPP at {host} took too long {what}. Check that the Pi is powered on and on the "
            f"same network as this computer.{mac_hint}"
        )
    if _has_dns_error(exc):
        extra = (
            " Names ending in .local need mDNS (Bonjour); try the Pi's IP address instead."
            if bare.lower().endswith(".local")
            else " Check the spelling, or use the Pi's IP address."
        )
        return "dns", f"The name {bare} could not be found on the network.{extra}"
    if number == errno.ECONNREFUSED:
        return "refused", (
            f"{host} refused the connection. The device is on the network but nothing answers "
            "on that port: check that FPP is running and the address (and port, if you gave "
            "one)."
        )
    if number in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN):
        return "no_route", (
            f"No route to {host}. Check the address and that the Pi is on the same network "
            f"as this computer.{mac_hint}"
        )
    if isinstance(exc, httpx.RemoteProtocolError):
        return "not_http", f"{host} answered, but not with valid HTTP. Is it really FPP?"
    if "ssl" in str(exc).lower() or "certificate" in str(exc).lower():
        return "tls", f"HTTPS to {host} failed ({exc}). FPP normally uses plain http://."
    return "unreachable", (
        f"Could not reach FPP at {host} ({type(exc).__name__}). Check the address and that "
        f"the Pi is on the same network.{mac_hint}"
    )


def _parse_host(host: str) -> tuple[str, str]:
    """Return ``(scheme, host[:port])``. Plain http unless https:// is given."""
    cleaned = host.strip()
    scheme = "http"
    lowered = cleaned.lower()
    if lowered.startswith("https://"):
        scheme, cleaned = "https", cleaned[len("https://") :]
    elif lowered.startswith("http://"):
        cleaned = cleaned[len("http://") :]
    cleaned = cleaned.rstrip("/")
    if not cleaned:
        raise FkError("BAD_REQUEST", "FPP host is empty")
    return scheme, cleaned


def _body_excerpt(response: httpx.Response) -> str:
    text = response.text.strip().replace("\n", " ")
    return text[:_MAX_ERR_BODY]


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _status_error(data: Any) -> str | None:
    """FPP's PHP API reports some failures as HTTP 200 with an error status in the body."""
    if not isinstance(data, dict):
        return None
    status = data.get("Status", data.get("status"))
    if isinstance(status, str) and status.upper() not in ("OK", ""):
        message = data.get("Message") or data.get("message") or data.get("error") or ""
        return f"{status}: {message}" if message else status
    return None


class FppClient:
    def __init__(
        self,
        host: str,
        *,
        timeout: float = 10.0,
        password: str | None = None,
        username: str | None = None,
    ) -> None:
        scheme, cleaned = _parse_host(host)
        self.host = cleaned
        self.scheme = scheme
        if password is None:
            password = os.environ.get(PASSWORD_ENV) or None
        user = username or os.environ.get(USER_ENV) or DEFAULT_USER
        self.has_password = password is not None
        auth = httpx.BasicAuth(user, password) if password is not None else None
        # No Origin header is ever sent: FPP refuses a POST whose Origin names another
        # site (cross-site gate in etc/apache2.site), and httpx sends none by default.
        self._client = httpx.Client(base_url=f"{scheme}://{cleaned}", timeout=timeout, auth=auth)

    def close(self) -> None:
        self._client.close()

    # -- plumbing -------------------------------------------------------------------------

    def _request(
        self, method: str, path: str, *, allow_404: bool = False, **kw: Any
    ) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kw)
        except httpx.TransportError as exc:
            reason, message = describe_transport_error(self.host, exc)
            log.warning(
                "FPP %s %s on %s failed: %s (%s, errno %s): %s",
                method,
                path,
                self.host,
                reason,
                type(exc).__name__,
                _errno_of(exc),
                exc,
            )
            raise FppError(FPP_UNREACHABLE, message, reason=reason) from exc
        if response.status_code == 404 and allow_404:
            return response
        if response.status_code == 401:
            log.warning("FPP %s %s on %s: HTTP 401 (password)", method, path, self.host)
            if self.has_password:
                raise FppError(
                    FPP_AUTH_REQUIRED,
                    f"FPP at {self.host} did not accept the password. Check the UI password "
                    f"set in FPP and in {PASSWORD_ENV}.",
                    reason="auth_rejected",
                )
            raise FppError(
                FPP_AUTH_REQUIRED,
                f"FPP at {self.host} asks for a password (its UI password is turned on). "
                f"Set {PASSWORD_ENV} to that password before starting FacadeKit, or turn the "
                "UI password off in FPP's settings.",
                reason="auth_required",
            )
        if response.status_code >= 400:
            excerpt = _body_excerpt(response)
            log.warning(
                "FPP %s %s on %s: HTTP %s %s",
                method,
                path,
                self.host,
                response.status_code,
                excerpt,
            )
            detail = f": {excerpt}" if excerpt else ""
            reason = "forbidden" if response.status_code == 403 else "http_status"
            raise FppError(
                FPP_BAD_RESPONSE,
                f"{method} {path} returned {response.status_code}{detail}",
                reason=reason,
            )
        return response

    def _json(self, response: httpx.Response, what: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            ctype = response.headers.get("content-type", "unknown type")
            log.warning("FPP %s on %s: not JSON (%s)", what, self.host, ctype)
            raise FppError(
                FPP_BAD_RESPONSE,
                f"{what}: FPP at {self.host} did not answer with JSON (got {ctype}).",
                reason="not_json",
            ) from exc

    # -- API ------------------------------------------------------------------------------

    def version(self) -> FppVersion:
        response = self._request("GET", "/api/fppd/version")
        data = _json_or_none(response)
        if data is None:
            ctype = response.headers.get("content-type", "unknown type")
            log.warning("FPP version on %s: not JSON (%s)", self.host, ctype)
            raise FppError(
                FPP_BAD_RESPONSE,
                f"{self.host} answered, but not like Falcon Player (got {ctype}, not JSON). "
                "Check that the address is the FPP Pi.",
                reason="not_fpp",
            )
        if not isinstance(data, dict) or not any(
            key in data for key in ("version", "majorVersion", "fppdAPI")
        ):
            raise FppError(
                FPP_BAD_RESPONSE,
                f"{self.host} answered with JSON that is not an FPP version reply. "
                "Check that the address is the FPP Pi.",
                reason="not_fpp",
            )
        version = str(data.get("version", ""))
        major = _to_int(data.get("majorVersion"))
        minor = _to_int(data.get("minorVersion"))
        if major is None:
            match = _VERSION_RE.search(version)
            if match is None:
                raise FppError(
                    FPP_BAD_RESPONSE,
                    f"FPP at {self.host} reports version {version or 'unknown'!r}, "
                    "which has no version number.",
                    reason="bad_version",
                )
            major, minor = int(match.group(1)), int(match.group(2))
        return FppVersion(version, major, minor or 0, str(data.get("branch", "")))

    def system_info(self) -> dict[str, Any]:
        data = self._json(self._request("GET", "/api/system/info"), "system info")
        if not isinstance(data, dict):
            raise FppError(
                FPP_BAD_RESPONSE, "system info: unexpected response shape", reason="shape"
            )
        return data

    def list_files(self, dir_name: str) -> list[str]:
        data = self._json(self._request("GET", f"/api/files/{_seg(dir_name)}"), "list files")
        if isinstance(data, dict) and "files" not in data:
            # FPP answers an unknown directory with 200 {"status": "Invalid Directory"} [V]
            status = data.get("status") or "no file list"
            raise FppError(
                FPP_BAD_RESPONSE,
                f"FPP has no folder called {dir_name!r} ({status}). "
                "Check the fpp.video_dir / fpp.sequences_dir settings.",
                reason="bad_directory",
            )
        items: Any = data.get("files", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise FppError(
                FPP_BAD_RESPONSE, "list files: unexpected response shape", reason="shape"
            )
        names: list[str] = []
        for item in items:
            if isinstance(item, str):
                names.append(item)
            elif (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and not item.get("isDirectory")
            ):
                names.append(item["name"])
        return names

    def upload(
        self,
        dir_name: str,
        local_path: Path,
        remote_name: str,
        progress: Callable[[float, str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> None:
        # FPP refuses names containing "..", "/" or "\" [V]
        if not _NAME_RE.match(remote_name) or ".." in remote_name:
            raise FkError("BAD_REQUEST", f"Invalid remote file name: {remote_name!r}")
        try:
            total = local_path.stat().st_size
        except OSError as exc:
            raise FkError("BAD_REQUEST", f"Cannot read {local_path}") from exc
        if total == 0:
            raise FkError("BAD_REQUEST", "Cannot upload an empty file")
        path = f"/api/file/{_seg(dir_name)}"
        try:
            # FPP answers this POST with an upload id as plain text; it is not needed [V].
            self._request("POST", path, timeout=UPLOAD_TIMEOUT)
            sent = 0
            with local_path.open("rb") as handle:
                while sent < total:
                    if cancel is not None and cancel():
                        raise FkError("CANCELLED", "Upload cancelled")
                    chunk = handle.read(CHUNK_SIZE)
                    if not chunk:
                        raise FkError(FPP_UPLOAD_FAILED, "Local file shrank during upload")
                    last = sent + len(chunk) >= total
                    timeout = UPLOAD_TIMEOUT + (total / ASSEMBLE_BYTES_PER_S if last else 0.0)
                    response = self._request(
                        "PATCH",
                        path,
                        content=chunk,
                        headers={
                            "Upload-Name": remote_name,
                            "Upload-Offset": str(sent),
                            "Upload-Length": str(total),
                            "Content-Type": "application/offset+octet-stream",
                        },
                        timeout=timeout,
                    )
                    sent += len(chunk)
                    if last:
                        self._check_upload_done(response, total)
                    if progress is not None:
                        progress(sent / total, "Uploading")
        except FppError as exc:
            if exc.code in (FPP_UPLOAD_FAILED, FPP_AUTH_REQUIRED):
                raise
            raise FppError(FPP_UPLOAD_FAILED, exc.message, reason=exc.reason) from exc
        except FkError as exc:
            if exc.code in ("CANCELLED", FPP_UPLOAD_FAILED):
                raise
            raise FkError(FPP_UPLOAD_FAILED, exc.message) from exc
        except OSError as exc:
            raise FkError(FPP_UPLOAD_FAILED, f"Upload failed: {exc}") from exc

    def _check_upload_done(self, response: httpx.Response, total: int) -> None:
        """The last chunk's reply is ``{"status": "OK", ..., "size": <total>}`` [V]."""
        data = _json_or_none(response)
        if not isinstance(data, dict):
            return  # tolerated: nothing to check
        error = _status_error(data)
        if error is not None:
            raise FppError(
                FPP_UPLOAD_FAILED, f"FPP could not store the file: {error}", reason="upload"
            )
        size = _to_int(data.get("size"))
        if size is not None and size != total:
            raise FppError(
                FPP_UPLOAD_FAILED,
                f"FPP stored {size} of {total} bytes. Check free space on the Pi.",
                reason="upload",
            )

    def delete_file(self, dir_name: str, name: str) -> None:
        response = self._request("DELETE", f"/api/file/{_seg(dir_name)}/{_seg(name)}")
        error = _status_error(_json_or_none(response))
        if error is not None:
            raise FppError(
                FPP_BAD_RESPONSE, f"delete {dir_name}/{name}: {error}", reason="bad_status"
            )

    def media_duration(self, name: str) -> float | None:
        response = self._request("GET", f"/api/media/{_seg(name)}/duration", allow_404=True)
        if response.status_code == 404:
            return None
        data = self._json(response, "media duration")
        value: Any = None
        if isinstance(data, dict):
            inner = data.get(name)
            if isinstance(inner, dict) and "duration" in inner:
                value = inner["duration"]
            else:
                value = data.get("duration")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def put_playlist(self, name: str, entries: list[dict[str, Any]]) -> None:
        body = {
            "name": name,
            "mainPlaylist": entries,
            "playlistInfo": {"total_duration": 0, "total_items": len(entries)},
        }
        response = self._request("POST", f"/api/playlist/{_seg(name)}", json=body)
        # A failed write still answers 200, with "Status": "Error" in the body [V]
        error = _status_error(_json_or_none(response))
        if error is not None:
            raise FppError(
                FPP_BAD_RESPONSE,
                f"FPP could not save playlist {name!r}: {error}",
                reason="bad_status",
            )

    def get_playlist(self, name: str) -> dict[str, Any] | None:
        response = self._request("GET", f"/api/playlist/{_seg(name)}", allow_404=True)
        if response.status_code == 404:
            return None
        data = self._json(response, "get playlist")
        if not isinstance(data, dict):
            raise FppError(
                FPP_BAD_RESPONSE, "get playlist: unexpected response shape", reason="shape"
            )
        return data

    def list_playlists(self) -> list[str]:
        data = self._json(self._request("GET", "/api/playlists"), "list playlists")
        if not isinstance(data, list):
            raise FppError(
                FPP_BAD_RESPONSE, "list playlists: unexpected response shape", reason="shape"
            )
        return [item for item in data if isinstance(item, str)]

    def delete_playlist(self, name: str) -> None:
        response = self._request("DELETE", f"/api/playlist/{_seg(name)}")
        error = _status_error(_json_or_none(response))
        if error is not None:
            raise FppError(
                FPP_BAD_RESPONSE, f"delete playlist {name!r}: {error}", reason="bad_status"
            )

    def start_playlist(self, name: str, *, confirm: bool = True) -> None:
        """Start a playlist; with ``confirm``, wait until FPP's status names it.

        FPP answers the start with plain text ("Playlist Starting") and HTTP 200 even when
        the playlist does not exist [V], so the reply alone proves nothing.
        """
        self._request("POST", f"/api/playlist/{_seg(name)}/start")
        if not confirm:
            return
        deadline = time.monotonic() + START_CONFIRM_S
        while True:
            if self.status()["current_playlist"] == name:
                return
            if time.monotonic() >= deadline:
                raise FppError(
                    FPP_BAD_RESPONSE,
                    f"FPP accepted the start of playlist {name!r} but is not playing it. "
                    "Check the playlist and the video file in FPP.",
                    reason="not_started",
                )
            time.sleep(0.1)

    def stop(self) -> None:
        self._request("POST", "/api/playlists/stop")

    def raw_status(self) -> dict[str, Any]:
        data = self._json(self._request("GET", "/api/fppd/status"), "status")
        return data if isinstance(data, dict) else {}

    def status(self) -> dict[str, Any]:
        data = self.raw_status()
        status_name = data.get("status_name")
        current = data.get("current_playlist")
        playlist = current.get("playlist") if isinstance(current, dict) else None
        seconds: float | None
        try:
            seconds = float(data.get("seconds_played"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            seconds = None
        return {
            "status_name": status_name if isinstance(status_name, str) else None,
            "current_playlist": playlist if isinstance(playlist, str) and playlist else None,
            "seconds_played": seconds,
        }

    def get_raw(self, path: str) -> httpx.Response:
        """GET any API path and return the response (404 allowed). For the S-1 recorder."""
        return self._request("GET", path, allow_404=True)
