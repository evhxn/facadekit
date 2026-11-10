from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from facadekit.api.deps import AppState, get_state
from facadekit.errors import ASSET_IN_USE, NOT_FOUND, REVISION_CONFLICT, FkError
from facadekit.ids import new_id
from facadekit.media.probe import make_thumbnail, probe
from facadekit.models.project import Asset, Project

log = logging.getLogger(__name__)

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]

CHUNK = 1024 * 1024
_AID = re.compile(r"^ast_[0-9a-f]{8}$")
_EXT = re.compile(r"^[a-z0-9]{1,5}$")


def _ext_of(name: str) -> str:
    ext = PurePosixPath(name.replace("\\", "/")).suffix.lstrip(".").lower()
    return ext if _EXT.fullmatch(ext) else "bin"


def _basename(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).name[:200]


def _find_asset(project: Project, aid: str) -> Asset:
    if not _AID.fullmatch(aid):
        raise FkError(NOT_FOUND, f"no such asset: {aid}")
    for asset in project.assets:
        if asset.id == aid:
            return asset
    raise FkError(NOT_FOUND, f"no such asset: {aid}")


def _thumb_path(project_dir: Path, aid: str) -> Path:
    return project_dir / "media" / f"{aid}.thumb.jpg"


def _write_chunk(handle: object, chunk: bytes) -> None:
    handle.write(chunk)  # type: ignore[attr-defined]


@router.post("/projects/{pid}/assets", status_code=201)
async def upload_asset(
    pid: str, state: State, file: Annotated[UploadFile, File()]
) -> dict[str, Asset | Project]:
    project_dir = state.store.project_dir(pid)
    media_dir = project_dir / "media"
    media_dir.mkdir(exist_ok=True)
    aid = new_id("ast")
    ext = _ext_of(file.filename or "")
    final = media_dir / f"{aid}.{ext}"
    part = media_dir / f"{aid}.{ext}.part"
    thumb = _thumb_path(project_dir, aid)
    try:
        with part.open("wb") as handle:
            while chunk := await file.read(CHUNK):
                await run_in_threadpool(_write_chunk, handle, chunk)
        part.replace(final)
        asset = await run_in_threadpool(probe, final)
    except BaseException:
        part.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
    asset.id = aid
    asset.filename = f"media/{aid}.{ext}"
    asset.original_name = _basename(file.filename or "")
    if asset.kind in ("video", "image"):
        try:
            await run_in_threadpool(make_thumbnail, final, asset.kind, thumb)
        except Exception:
            log.warning("thumbnail failed for %s", final, exc_info=True)
            thumb.unlink(missing_ok=True)

    def attach() -> Project:
        for attempt in (0, 1):
            project = state.store.load(pid)
            project.assets.append(asset)
            try:
                return state.store.save(project)
            except FkError as exc:
                if exc.code != REVISION_CONFLICT or attempt == 1:
                    raise
        raise AssertionError("unreachable")

    try:
        saved = await run_in_threadpool(attach)
    except BaseException:
        final.unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)
        raise
    return {"asset": asset, "project": saved}


@router.get("/projects/{pid}/assets/{aid}/file")
def get_asset_file(pid: str, aid: str, state: State) -> FileResponse:
    project = state.store.load(pid)
    asset = _find_asset(project, aid)
    path = state.store.project_dir(pid) / asset.filename
    if not path.is_file():
        raise FkError(NOT_FOUND, f"asset file missing: {aid}")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.get("/projects/{pid}/assets/{aid}/thumb")
def get_asset_thumb(pid: str, aid: str, state: State) -> FileResponse:
    project = state.store.load(pid)
    asset = _find_asset(project, aid)
    path = _thumb_path(state.store.project_dir(pid), asset.id)
    if asset.kind == "audio" or not path.is_file():
        raise FkError(NOT_FOUND, f"no thumbnail for asset: {aid}")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/projects/{pid}/assets/{aid}")
def delete_asset(pid: str, aid: str, state: State) -> Project:
    project = state.store.load(pid)
    asset = _find_asset(project, aid)
    in_use = project.timeline.audio_asset_id == aid or any(
        clip.asset_id == aid for track in project.timeline.tracks for clip in track.clips
    )
    if in_use:
        raise FkError(ASSET_IN_USE, f"asset {aid} is used by the timeline")
    project.assets = [a for a in project.assets if a.id != aid]
    saved = state.store.save(project)
    project_dir = state.store.project_dir(pid)
    (project_dir / asset.filename).unlink(missing_ok=True)
    _thumb_path(project_dir, aid).unlink(missing_ok=True)
    return saved
