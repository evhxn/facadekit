from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from facadekit import __version__
from facadekit.api.deps import AppState, get_state
from facadekit.api.routes_render import render_file, render_kind
from facadekit.errors import BAD_REQUEST, FPP_UNREACHABLE, REVISION_CONFLICT, FkError
from facadekit.fpp.client import FppClient
from facadekit.fpp.plugin_client import (
    FppPluginClient,
    PluginError,
    plugin_summary,
    store_token,
    valid_token_shape,
)
from facadekit.fpp.publish import probe_fpp, publish_render, sequences_dir
from facadekit.models.jobs import utc_now_iso

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]

_BAD_HOST = re.compile(r"[/?#@\s]")


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    render_id: str
    playlist_name: str | None = None
    start: bool
    sequence_name: str | None = None
    power_on: bool = False


class PluginPairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    token: str


class PluginPowerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    action: str
    wait: bool = False


def check_host(host: str) -> str:
    if not host or _BAD_HOST.search(host):
        raise FkError(BAD_REQUEST, "host must be a plain address like 192.168.1.50 or fpp.local")
    return host


@router.post("/fpp/probe")
def probe(body: ProbeRequest) -> dict[str, Any]:
    return probe_fpp(check_host(body.host))


@router.get("/fpp/status")
def fpp_status(host: str) -> dict[str, Any]:
    check_host(host)
    client: FppClient | None = None
    try:
        client = FppClient(host, timeout=3)
        return client.status()
    except FkError:
        return {"status_name": None, "current_playlist": None, "seconds_played": None}
    finally:
        if client is not None:
            client.close()


@router.get("/fpp/sequences")
def fpp_sequences(host: str) -> dict[str, list[str]]:
    check_host(host)
    client: FppClient | None = None
    try:
        client = FppClient(host, timeout=3)
        names = client.list_files(sequences_dir())
    except FkError:
        return {"sequences": []}
    finally:
        if client is not None:
            client.close()
    fseq = [name for name in names if name.lower().endswith(".fseq")]
    return {"sequences": sorted(fseq, key=str.lower)}


@router.post("/projects/{pid}/publish", status_code=202)
def publish(pid: str, body: PublishRequest, state: State) -> dict[str, str]:
    host = check_host(body.host)
    project_dir = state.store.project_dir(pid)
    path = render_file(project_dir, body.render_id)
    project = state.store.load(pid)
    align = render_kind(project_dir, body.render_id) == "alignment_check"
    media_name = f"fk_{pid}_align.mp4" if align else f"fk_{pid}.mp4"
    default_playlist = f"FacadeKit - {project.name}" + (" (alignment)" if align else "")
    playlist_name = body.playlist_name or default_playlist

    manifest = _show_manifest(state, pid, align)

    def run(progress: Any, cancel: Any) -> dict[str, Any]:
        result = publish_render(
            host,
            path,
            media_name=media_name,
            playlist_name=playlist_name,
            start=body.start,
            sequence_name=body.sequence_name,
            progress=progress,
            cancel=cancel,
            manifest=manifest,
            power_on=body.power_on,
        )
        _remember_target(state, pid, host, result["playlist_name"])
        return result

    job = state.jobs.submit("publish", run)
    return {"job_id": job.id}


def _remember_target(state: AppState, pid: str, host: str, playlist_name: str) -> None:
    for attempt in (0, 1):
        project = state.store.load(pid)
        project.fpp.host = host
        project.fpp.playlist_name = playlist_name
        try:
            saved = state.store.save(project)
        except FkError as exc:
            if exc.code == REVISION_CONFLICT and attempt == 0:
                continue
            raise
        events = getattr(state, "events", None)
        if events is not None:
            events.publish({"type": "project", "project_id": pid, "revision": saved.revision})
        return


def _show_manifest(state: AppState, pid: str, align: bool) -> dict[str, Any]:
    """What the FPP plugin records about a published show (playlist and video are added later)."""
    from datetime import UTC, datetime

    from facadekit.calib.pipeline import load_calibration

    project = state.store.load(pid)
    manifest: dict[str, Any] = {
        "name": project.name + (" (alignment)" if align else ""),
        "project": project.name,
        "project_id": pid,
        "published_at": utc_now_iso(),
        "facadekit_version": __version__,
        "calibration_id": project.calibration_id,
        "calibrated_at": None,
        "calibration_age_days": None,
    }
    if project.calibration_id:
        try:
            calibration = load_calibration(state.store.project_dir(pid), project.calibration_id)
            made = datetime.fromisoformat(calibration.created_at.replace("Z", "+00:00"))
            age = (datetime.now(UTC) - made).total_seconds() / 86400
            manifest.update(
                calibrated_at=calibration.created_at, calibration_age_days=round(age, 2)
            )
        except (FkError, OSError, ValueError):
            pass
    return manifest


# -- FacadeKit FPP plugin (optional; docs/20-fpp-plugin.md) -----------------------------


@router.get("/fpp/plugin")
def fpp_plugin(host: str) -> dict[str, Any]:
    return plugin_summary(check_host(host))


@router.post("/fpp/plugin/pair")
def fpp_plugin_pair(body: PluginPairRequest) -> dict[str, Any]:
    host = check_host(body.host)
    token = body.token.strip()
    if not valid_token_shape(token):
        raise FkError(
            BAD_REQUEST, "That does not look like a pairing token. Copy it again from FPP."
        )
    summary = plugin_summary(host, token=token)
    if summary["paired"]:
        store_token(host, token)
        summary["token_stored"] = True
    return summary


@router.delete("/fpp/plugin/pair")
def fpp_plugin_unpair(host: str) -> dict[str, Any]:
    store_token(check_host(host), None)
    return plugin_summary(host)


@router.post("/fpp/plugin/projectors")
def fpp_plugin_projectors(body: PluginPowerRequest) -> dict[str, Any]:
    host = check_host(body.host)
    if body.action not in ("on", "off"):
        raise FkError(BAD_REQUEST, "action must be on or off")
    try:
        with FppPluginClient(host) as client:
            return client.power(body.action, wait=body.wait)
    except PluginError as exc:
        code = FPP_UNREACHABLE if exc.reason == "unreachable" else exc.code
        raise FkError(code, exc.message) from exc
