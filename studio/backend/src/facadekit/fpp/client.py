"""HTTP client for Falcon Player (FPP). The only module that speaks HTTP to FPP.

Response details marked [A] in docs/05-interfaces.md section 7 are unconfirmed, so parsing
is defensive and the directory name is always a caller-supplied argument.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from facadekit.errors import FkError

CHUNK_SIZE = 8 * 1024 * 1024
UPLOAD_TIMEOUT = 120.0
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)")
_MAX_ERR_BODY = 300


@dataclass
class FppVersion:
    version: str
    major: int
    minor: int
    branch: str


def _seg(value: str) -> str:
    return quote(value, safe="")


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FppClient:
    def __init__(self, host: str, *, timeout: float = 10.0) -> None:
        cleaned = host.strip()
        for prefix in ("http://", "https://"):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                break
        cleaned = cleaned.rstrip("/")
        if not cleaned:
            raise FkError("BAD_REQUEST", "FPP host is empty")
        self.host = cleaned
        self._client = httpx.Client(base_url=f"http://{cleaned}", timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # -- plumbing -------------------------------------------------------------------------

    def _request(
        self, method: str, path: str, *, allow_404: bool = False, **kw: Any
    ) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kw)
        except httpx.TransportError as exc:
            raise FkError("FPP_UNREACHABLE", f"Could not reach FPP at {self.host}") from exc
        if response.status_code == 404 and allow_404:
            return response
        if response.status_code >= 400:
            raise FkError("FPP_BAD_RESPONSE", f"{method} {path} returned {response.status_code}")
        return response

    def _json(self, response: httpx.Response, what: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise FkError("FPP_BAD_RESPONSE", f"{what}: response is not JSON") from exc

    # -- API ------------------------------------------------------------------------------

    def version(self) -> FppVersion:
        data = self._json(self._request("GET", "/api/fppd/version"), "version")
        if not isinstance(data, dict):
            raise FkError("FPP_BAD_RESPONSE", "version: unexpected response shape")
        version = str(data.get("version", ""))
        major = _to_int(data.get("majorVersion"))
        minor = _to_int(data.get("minorVersion"))
        if major is None:
            match = _VERSION_RE.search(version)
            if match is None:
                raise FkError("FPP_BAD_RESPONSE", "version: no version number found")
            major, minor = int(match.group(1)), int(match.group(2))
        return FppVersion(version, major, minor or 0, str(data.get("branch", "")))

    def system_info(self) -> dict[str, Any]:
        data = self._json(self._request("GET", "/api/system/info"), "system info")
        if not isinstance(data, dict):
            raise FkError("FPP_BAD_RESPONSE", "system info: unexpected response shape")
        return data

    def list_files(self, dir_name: str) -> list[str]:
        data = self._json(self._request("GET", f"/api/files/{_seg(dir_name)}"), "list files")
        items: Any = data.get("files", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise FkError("FPP_BAD_RESPONSE", "list files: unexpected response shape")
        names: list[str] = []
        for item in items:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
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
        if not _NAME_RE.match(remote_name):
            raise FkError("BAD_REQUEST", f"Invalid remote file name: {remote_name!r}")
        try:
            total = local_path.stat().st_size
        except OSError as exc:
            raise FkError("BAD_REQUEST", f"Cannot read {local_path}") from exc
        if total == 0:
            raise FkError("BAD_REQUEST", "Cannot upload an empty file")
        path = f"/api/file/{_seg(dir_name)}"
        try:
            self._request("POST", path, timeout=UPLOAD_TIMEOUT)
            sent = 0
            with local_path.open("rb") as handle:
                while sent < total:
                    if cancel is not None and cancel():
                        raise FkError("CANCELLED", "Upload cancelled")
                    chunk = handle.read(CHUNK_SIZE)
                    if not chunk:
                        raise FkError("FPP_UPLOAD_FAILED", "Local file shrank during upload")
                    self._request(
                        "PATCH",
                        path,
                        content=chunk,
                        headers={
                            "Upload-Name": remote_name,
                            "Upload-Offset": str(sent),
                            "Upload-Length": str(total),
                            "Content-Type": "application/offset+octet-stream",
                        },
                        timeout=UPLOAD_TIMEOUT,
                    )
                    sent += len(chunk)
                    if progress is not None:
                        progress(sent / total, "Uploading")
        except FkError as exc:
            if exc.code in ("CANCELLED", "FPP_UPLOAD_FAILED"):
                raise
            raise FkError("FPP_UPLOAD_FAILED", exc.message) from exc
        except OSError as exc:
            raise FkError("FPP_UPLOAD_FAILED", f"Upload failed: {exc}") from exc

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
        self._request("POST", f"/api/playlist/{_seg(name)}", json=body)

    def start_playlist(self, name: str) -> None:
        self._request("POST", f"/api/playlist/{_seg(name)}/start")

    def stop(self) -> None:
        self._request("POST", "/api/playlists/stop")

    def status(self) -> dict[str, Any]:
        data = self._json(self._request("GET", "/api/fppd/status"), "status")
        if not isinstance(data, dict):
            data = {}
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
