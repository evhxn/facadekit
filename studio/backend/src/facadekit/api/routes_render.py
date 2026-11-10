from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import cv2
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from starlette.responses import FileResponse

from facadekit.api.deps import AppState, get_state
from facadekit.errors import BAD_REQUEST, INTERNAL, NOT_FOUND, FkError
from facadekit.ids import new_id
from facadekit.render.compositor import Compositor
from facadekit.render.outline import render_alignment_check
from facadekit.render.pipeline import render_project
from facadekit.render.presets import get_preset
from facadekit.store.project_store import load_project_dir

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]

_RID = re.compile(r"^rnd_[0-9a-f]{8}$")


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str
    include_audio: bool = True
    kind: Literal["show", "alignment_check"] = "show"


def renders_dir(project_dir: Path) -> Path:
    return project_dir / "renders"


def render_file(project_dir: Path, render_id: str) -> Path:
    """Path of an existing render; raises NOT_FOUND for malformed or unknown ids."""
    if not _RID.fullmatch(render_id):
        raise FkError(NOT_FOUND, f"no such render: {render_id}")
    path = renders_dir(project_dir) / f"{render_id}.mp4"
    if not path.is_file():
        raise FkError(NOT_FOUND, f"no such render: {render_id}")
    return path


def render_kind(project_dir: Path, render_id: str) -> str:
    sidecar = renders_dir(project_dir) / f"{render_id}.json"
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        kind = data.get("kind")
    except (OSError, ValueError, AttributeError):
        return "show"
    return "alignment_check" if kind == "alignment_check" else "show"


@router.get("/projects/{pid}/frame")
def get_frame(
    pid: str,
    state: State,
    t_ms: Annotated[int, Query()],
    scale: Annotated[float, Query()] = 0.5,
) -> Response:
    if t_ms < 0:
        raise FkError(BAD_REQUEST, "t_ms must be >= 0")
    if not 0.1 <= scale <= 1:
        raise FkError(BAD_REQUEST, "scale must be between 0.1 and 1")
    project_dir = state.store.project_dir(pid)
    project = load_project_dir(project_dir)
    compositor = Compositor(project, project_dir, scale=scale)
    try:
        frame = compositor.frame(t_ms)
    finally:
        compositor.close()
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise FkError(INTERNAL, "could not encode frame")
    return Response(
        content=buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


@router.post("/projects/{pid}/renders", status_code=202)
def start_render(pid: str, body: RenderRequest, state: State) -> dict[str, str]:
    project_dir = state.store.project_dir(pid)
    get_preset(body.preset)
    render_id = new_id("rnd")
    out_path = renders_dir(project_dir) / f"{render_id}.mp4"

    def run(progress: Any, cancel: Any) -> dict[str, Any]:
        out_path.parent.mkdir(exist_ok=True)
        if body.kind == "alignment_check":
            result = render_alignment_check(
                project_dir, out_path, preset_id=body.preset, progress=progress, cancel=cancel
            )
        else:
            result = render_project(
                project_dir,
                body.preset,
                out_path,
                include_audio=body.include_audio,
                progress=progress,
                cancel=cancel,
            )
        sidecar = out_path.with_suffix(".json")
        sidecar.write_text(json.dumps({"kind": body.kind, "preset": body.preset}), encoding="utf-8")
        return {
            "render_id": render_id,
            "filename": result["filename"],
            "size_bytes": result["size_bytes"],
            "duration_ms": result["duration_ms"],
        }

    job = state.jobs.submit("render", run)
    return {"job_id": job.id}


@router.get("/projects/{pid}/renders")
def list_renders(pid: str, state: State) -> list[dict[str, Any]]:
    folder = renders_dir(state.store.project_dir(pid))
    entries: list[tuple[float, dict[str, Any]]] = []
    for path in folder.glob("rnd_*.mp4") if folder.is_dir() else []:
        if not _RID.fullmatch(path.stem):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries.append(
            (
                stat.st_mtime,
                {
                    "render_id": path.stem,
                    "filename": path.name,
                    "size_bytes": stat.st_size,
                    "modified_at": modified,
                },
            )
        )
    entries.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in entries]


@router.get("/projects/{pid}/renders/{rid}/file")
def get_render_file(pid: str, rid: str, state: State) -> FileResponse:
    path = render_file(state.store.project_dir(pid), rid)
    return FileResponse(path, media_type="video/mp4")
