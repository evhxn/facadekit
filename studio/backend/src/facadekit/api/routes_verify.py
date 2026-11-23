"""Verify surface (docs/13-surface-verification.md): has the rig moved, does the light land on
the object? Loopback only: these routes are not in the paired-device list (remote_auth)."""

from __future__ import annotations

import base64
import logging
import re
import time
from collections.abc import Callable
from typing import Annotated, Any

import cv2
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from facadekit.api.deps import AppState, get_state
from facadekit.api.routes_calibration import _scan_display
from facadekit.api.routes_devices import CameraBusyError, _busy_response
from facadekit.calib.pipeline import load_calibration
from facadekit.calib.verify import (
    RIG_RECORD,
    Injection,
    load_record,
    overlay_path,
    run_verify,
    save_record,
)
from facadekit.errors import BAD_REQUEST, NOT_FOUND, FkError
from facadekit.models.verification import VerificationRecord

router = APIRouter()
log = logging.getLogger(__name__)

State = Annotated[AppState, Depends(get_state)]
Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]

_RECORD_ID = re.compile(r"^[a-z0-9_-]{1,40}$")


class SelfTestInjection(BaseModel):
    """A known error put into the check (self-test only, docs/13 "Self-test")."""

    model_config = ConfigDict(extra="forbid")

    proj_shift: tuple[float, float] = (0.0, 0.0)
    grow_px: float = Field(default=0.0, ge=-40, le=40)
    cam_shift: tuple[float, float] = (0.0, 0.0)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_id: str | None = None  # None: only check that the camera and projector are still
    camera_id: str | None = None  # None: the camera that made the scan
    inject: SelfTestInjection | None = None


@router.post("/projects/{pid}/verify", status_code=202, response_model=None)
def start_verify(pid: str, body: VerifyRequest, state: State) -> dict[str, str] | JSONResponse:
    project = state.store.load(pid)
    project_dir = state.store.project_dir(pid)
    if project.calibration_id is None:
        raise FkError(BAD_REQUEST, "Scan the house on the Setup page first.")
    cal = load_calibration(project_dir, project.calibration_id)
    if cal.mode != "scan":
        raise FkError(
            BAD_REQUEST,
            "This project uses manual mode, which has no camera measurements to check "
            "against. Scan the house to use Verify.",
        )
    if body.surface_id is not None and not any(s.id == body.surface_id for s in project.surfaces):
        raise FkError(NOT_FOUND, f"surface {body.surface_id!r} not found")
    camera_id = body.camera_id or cal.camera_id
    if not camera_id:
        raise FkError(BAD_REQUEST, "camera_id: required (the scan does not name its camera)")
    try:
        state.cameras.validate(camera_id)
    except FkError as exc:
        raise FkError(
            BAD_REQUEST,
            "The scan was made with a camera FacadeKit cannot open now (a phone or the Pi). "
            "Verify needs the laptop camera that made the scan: scan with it first.",
        ) from exc
    display = _scan_display(state, (project.projector.width, project.projector.height))
    sleep: Callable[[float], None] = (lambda _s: None) if state.sim else time.sleep
    inject = (
        Injection(
            proj_shift=body.inject.proj_shift,
            grow_px=body.inject.grow_px,
            cam_shift=body.inject.cam_shift,
        )
        if body.inject is not None
        else None
    )
    try:
        camera = state.cameras.acquire(camera_id)  # 409 now, not inside the job
    except CameraBusyError as exc:
        return _busy_response(exc)
    low_power = state.cameras.low_power
    log.info(
        "verify requested: project %s, surface %s, camera %s%s",
        pid,
        body.surface_id or "(rig only)",
        camera_id,
        " (self-test)" if inject is not None else "",
    )

    def verify_job(progress: Progress, cancel: Cancel) -> dict[str, Any]:
        try:
            current = state.store.load(pid)  # the outline as saved when the job starts
            outcome = run_verify(
                project_dir,
                current,
                display,
                camera,
                surface_id=body.surface_id,
                progress=progress,
                cancel=cancel,
                sleep=sleep,
                low_power=low_power,
                inject=inject,
            )
        finally:
            state.cameras.release(camera_id)
        result: dict[str, Any] = {"verification": outcome.record.model_dump(mode="json")}
        if inject is None:  # a self-test's deliberate errors are not the surface's record
            save_record(project_dir, outcome)
        elif outcome.overlay is not None:  # the self-test keeps its pictures in its bundle
            ok, buf = cv2.imencode(".jpg", outcome.overlay, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                result["overlay_jpg_b64"] = base64.b64encode(buf.tobytes()).decode("ascii")
        return result

    try:
        return {"job_id": state.jobs.submit("calibration", verify_job).id}
    except BaseException:
        state.cameras.release(camera_id)
        raise


def _record_id(rid: str) -> str | None:
    if not _RECORD_ID.match(rid):
        raise FkError(NOT_FOUND, "no such verification")
    return None if rid == RIG_RECORD else rid


@router.get("/projects/{pid}/verifications/{rid}")
def get_verification(pid: str, rid: str, state: State) -> VerificationRecord:
    """The latest check of surface `rid` (`rig` for a camera-and-projector-only check)."""
    return load_record(state.store.project_dir(pid), _record_id(rid))


@router.get("/projects/{pid}/verifications/{rid}/overlay.jpg")
def get_verification_overlay(pid: str, rid: str, state: State) -> FileResponse:
    path = overlay_path(state.store.project_dir(pid), _record_id(rid))
    if not path.is_file():
        raise FkError(NOT_FOUND, "No picture for this verification.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
