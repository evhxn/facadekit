"""Fake Falcon Player (FPP) REST server for tests.

Implements the subset of FPP's API that FacadeKit uses (docs/05-interfaces.md section 7),
following the FPP 10.4 source (tag ``10.4``, commit 123b8ca; docs/17-fpp-integration-audit.md):
string ``majorVersion``/``minorVersion``, directory keys matched without regard to case,
``200 {"status": "Invalid Directory"}`` for an unknown directory, a plain-text upload id from
``POST /api/file/{dir}``, ``{"status": "OK", "file", "dir", "size"}`` from each ``PATCH``,
playlists saved as sent plus ``version`` 4 and recomputed ``playlistInfo``, plain-text
replies from start/stop (a missing playlist still answers 200 and nothing plays), HTTP Basic
auth when a UI password is set, and the cross-site gate that refuses a non-GET request whose
``Origin`` names another site.

Uploads and playlists are stored under a state directory. This module imports nothing
from ``facadekit`` so that it works standalone.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

# Directory keys FPP accepts (GetDirSetting in www/config.php), mapped to folders on disk.
DIR_KEYS: dict[str, str] = {
    "sequences": "sequences",
    "music": "music",
    "videos": "videos",
    "images": "images",
    "effects": "effects",
    "scripts": "scripts",
    "logs": "logs",
    "uploads": "upload",
    "upload": "upload",
    "docs": "docs",
    "playlists": "playlists",
}
# Directories that FPP searches when resolving a media name for duration lookups.
MEDIA_DIRS: tuple[str, ...] = ("videos", "music")
PLAYLIST_DIR = "playlists"
_PARTIAL_PREFIX = ".partial_"
FAKE_USERS = ("admin", "fpp")


def _is_safe_name(name: str) -> bool:
    """FPP's upload rule: no "..", "/" or "\\" (PatchFile in files.php)."""
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


def _is_playlist_name(name: str) -> bool:
    """FPP's playlist rule: not empty, no "/", "\\" or NUL (PlaylistFilePath)."""
    return bool(name) and not any(c in name for c in "/\\\0") and name not in (".", "..")


def _json(content: dict[str, Any] | list[Any], status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status)


def _parse_version(version: str) -> tuple[str, str]:
    parts = version.split("-")[0].split(".")
    return parts[0], parts[1] if len(parts) > 1 else "0"


def _human(size: int) -> str:
    return f"{size / 1024 / 1024:.2f}MB" if size >= 1024 * 1024 else f"{size / 1024:.2f}KB"


def create_fake_fpp(
    state_dir: Path, *, version: str = "10.4", password: str | None = None
) -> FastAPI:
    """Create a FastAPI app that imitates FPP, storing its state under ``state_dir``.

    ``app.state.playing`` holds the name of the playlist playing (or None),
    ``app.state.requests`` lists every handled request as ``"METHOD /path"`` and
    ``app.state.headers`` their headers. With ``password`` set, every request needs HTTP
    Basic auth as user ``admin`` or ``fpp``.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    playlist_dir = state_dir / PLAYLIST_DIR
    major, minor = _parse_version(version)

    app = FastAPI(title="FakeFPP", version=version)
    app.state.playing = None
    app.state.requests = []
    app.state.headers = []

    def _dir(key: str) -> Path | None:
        folder = DIR_KEYS.get(key.lower())
        return None if folder is None else state_dir / folder

    @app.middleware("http")
    async def _gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        app.state.requests.append(f"{request.method} {request.url.path}")
        app.state.headers.append(dict(request.headers))
        if password is not None:
            expected = {
                "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
                for user in FAKE_USERS
            }
            if request.headers.get("authorization") not in expected:
                return PlainTextResponse(
                    "Unauthorized",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Falcon Player"'},
                )
        origin = request.headers.get("origin", "")
        host = request.headers.get("host", "")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and origin not in (f"http://{host}", f"https://{host}")
        ):
            return PlainTextResponse("Forbidden", status_code=403)
        return await call_next(request)

    @app.get("/api/fppd/version")
    async def fppd_version() -> JSONResponse:
        return _json(
            {
                "Message": "",
                "Status": "OK",
                "branch": f"v{version}",
                "fppdAPI": "v1",
                "majorVersion": major,
                "minorVersion": minor,
                "respCode": 200,
                "version": version,
            }
        )

    @app.get("/api/system/info")
    async def system_info() -> JSONResponse:
        return _json(
            {
                "HostName": "FakeFPP",
                "HostDescription": "",
                "Platform": "Raspberry Pi",
                "Variant": "Pi 3 Model B",
                "Mode": "player",
                "Version": version,
                "Branch": f"v{version}",
                "majorVersion": int(major),
                "minorVersion": int(minor) if minor.isdigit() else 0,
                "uuid": "M1-FAKE0000",
                "IPs": ["192.168.1.50"],
            }
        )

    @app.get("/api/files/{dir_name}")
    async def list_files(dir_name: str, request: Request) -> Response:
        directory = _dir(dir_name)
        if directory is None:
            return _json({"status": "Invalid Directory"})
        names: list[str] = []
        files: list[dict[str, Any]] = []
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.name.startswith(_PARTIAL_PREFIX):
                    continue
                if path.is_dir():
                    files.append(
                        {
                            "name": path.name,
                            "mtime": "11/23/26  07:00 PM",
                            "sizeBytes": 0,
                            "sizeHuman": "Directory",
                            "isDirectory": True,
                        }
                    )
                    continue
                size = path.stat().st_size
                names.append(path.name)
                entry: dict[str, Any] = {
                    "name": path.name,
                    "mtime": "11/23/26  07:00 PM",
                    "sizeBytes": size,
                    "sizeHuman": _human(size),
                }
                if directory.name in MEDIA_DIRS:
                    entry["playtimeSeconds"] = None
                files.append(entry)
        if request.query_params.get("nameOnly") == "1":
            return _json(names)
        return _json({"status": "ok", "files": files})

    @app.post("/api/file/{dir_name}")
    async def upload_start(dir_name: str) -> Response:
        # FPP returns PHP uniqid("", true) as the whole body.
        return PlainTextResponse(f"{uuid.uuid4().hex[:14]}.{int(time.time())}")

    @app.patch("/api/file/{dir_name}")
    async def upload_chunk(dir_name: str, request: Request) -> JSONResponse:
        upload_name = request.headers.get("Upload-Name", "")
        offset_s = request.headers.get("Upload-Offset", "")
        length_s = request.headers.get("Upload-Length", "")
        if not upload_name or not offset_s.isdigit() or not length_s.isdigit():
            return _json(
                {"status": "Missing or invalid Upload-Name, Upload-Offset or Upload-Length"}, 400
            )
        directory = _dir(dir_name)
        if directory is None:
            return _json({"status": "Invalid Directory"}, 400)
        if not _is_safe_name(upload_name):
            return _json({"status": "Invalid filename"}, 400)
        offset, length = int(offset_s), int(length_s)
        body = await request.body()

        directory.mkdir(parents=True, exist_ok=True)
        partial = directory / f"{_PARTIAL_PREFIX}{upload_name}"
        if offset == 0:
            partial.unlink(missing_ok=True)
        elif not partial.exists():
            return _json(
                {
                    "status": "failed",
                    "file": upload_name,
                    "dir": dir_name,
                    "error": "Upload is no longer in progress, please upload the file again",
                },
                409,
            )
        with partial.open("r+b" if partial.exists() else "wb") as handle:
            handle.seek(offset)
            handle.write(body)
        size = partial.stat().st_size
        if size >= length:
            partial.replace(directory / upload_name)
        return _json({"status": "OK", "file": upload_name, "dir": dir_name, "size": size})

    @app.delete("/api/file/{dir_name}/{name}")
    async def delete_file(dir_name: str, name: str) -> JSONResponse:
        directory = _dir(dir_name)
        if directory is None:
            return _json({"status": "Invalid Directory", "file": name, "dir": dir_name}, 400)
        target = directory / name
        if not _is_safe_name(name) or not target.is_file():
            return _json({"status": "File Not Found", "file": name, "dir": dir_name}, 404)
        target.unlink()
        return _json({"status": "OK", "file": name, "dir": dir_name})

    @app.get("/api/media/{name}/duration")
    async def media_duration(name: str) -> Response:
        if _is_safe_name(name) and any(
            (state_dir / media_dir / name).is_file() for media_dir in MEDIA_DIRS
        ):
            return _json({name: {"duration": 60.0}})
        return PlainTextResponse(f"Not found: {name}", status_code=404)

    @app.post("/api/playlist/{name}")
    async def save_playlist(name: str, request: Request) -> JSONResponse:
        if not _is_playlist_name(name):
            return _json({"Status": "Error", "Message": "Invalid playlist name."}, 400)
        payload: Any = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                payload = json.loads(await request.body())
            except ValueError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["version"] = 4
        info = payload.get("playlistInfo")
        info = dict(info) if isinstance(info, dict) else {}
        main = payload.get("mainPlaylist")
        count = len(main) if isinstance(main, list) else 0
        info.update(
            leadIn_items=0,
            leadIn_duration=0,
            mainPlaylist_items=count,
            mainPlaylist_duration=0,
            leadOut_items=0,
            leadOut_duration=0,
            total_items=count,
            total_duration=0,
        )
        payload["playlistInfo"] = info
        playlist_dir.mkdir(parents=True, exist_ok=True)
        (playlist_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        return _json(payload)

    @app.get("/api/playlist/{name}")
    async def get_playlist(name: str) -> JSONResponse:
        path = playlist_dir / f"{name}.json"
        if not _is_playlist_name(name) or not path.is_file():
            return _json({"Status": "Error", "Message": f"Playlist not found: {name}"}, 404)
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @app.delete("/api/playlist/{name}")
    async def delete_playlist(name: str) -> JSONResponse:
        if not _is_playlist_name(name):
            return _json({"Status": "Error", "Message": "Invalid playlist name."}, 400)
        path = playlist_dir / f"{name}.json"
        if not path.is_file():
            return _json({"Status": "Error", "Message": f"Playlist '{name}' does not exist."})
        path.unlink()
        return _json({"Status": "OK", "Message": ""})

    @app.get("/api/playlists")
    async def list_playlists() -> JSONResponse:
        names: list[str] = []
        if playlist_dir.is_dir():
            names = sorted(path.stem for path in playlist_dir.glob("*.json"))
        return JSONResponse(names)

    @app.post("/api/playlist/{name}/start")
    async def start_playlist(name: str) -> Response:
        # fppd's "Start Playlist" command answers "Playlist Starting" whether or not the
        # playlist exists; a missing one simply does not play.
        if _is_playlist_name(name) and (playlist_dir / f"{name}.json").is_file():
            app.state.playing = name
        return PlainTextResponse("Playlist Starting")

    @app.post("/api/playlists/stop")
    async def stop_playlists() -> Response:
        app.state.playing = None
        return PlainTextResponse("Stopping")

    @app.get("/api/fppd/status")
    async def fppd_status() -> JSONResponse:
        playing: str | None = app.state.playing
        return _json(
            {
                "fppd": "running",
                "mode_name": "player",
                "status": 1 if playing is not None else 0,
                "status_name": "playing" if playing is not None else "idle",
                "version": version,
                "current_playlist": {
                    "playlist": playing or "",
                    "count": "1" if playing else "0",
                    "index": "1" if playing else "0",
                    "type": "",
                },
                "seconds_played": "0",
                "seconds_remaining": "0",
            }
        )

    return app
