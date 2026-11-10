"""Fake Falcon Player (FPP) REST server for tests.

Implements the subset of FPP's API that FacadeKit uses (docs/05-interfaces.md section 7).
Uploads and playlists are stored under a state directory. This module imports nothing
from ``facadekit`` so that it works standalone.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

# Directories that FPP searches when resolving a media name for duration lookups.
MEDIA_DIRS: tuple[str, ...] = ("videos", "music")
PLAYLIST_DIR = "playlists"
_PARTIAL_PREFIX = ".partial_"


def _is_safe_name(name: str) -> bool:
    """Reject names that could escape the state directory."""
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


def _json(content: dict[str, Any] | list[Any], status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"status": "error", "message": message}, status_code=status)


def _parse_version(version: str) -> tuple[int, int]:
    parts = version.split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    return major, minor


def create_fake_fpp(state_dir: Path, *, version: str = "10.4") -> FastAPI:
    """Create a FastAPI app that imitates FPP, storing its state under ``state_dir``.

    ``app.state.playing`` holds the name of the playlist started last (or None), and
    ``app.state.requests`` lists every handled request as ``"METHOD /path"``.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    playlist_dir = state_dir / PLAYLIST_DIR
    major, minor = _parse_version(version)

    app = FastAPI(title="FakeFPP", version=version)
    app.state.playing = None
    app.state.requests = []

    @app.middleware("http")
    async def _record_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        app.state.requests.append(f"{request.method} {request.url.path}")
        return await call_next(request)

    @app.get("/api/fppd/version")
    async def fppd_version() -> JSONResponse:
        return _json(
            {
                "version": version,
                "majorVersion": major,
                "minorVersion": minor,
                "branch": f"v{version}",
                "fppdAPI": "v1",
                "Status": "OK",
            }
        )

    @app.get("/api/system/info")
    async def system_info() -> JSONResponse:
        return _json(
            {
                "HostName": "FakeFPP",
                "Platform": "Raspberry Pi",
                "Variant": "Pi 3 Model B",
                "Version": version,
            }
        )

    @app.get("/api/files/{dir_name}")
    async def list_files(dir_name: str) -> JSONResponse:
        if not _is_safe_name(dir_name):
            return _error(400, "invalid directory name")
        directory = state_dir / dir_name
        files: list[dict[str, Any]] = []
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.is_file() and not path.name.startswith(_PARTIAL_PREFIX):
                    files.append({"name": path.name, "sizeBytes": path.stat().st_size})
        return _json({"status": "ok", "files": files})

    @app.post("/api/file/{dir_name}")
    async def upload_start(dir_name: str) -> JSONResponse:
        if not _is_safe_name(dir_name):
            return _error(400, "invalid directory name")
        directory = state_dir / dir_name
        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.iterdir():
            if stale.name.startswith(_PARTIAL_PREFIX):
                stale.unlink()
        return _json({"status": "ok"})

    @app.patch("/api/file/{dir_name}")
    async def upload_chunk(dir_name: str, request: Request) -> JSONResponse:
        upload_name = request.headers.get("Upload-Name", "")
        if not _is_safe_name(dir_name) or not _is_safe_name(upload_name):
            return _error(400, "invalid directory or file name")
        try:
            offset = int(request.headers.get("Upload-Offset", ""))
            length = int(request.headers.get("Upload-Length", ""))
        except ValueError:
            return _error(400, "Upload-Offset and Upload-Length must be integers")
        body = await request.body()

        directory = state_dir / dir_name
        directory.mkdir(parents=True, exist_ok=True)
        partial = directory / f"{_PARTIAL_PREFIX}{upload_name}"
        current = partial.stat().st_size if partial.exists() else 0
        if offset != current:
            return _error(409, f"Upload-Offset {offset} does not match current size {current}")
        new_size = current + len(body)
        if new_size > length:
            return _error(400, "chunk runs past Upload-Length")
        with partial.open("ab") as handle:
            handle.write(body)
        if new_size == length:
            partial.replace(directory / upload_name)
        return _json({"status": "ok"})

    @app.get("/api/media/{name}/duration")
    async def media_duration(name: str) -> JSONResponse:
        if not _is_safe_name(name):
            return _error(400, "invalid media name")
        if any((state_dir / media_dir / name).is_file() for media_dir in MEDIA_DIRS):
            return _json({name: {"duration": 60.0}})
        return _error(404, f"media {name} not found")

    @app.post("/api/playlist/{name}")
    async def save_playlist(name: str, request: Request) -> JSONResponse:
        if not _is_safe_name(name):
            return _error(400, "invalid playlist name")
        try:
            payload = json.loads(await request.body())
        except ValueError:
            return _error(400, "body is not valid JSON")
        if not isinstance(payload, dict) or "mainPlaylist" not in payload:
            return _error(400, "playlist must contain mainPlaylist")
        playlist_dir.mkdir(parents=True, exist_ok=True)
        (playlist_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        return _json({"status": "ok"})

    @app.get("/api/playlist/{name}")
    async def get_playlist(name: str) -> JSONResponse:
        if not _is_safe_name(name):
            return _error(400, "invalid playlist name")
        path = playlist_dir / f"{name}.json"
        if not path.is_file():
            return _error(404, f"playlist {name} not found")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/playlists")
    async def list_playlists() -> JSONResponse:
        names: list[str] = []
        if playlist_dir.is_dir():
            names = sorted(path.stem for path in playlist_dir.glob("*.json"))
        return JSONResponse(names)

    @app.post("/api/playlist/{name}/start")
    async def start_playlist(name: str) -> JSONResponse:
        if not _is_safe_name(name):
            return _error(400, "invalid playlist name")
        if not (playlist_dir / f"{name}.json").is_file():
            return _error(404, f"playlist {name} not found")
        app.state.playing = name
        return _json({"status": "ok"})

    @app.post("/api/playlists/stop")
    async def stop_playlists() -> JSONResponse:
        app.state.playing = None
        return _json({"status": "ok"})

    @app.get("/api/fppd/status")
    async def fppd_status() -> JSONResponse:
        playing: str | None = app.state.playing
        return _json(
            {
                "status_name": "playing" if playing is not None else "idle",
                "current_playlist": {"playlist": playing or ""},
                "seconds_played": "0",
            }
        )

    return app
