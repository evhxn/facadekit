from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from facadekit.api.deps import AppState, get_state
from facadekit.api.routes_devices import CameraBusyError, _busy_response
from facadekit.calib.capture import precheck
from facadekit.calib.learn import STRATEGIES, ScanAdvisor, learning_enabled
from facadekit.calib.patterns import PatternSpec, pattern_sequence
from facadekit.calib.pipeline import (
    create_manual_calibration,
    load_calibration,
    run_calibration,
    run_calibration_from_capture,
)
from facadekit.calib.quality import LitBlob, probe_stripes, verdict
from facadekit.calib.realign import apply_drift, estimate_drift
from facadekit.calib.roi import suggest_roi
from facadekit.calib.selfclock import (
    RECORD_MARGIN_S,
    SCAN_PLAYLIST,
    record_and_capture,
    render_pattern_video,
    scan_media_name,
    video_seconds,
)
from facadekit.devices.base import Camera, ProjectorDisplay
from facadekit.devices.cropped import CroppedCamera
from facadekit.devices.display_ws import WsDisplay
from facadekit.errors import (
    BAD_REQUEST,
    NOT_FOUND,
    PROJECTOR_NOT_CONNECTED,
    REVISION_CONFLICT,
    FkError,
)
from facadekit.fpp.client import FppClient
from facadekit.fpp.publish import publish_render
from facadekit.geom.homography import apply_h
from facadekit.ids import new_id
from facadekit.models.calibration import (
    Calibration,
    CalibSettings,
    CameraRoi,
    StrictCalibSettings,
    StrictCameraRoi,
)
from facadekit.models.camera_check import CameraCheck
from facadekit.models.project import Project

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["scan", "manual", "scan_pi"]
    camera_id: str | None = None
    host: str | None = None
    settings: StrictCalibSettings | None = None
    learn: bool = True  # let scan memory pick the strategy (modes "scan" and "scan_pi")


_STRATEGY_FIELDS = {"skip_lsb", "average_frames", "min_conf"}


def _advisor(state: AppState, body: CalibrationRequest) -> ScanAdvisor | None:
    """Scan memory picks the strategy unless the caller fixed one of its fields."""
    if not body.learn or state.scan_memory is None or not learning_enabled():
        return None
    if body.settings is not None and body.settings.model_fields_set & _STRATEGY_FIELDS:
        return None
    return ScanAdvisor(state.scan_memory)


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


def _scan_display(state: AppState) -> ProjectorDisplay:
    if state.sim:
        assert state.sim_display is not None
        return state.sim_display
    if not state.projector.status().connected:
        raise FkError(PROJECTOR_NOT_CONNECTED, "no projector page is connected")
    return WsDisplay(state.projector)


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
        raise FkError(BAD_REQUEST, f"camera_id: required for mode '{body.mode}'")
    state.cameras.validate(camera_id)

    if body.mode == "scan_pi":
        if not body.host:
            raise FkError(BAD_REQUEST, "host: required for mode 'scan_pi'")
        return {
            "job_id": _submit_pi_scan(
                state, pid, project, body.host, camera_id, settings, _advisor(state, body)
            )
        }

    display = _scan_display(state)
    _check_resolution(display.size, project_size)
    sleep: Callable[[float], None] = (lambda _s: None) if state.sim else time.sleep
    advisor = _advisor(state, body)

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
                advisor=advisor,
            )
        finally:
            state.cameras.release(camera_id)
        return _finish(state, pid, cal.id)

    return {"job_id": state.jobs.submit("calibration", scan_job).id}


def _submit_pi_scan(
    state: AppState,
    pid: str,
    project: Project,
    host: str,
    camera_id: str,
    settings: CalibSettings,
    advisor: ScanAdvisor | None,
) -> str:
    """Scan with the projector on the Pi: upload a pattern video, play it, record the camera."""
    project_dir = state.store.project_dir(pid)

    def pi_job(progress: Progress, cancel: Cancel) -> dict[str, Any]:
        # The video is rendered before the lighting is measured, so the strategy comes from the
        # lighting remembered for this camera; without history it equals the fixed defaults.
        scan_settings = settings
        strategy_id: str | None = None
        if advisor is not None:
            strategy = advisor.remembered_strategy(camera_id) or STRATEGIES[0]
            scan_settings = strategy.apply(settings)
            strategy_id = strategy.id
        spec = PatternSpec(
            project.projector.width, project.projector.height, scan_settings.skip_lsb
        )
        n_patterns = len(pattern_sequence(spec))

        def stage(lo: float, hi: float) -> Progress:
            return lambda f, m: progress(lo + (hi - lo) * f, m)

        out_dir = project_dir / "renders"
        out_dir.mkdir(exist_ok=True)
        video = out_dir / f"{new_id('rnd')}.mp4"
        try:
            progress(0.0, "Rendering the pattern video")
            render_pattern_video(spec, video)
            publish_render(
                host,
                video,
                media_name=scan_media_name(pid),
                playlist_name=SCAN_PLAYLIST,
                start=False,
                progress=stage(0.1, 0.3),
                cancel=cancel,
            )
        finally:
            video.unlink(missing_ok=True)  # a scan video is not a show render
        camera = state.cameras.acquire(camera_id)
        fpp = FppClient(host)
        try:
            camera.open()
            try:
                capture = record_and_capture(
                    camera,
                    spec,
                    lambda: fpp.start_playlist(SCAN_PLAYLIST),
                    total_seconds=video_seconds(n_patterns) + RECORD_MARGIN_S,
                    progress=stage(0.3, 0.8),
                    cancel=cancel,
                )
            finally:
                camera.close()
                try:
                    fpp.stop()
                except FkError:
                    pass
        finally:
            fpp.close()
            state.cameras.release(camera_id)
        cal = run_calibration_from_capture(
            project_dir,
            capture,
            camera_id,
            scan_settings,
            progress=stage(0.8, 1.0),
            cancel=cancel,
            strategy=strategy_id,
            advisor=advisor,
        )
        return _finish(state, pid, cal.id)

    return state.jobs.submit("calibration", pi_job).id


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
    warning: str | None = None  # set when the shift is far above this user's usual re-align


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
    warning = state.align_memory.warning(max_shift) if state.align_memory is not None else None
    out: Project = moved
    if body.apply:
        out = state.store.save(moved.model_copy(update={"calibration_id": body.new_calibration_id}))
        state.events.publish({"type": "project", "project_id": pid, "revision": out.revision})
        if state.align_memory is not None:
            state.align_memory.record_applied(
                pid, max_shift, result.reproj_error_px, result.inliers
            )
    return RealignResponse(
        inliers=result.inliers,
        reproj_error_px=result.reproj_error_px,
        max_shift_px=max_shift,
        project=out,
        warning=warning,
    )


class CameraCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_id: str
    roi: StrictCameraRoi | None = None


def _full_box(box: tuple[int, int, int, int], roi: CameraRoi | None) -> tuple[int, int, int, int]:
    """A box in (binned) crop coordinates -> full-frame pixel-corner coordinates."""
    x0, y0, x1, y1 = box
    if roi is None:
        return box
    return (
        roi.x + x0 * roi.bin,
        roi.y + y0 * roi.bin,
        roi.x + x1 * roi.bin,
        roi.y + y1 * roi.bin,
    )


def _check_camera(
    display: ProjectorDisplay,
    camera: Camera,
    project_size: tuple[int, int],
    roi: CameraRoi | None,
    sleep: Callable[[float], None],
) -> CameraCheck:
    settings = CalibSettings()
    view: Camera = CroppedCamera(camera, roi) if roi is not None else camera
    view.open()
    try:
        controls = view.set_controls(auto_exposure=False, auto_wb=False, autofocus=False)
        spec = PatternSpec(*project_size)
        stats = precheck(display, view, spec, settings=settings, sleep=sleep)
        modulation = probe_stripes(display, view, spec, settings, bits=(2, 3, 4), sleep=sleep)
    finally:
        view.close()
    frame_w, frame_h = (roi.frame_width, roi.frame_height) if roi else camera.resolution
    s = stats["px_per_proj_px"]
    result, reasons = verdict(
        s,
        stats["snr"],
        modulation=modulation,
        p99_white=stats["p99_white"],
        controls=controls,
    )
    crop_box = (
        int(stats["lit_x0"]),
        int(stats["lit_y0"]),
        int(stats["lit_x1"]),
        int(stats["lit_y1"]),
    )
    x0, y0, x1, y1 = _full_box(crop_box, roi)
    lit_box = CameraRoi(x=x0, y=y0, w=x1 - x0, h=y1 - y0, frame_width=frame_w, frame_height=frame_h)
    # suggest_roi only reads the blob's box.
    stub = LitBlob(
        mask=np.zeros((1, 1), bool),
        box=(x0, y0, x1, y1),
        area_px=int(stats["lit_px"]),
        hull_area_px=0.0,
        total_lit_px=int(stats["lit_px"]),
    )
    return CameraCheck(
        verdict=result,
        reasons=list(reasons),
        camera_width=frame_w,
        camera_height=frame_h,
        lit_box=lit_box,
        suggested_roi=suggest_roi(stub, frame_w, frame_h),
        px_per_proj_px=s,
        proj_px_per_cam_px=round(1.0 / s, 1) if s > 0 else 0.0,
        stripe_modulation={str(bit): m for bit, m in sorted(modulation.items())},
        snr=stats["snr"],
        median_contrast=stats["median_contrast"],
        controls=controls,
        suggest_1080p=result != "good" and (frame_w < 1920 or frame_h < 1080),
    )


def _show_grid(state: AppState) -> None:
    """Leave the projector on the grid, as the camera step does, not on test stripes."""
    try:
        state.projector.send({"type": "grid"}, wait_ack=False)
        state.projector.set_mode("grid")
    except FkError:
        pass  # projector page closed meanwhile; nothing to restore


@router.post("/projects/{pid}/camera-check", status_code=202, response_model=None)
def start_camera_check(
    pid: str, body: CameraCheckRequest, state: State
) -> dict[str, str] | JSONResponse:
    project = state.store.load(pid)
    project_size = (project.projector.width, project.projector.height)
    state.cameras.validate(body.camera_id)
    display = _scan_display(state)
    _check_resolution(display.size, project_size)
    sleep: Callable[[float], None] = (lambda _s: None) if state.sim else time.sleep
    camera_id = body.camera_id
    try:
        camera = state.cameras.acquire(camera_id)  # 409 now, not inside the job
    except CameraBusyError as exc:
        return _busy_response(exc)

    def check_job(_progress: Progress, _cancel: Cancel) -> dict[str, Any]:
        try:
            check = _check_camera(display, camera, project_size, body.roi, sleep)
        finally:
            state.cameras.release(camera_id)
            _show_grid(state)
        return {"check": check.model_dump(mode="json")}

    try:
        return {"job_id": state.jobs.submit("calibration", check_job).id}
    except BaseException:
        state.cameras.release(camera_id)
        raise
