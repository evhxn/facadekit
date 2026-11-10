from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from facadekit.api.deps import AppState, get_state
from facadekit.api.ws import ProjectorStatus
from facadekit.errors import BAD_REQUEST, PROJECTOR_NOT_CONNECTED, FkError

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


class ShowRequest(BaseModel):
    mode: Literal["blank", "white", "grid", "outlines", "compositor"]
    project_id: str | None = None
    selected_id: str | None = None
    # surface id -> grid polylines (mesh editor preview); passed through to the outlines message
    grids: dict[str, list[list[list[float]]]] | None = None


class TransportRequest(BaseModel):
    project_id: str
    playing: bool
    t_ms: int
    revision: int


@router.get("/projector/status")
def projector_status(state: State) -> ProjectorStatus:
    return state.projector.status()


@router.post("/projector/show")
def projector_show(body: ShowRequest, state: State) -> ProjectorStatus:
    hub = state.projector
    if not hub.status().connected:
        raise FkError(PROJECTOR_NOT_CONNECTED, "no projector page is connected")
    message: dict[str, Any] = {"type": body.mode}
    if body.mode in ("outlines", "compositor"):
        if not body.project_id:
            raise FkError(BAD_REQUEST, f"mode {body.mode!r} requires project_id")
        project = state.store.load(body.project_id)
        if body.mode == "compositor":
            message["project_id"] = project.id
        else:
            message["width"] = project.projector.width
            message["height"] = project.projector.height
            grids = body.grids or {}
            surfaces: list[dict[str, Any]] = []
            for s in project.surfaces:
                entry: dict[str, Any] = {
                    "id": s.id,
                    "name": s.name,
                    "quad": [list(p) for p in s.quad],
                    "selected": s.id == body.selected_id,
                }
                if s.id in grids:
                    entry["grid"] = grids[s.id]
                surfaces.append(entry)
            message["surfaces"] = surfaces
    hub.send(message, wait_ack=False)
    hub.set_mode(body.mode)
    return hub.status()


@router.post("/projector/transport", status_code=204)
def projector_transport(body: TransportRequest, state: State) -> Response:
    try:
        state.projector.send({"type": "transport", **body.model_dump()}, wait_ack=False)
    except FkError as exc:
        if exc.code != PROJECTOR_NOT_CONNECTED:
            raise
    return Response(status_code=204)


@router.get("/projector/image/{seq}.png")
def projector_image(seq: int, state: State) -> Response:
    return Response(content=state.projector.get_image(seq), media_type="image/png")
