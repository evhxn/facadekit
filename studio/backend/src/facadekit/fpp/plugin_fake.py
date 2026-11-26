"""Fake FacadeKit FPP plugin routes, added to the fake FPP (``fpp/fake.py``) for tests.

Mirrors ``integrations/fpp-plugin/lib/api_core.php``: the same routes, token rule
(``X-FacadeKit-Token`` or ``Authorization: Bearer``; 403 ``not_paired`` before a token
exists, 401 ``bad_token`` for a wrong one; ``version`` and ``projector-names`` open),
manifest validation and answer shapes. Projectors are simulated in memory and switch at
once, like a laser projector. Imports nothing from ``facadekit`` so that it works standalone.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PREFIX = "/api/plugin/fpp-plugin-FacadeKit"
PLUGIN_VERSION = "0.1.0"
_MEDIA_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class FakePluginState:
    token: str | None = None
    projectors: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": "p1", "name": "Left", "host": "192.168.50.10", "power": "standby"},
            {"id": "p2", "name": "Right", "host": "192.168.50.11", "power": "standby"},
        ]
    )
    shows: list[dict[str, Any]] = field(default_factory=list)
    healthy: bool = True
    fault: str | None = None
    calls: list[str] = field(default_factory=list)
    unreachable: set[str] = field(default_factory=set)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"code": code, "message": message}}, status)


def _snapshot(p: dict[str, Any], unreachable: set[str]) -> dict[str, Any]:
    if p["id"] in unreachable:
        return {"id": p["id"], "name": p["name"], "host": p["host"], "ok": False,
                "power": "unknown", "error": "no answer", "error_kind": "timeout",
                "checked_at": _now()}  # fmt: skip
    return {
        "id": p["id"], "name": p["name"], "host": p["host"], "ok": True, "power": p["power"],
        "input": "31" if p["power"] == "on" else None,
        "errors": {k: "ok" for k in ("fan", "lamp", "temperature", "cover", "filter", "other")},
        "lamps": [{"hours": 1234, "on": p["power"] == "on"}], "manufacturer": "Panasonic",
        "model": "PT-RZ570", "pjlink_class": "1", "error": None, "error_kind": None,
        "checked_at": _now(),
    }  # fmt: skip


def _valid_manifest(body: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(body, dict):
        return None, "body must be a JSON object"
    playlist, video = body.get("playlist"), body.get("video")
    if not isinstance(playlist, str) or not playlist or any(c in playlist for c in "/\\\0"):
        return None, "playlist must be an FPP playlist name (no / or \\)"
    if not isinstance(video, str) or not _MEDIA_RE.match(video) or ".." in video:
        return None, 'video must be a file name of letters, digits, ".", "_" or "-"'
    age = body.get("calibration_age_days")
    if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float))):
        return None, "calibration_age_days must be a number"
    keys = ("name", "project", "project_id", "published_at", "calibration_id",
            "calibrated_at", "facadekit_version", "sequence")  # fmt: skip
    out: dict[str, Any] = {k: body.get(k) for k in keys}
    for key in keys:
        if out[key] is not None and not isinstance(out[key], str):
            return None, f"{key} must be a short text"
    out.update(
        name=out["name"] or playlist,
        playlist=playlist,
        video=video,
        calibration_age_days=age,
        published_at=out["published_at"] or _now(),
        registered_at=_now(),
    )
    return out, None


def add_fake_plugin(app: FastAPI, state: FakePluginState) -> None:
    """Add the plugin's routes to ``app``; ``app.state.plugin`` is ``state``."""
    app.state.plugin = state

    def _authorized(request: Request) -> JSONResponse | None:
        if state.token is None:
            return _error(403, "not_paired", "Create a pairing token on the plugin page first.")
        presented = request.headers.get("x-facadekit-token", "")
        bearer = request.headers.get("authorization", "")
        if not presented and bearer.lower().startswith("bearer "):
            presented = bearer[7:].strip()
        good = hashlib.sha256(state.token.encode()).hexdigest()
        if not hmac.compare_digest(good, hashlib.sha256(presented.encode()).hexdigest()):
            return _error(401, "bad_token", "Missing or wrong pairing token.")
        return None

    @app.get(f"{PREFIX}/version")
    async def version() -> JSONResponse:
        state.calls.append("version")
        return JSONResponse({"ok": True, "plugin": "fpp-plugin-FacadeKit",
                             "version": PLUGIN_VERSION, "api": 1,
                             "paired": state.token is not None,
                             "configured": bool(state.projectors)})  # fmt: skip

    @app.get(f"{PREFIX}/projector-names")
    async def names() -> JSONResponse:
        return JSONResponse(["all", *[p["name"] for p in state.projectors]])

    @app.get(f"{PREFIX}/status")
    async def status(request: Request) -> JSONResponse:
        state.calls.append("status")
        if (denied := _authorized(request)) is not None:
            return denied
        return JSONResponse({"ok": True, "live": request.query_params.get("cached") != "1",
                             "projectors": [_snapshot(p, state.unreachable)
                                            for p in state.projectors],
                             "health": _health(state), "checked_at": _now()})  # fmt: skip

    @app.get(f"{PREFIX}/health")
    async def health(request: Request) -> JSONResponse:
        state.calls.append("health")
        if (denied := _authorized(request)) is not None:
            return denied
        snaps = [_snapshot(p, state.unreachable) for p in state.projectors]
        summary = {"total": len(snaps), "on": 0, "off": 0, "transition": 0, "error": 0,
                   "unknown": 0}  # fmt: skip
        for snap in snaps:
            if snap["error"]:
                summary["error"] += 1
            elif snap["power"] == "on":
                summary["on"] += 1
            elif snap["power"] == "standby":
                summary["off"] += 1
            else:
                summary["transition"] += 1
        playing = getattr(app.state, "playing", None)
        player = {"status_name": "playing" if playing else "idle", "current_playlist": playing,
                  "seconds_played": 0.0, "mode_name": "player"}  # fmt: skip
        return JSONResponse({
            "ok": True, "plugin": {"name": "fpp-plugin-FacadeKit", "version": PLUGIN_VERSION,
                                   "api": 1},
            "healthy": state.healthy and summary["error"] == 0, "health": _health(state),
            "projectors": snaps, "summary": summary, "player": player, "shows": state.shows,
            "settings": {"auto_on": False, "auto_off": False, "auto_scope": "facadekit",
                         "guard_enabled": False, "guard_stop_playback": False},
            "checked_at": _now(),
        })  # fmt: skip

    async def _power(request: Request, action: str) -> JSONResponse:
        state.calls.append(f"power {action}")
        if (denied := _authorized(request)) is not None:
            return denied
        if not state.projectors:
            return _error(409, "not_configured", "No projectors are set up in the plugin yet.")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            return _error(400, "bad_json", "The body must be a JSON object.")
        timeout = body.get("timeout_s")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 <= timeout <= 900
        ):
            return _error(400, "bad_timeout", "timeout_s must be a whole number from 0 to 900.")
        results = []
        for p in state.projectors:
            if p["id"] in state.unreachable:
                results.append({"id": p["id"], "name": p["name"], "ok": False, "state": "error",
                                "message": "no answer"})  # fmt: skip
                continue
            p["power"] = "on" if action == "on" else "standby"
            results.append({"id": p["id"], "name": p["name"], "ok": True,
                            "state": "on" if action == "on" else "off",
                            "message": action})  # fmt: skip
        wait = bool(body.get("wait"))
        return JSONResponse({"ok": all(r["ok"] for r in results), "action": action,
                             "waited": wait, "results": results})  # fmt: skip

    @app.post(f"{PREFIX}/projectors/on")
    async def power_on(request: Request) -> JSONResponse:
        return await _power(request, "on")

    @app.post(f"{PREFIX}/projectors/off")
    async def power_off(request: Request) -> JSONResponse:
        return await _power(request, "off")

    @app.get(f"{PREFIX}/shows")
    async def shows(request: Request) -> JSONResponse:
        if (denied := _authorized(request)) is not None:
            return denied
        return JSONResponse({"ok": True, "shows": state.shows})

    @app.post(f"{PREFIX}/shows")
    async def register(request: Request) -> JSONResponse:
        state.calls.append("register")
        if (denied := _authorized(request)) is not None:
            return denied
        try:
            body = await request.json()
        except ValueError:
            body = None
        manifest, error = _valid_manifest(body)
        if manifest is None:
            return _error(400, "bad_manifest", error or "bad manifest")
        state.shows = [manifest, *[s for s in state.shows if s["playlist"] != manifest["playlist"]]]
        return JSONResponse({"ok": True, "show": manifest})

    @app.delete(f"{PREFIX}/shows/{{playlist}}")
    async def delete(playlist: str, request: Request) -> JSONResponse:
        if (denied := _authorized(request)) is not None:
            return denied
        before = len(state.shows)
        state.shows = [s for s in state.shows if s["playlist"] != playlist]
        if len(state.shows) == before:
            return _error(404, "not_found", "No show is registered for that playlist.")
        return JSONResponse({"ok": True})


def _health(state: FakePluginState) -> dict[str, Any]:
    return {"healthy": state.healthy, "reason": state.fault, "projector": None, "since": None}
