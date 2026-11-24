from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Response

from facadekit.api.deps import AppState, get_state
from facadekit.errors import BAD_REQUEST, FkError
from facadekit.models.project import Project, ProjectorSpec, parse_project
from facadekit.sim.scene import default_scene
from facadekit.store.project_store import ProjectSummary

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


@router.get("/projects")
def list_projects(state: State) -> list[ProjectSummary]:
    return state.store.list()


@router.post("/projects", status_code=201)
def create_project(state: State, body: Annotated[dict[str, Any], Body()]) -> Project:
    name = body.get("name")
    if not isinstance(name, str):
        raise FkError(BAD_REQUEST, "name: must be a string")
    projector_data = body.get("projector")
    projector = ProjectorSpec() if projector_data is None else _parse_projector(projector_data)
    return state.store.create(name, projector)


@router.post("/projects/samples", status_code=202)
def create_sample_project(
    state: State, body: Annotated[dict[str, Any] | None, Body()] = None
) -> dict[str, str]:
    """A sample project on the simulated house, built in a job (scan + surfaces + show)."""
    from facadekit.samples import THEMES, create_sample, sample_name

    theme = (body or {}).get("theme", "halloween")
    if theme not in THEMES:
        raise FkError(BAD_REQUEST, f"theme: must be one of {', '.join(THEMES)}")
    width, height = default_scene().proj_size
    stub = state.store.create(sample_name(theme), ProjectorSpec(width=width, height=height))
    folder = state.store.project_dir(stub.id)

    def job(progress: Progress, cancel: Cancel) -> dict[str, Any]:
        try:
            create_sample(folder, stub.id, theme, progress=progress, cancel=cancel)
        except BaseException:
            # A half-made sample is useless: remove it so the project list stays clean.
            state.store.delete(stub.id)
            raise
        return {"project_id": stub.id}

    return {"job_id": state.jobs.submit("calibration", job).id, "project_id": stub.id}


def _parse_projector(data: Any) -> ProjectorSpec:
    from pydantic import ValidationError

    try:
        return ProjectorSpec.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise FkError(BAD_REQUEST, f"projector.{location}: {first['msg']}") from exc


@router.get("/projects/{pid}")
def get_project(pid: str, state: State) -> Project:
    return state.store.load(pid)


@router.put("/projects/{pid}")
def put_project(pid: str, state: State, body: Annotated[dict[str, Any], Body()]) -> Project:
    project = parse_project(body)
    if project.id != pid:
        raise FkError(BAD_REQUEST, f"project id {project.id!r} does not match URL {pid!r}")
    return state.store.save(project)


@router.delete("/projects/{pid}")
def delete_project(pid: str, state: State) -> Response:
    state.store.delete(pid)
    return Response(status_code=204)
