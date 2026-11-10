from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from facadekit.api.deps import AppState, get_state
from facadekit.calib.pipeline import create_manual_calibration, load_calibration, run_calibration
from facadekit.calib.realign import apply_drift, estimate_drift
from facadekit.devices.base import ProjectorDisplay
from facadekit.devices.display_ws import WsDisplay
from facadekit.errors import (
    BAD_REQUEST,
    NOT_FOUND,
    PROJECTOR_NOT_CONNECTED,
    REVISION_CONFLICT,
    FkError,
)
from facadekit.geom.homography import apply_h
from facadekit.models.calibration import Calibration, CalibSettings
from facadekit.models.project import Project

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["scan", "manual"]
    camera_id: str | None = None
    settings: CalibSettings | None = None


def _check_resolution(display_size: tuple[int, int], project_size: tuple[int, int]) -> None:
    if display_size != project_size:
        raise FkError(
            "RESOLUTION_MISMATCH",
            f"Projector window is {display_size[0]}x{display_size[1]} but the project expects "
            f"{project_size[0]}x{project_size[1]}.",
        )


def _attach_calibration(state: AppState, project_id: str, calibration_id: str) -> int:
    for attempt in range(2):
        project = state.store.load(project_id)
        try:
            saved = state.store.save(project.model_copy(update={"calibration_id": calibration_id}))
        except FkError as exc:
            if exc.code == REVISION_CONFLICT and attempt == 0:
                continue
            raise
        return saved.revision
    raise FkError(REVISION_CONFLICT, f"project {project_id!r} kept changing")  # pragma: no cover


@router.post("/projects/{pid}/calibrations", status_code=202)
def start_calibration(pid: str, body: CalibrationRequest, state: State) -> dict[str, str]:
    project = state.store.load(pid)
    project_dir = state.store.project_dir(pid)
    project_size = (project.projector.width, project.projector.height)
    settings = body.settings or CalibSettings()

    if body.mode == "manual":

        def manual_job(_progress: Progress, _cancel: Cancel) -> dict[str, Any]:
            cal = create_manual_calibration(project_dir, project.projector)
            return _finish(state, pid, cal.id)

        return {"job_id": state.jobs.submit("calibration", manual_job).id}

    camera_id = body.camera_id
    if not camera_id:
        raise FkError(BAD_REQUEST, "camera_id: required for mode 'scan'")
    state.cameras.validate(camera_id)

    display: ProjectorDisplay
    if state.sim:
        assert state.sim_display is not None
        display = state.sim_display
    else:
        status = state.projector.status()
        if not status.connected:
            raise FkError(PROJECTOR_NOT_CONNECTED, "no projector page is connected")
        display = WsDisplay(state.projector)
    _check_resolution(display.size, project_size)
    sleep: Callable[[float], None] = (lambda _s: None) if state.sim else time.sleep

    def scan_job(progress: Progress, cancel: Cancel) -> dict[str, Any]:
        camera = state.cameras.acquire(camera_id)
        try:
            cal = run_calibration(
                project_dir,
                display,
                camera,
                camera_id,
                settings,
                progress=progress,
                cancel=cancel,
                sleep=sleep,
            )
        finally:
            state.cameras.release(camera_id)
        return _finish(state, pid, cal.id)

    return {"job_id": state.jobs.submit("calibration", scan_job).id}


def _finish(state: AppState, project_id: str, calibration_id: str) -> dict[str, Any]:
    revision = _attach_calibration(state, project_id, calibration_id)
    state.events.publish({"type": "project", "project_id": project_id, "revision": revision})
    return {"calibration_id": calibration_id}


@router.get("/projects/{pid}/calibrations/{cid}")
def get_calibration(pid: str, cid: str, state: State) -> Calibration:
    return load_calibration(state.store.project_dir(pid), cid)


def _image(state: AppState, pid: str, cid: str, name: str) -> FileResponse:
    project_dir = state.store.project_dir(pid)
    load_calibration(project_dir, cid)  # validates the id
    path: Path = project_dir / "calibration" / cid / name
    if not path.is_file():
        raise FkError(NOT_FOUND, f"{name} not found for calibration {cid}")
    return FileResponse(path, media_type="image/png")


@router.get("/projects/{pid}/calibrations/{cid}/proj_view.png")
def proj_view(pid: str, cid: str, state: State) -> FileResponse:
    return _image(state, pid, cid, "proj_view.png")


@router.get("/projects/{pid}/calibrations/{cid}/measured_mask.png")
def measured_mask(pid: str, cid: str, state: State) -> FileResponse:
    return _image(state, pid, cid, "measured_mask.png")


class RealignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_calibration_id: str
    new_calibration_id: str
    apply: bool = False


class RealignResponse(BaseModel):
    inliers: int
    reproj_error_px: float
    max_shift_px: float
    project: Project | None


def _read_view(project_dir: Path, cid: str, name: str, *, required: bool) -> np.ndarray | None:
    load_calibration(project_dir, cid)  # validates the id, NOT_FOUND
    path = project_dir / "calibration" / cid / name
    flag = cv2.IMREAD_COLOR if name == "proj_view.png" else cv2.IMREAD_GRAYSCALE
    img = cv2.imread(str(path), flag) if path.is_file() else None
    if img is None and required:
        raise FkError(NOT_FOUND, f"{name} not found for calibration {cid}")
    return img


@router.post("/projects/{pid}/realign")
def realign(pid: str, body: RealignRequest, state: State) -> RealignResponse:
    project_dir = state.store.project_dir(pid)
    project = state.store.load(pid)
    old_view = _read_view(project_dir, body.old_calibration_id, "proj_view.png", required=True)
    new_view = _read_view(project_dir, body.new_calibration_id, "proj_view.png", required=True)
    assert old_view is not None and new_view is not None
    result = estimate_drift(
        old_view,
        new_view,
        old_mask=_read_view(
            project_dir, body.old_calibration_id, "measured_mask.png", required=False
        ),
        new_mask=_read_view(
            project_dir, body.new_calibration_id, "measured_mask.png", required=False
        ),
    )
    moved = apply_drift(project, result.h_old2new)
    shifts = [
        float(np.linalg.norm(apply_h(result.h_old2new, np.array([p]))[0] - np.array(p)))
        for surface in project.surfaces
        for p in surface.quad
    ]
    max_shift = max(shifts, default=0.0)
    out: Project = moved
    if body.apply:
        out = state.store.save(moved.model_copy(update={"calibration_id": body.new_calibration_id}))
        state.events.publish({"type": "project", "project_id": pid, "revision": out.revision})
    return RealignResponse(
        inliers=result.inliers,
        reproj_error_px=result.reproj_error_px,
        max_shift_px=max_shift,
        project=out,
    )
