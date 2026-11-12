from __future__ import annotations

import logging
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import cv2
import numpy as np
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect

from facadekit.api.deps import AppState, get_state
from facadekit.api.routes_devices import CameraBusyError, _busy_response
from facadekit.calib import phone
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
    INTERNAL,
    NOT_FOUND,
    PROJECTOR_NOT_CONNECTED,
    REVISION_CONFLICT,
    FkError,
)
from facadekit.fpp.client import FppClient
from facadekit.fpp.publish import publish_render
from facadekit.geom.homography import apply_h
from facadekit.ids import new_id
from facadekit.jobs.runner import JobRunner
from facadekit.models.calibration import Calibration, CalibSettings, CameraRoi
from facadekit.models.camera_check import CameraCheck
from facadekit.models.project import Project

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]

router = APIRouter()
log = logging.getLogger(__name__)

State = Annotated[AppState, Depends(get_state)]


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["scan", "manual", "scan_pi"]
    camera_id: str | None = None
    host: str | None = None
    settings: CalibSettings | None = None
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
    roi: CameraRoi | None = None


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


# --- phone scans (T-208, docs/08 §4) -------------------------------------------------------

PHONE_SESSION_TTL_S = 24 * 3600
FREE_DISK_MARGIN_BYTES = 1024**3  # docs/08: keep at least 1 GB free after the upload
UPLOAD_CHUNK_BYTES = 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 64 * 1024  # boundaries and part headers on top of the video bytes
KEEP_VIDEO_ENV = "FACADEKIT_KEEP_PHONE_VIDEO"
MSG_SESSION_EXPIRED = "This light show has expired. Start it again."
MSG_SHOW_BUSY = "A light show is already running."
MSG_ANALYSIS_BUSY = "A phone video is already being checked for this project. Wait for it."
MSG_TOO_LARGE = "The video is larger than 4 GB."
MSG_NO_SPACE = "There is not enough free disk space for this video. Free some space and retry."
MSG_BAD_UPLOAD = "The video did not arrive completely. Send it again."
MSG_UPLOAD_STALLED = "Sending the video stopped. Check the Wi-Fi and send it again."
MSG_PHONE_JOB_FAILED = "Something went wrong while checking the video. Try again."
MSG_SHOW_JOB_FAILED = "Something went wrong during the light show. Try again."
UPLOAD_IDLE_TIMEOUT_S = 60.0  # no bytes for this long: the upload is dropped
UPLOAD_MAX_S = 2 * 3600.0  # 4 GB at about 0.6 MB/s; slower uploads are dropped


class PhoneScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countdown_s: int = Field(default=15, ge=5, le=60)
    learn: bool = True


class PhoneSession(BaseModel):
    """`<project>/calibration/.phone/<sid>.json`: what the light show played."""

    model_config = ConfigDict(extra="ignore")

    id: str
    created_at: str
    proj_w: int = Field(gt=0)
    proj_h: int = Field(gt=0)
    skip_lsb: int = Field(ge=0)
    min_conf: float = Field(gt=0.0, lt=1.0)
    strategy: str | None = None
    learn: bool = True


class _BusySlots:
    """At most one holder per key: a request that is still streaming, or the job it submitted.

    A slot whose job is no longer queued or running (finished, cancelled while queued, or
    trimmed from the runner) counts as free, so a job that never ran cannot lock the key.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[str, tuple[str, str | None, JobRunner | None]] = {}

    def claim(self, key: str) -> str | None:
        with self._lock:
            held = self._held.get(key)
            if held is not None and self._active(held):
                return None
            token = secrets.token_hex(8)
            self._held[key] = (token, None, None)
            return token

    def attach(self, key: str, token: str, jobs: JobRunner, job_id: str) -> None:
        with self._lock:
            held = self._held.get(key)
            if held is not None and held[0] == token:
                self._held[key] = (token, job_id, jobs)

    def release(self, key: str, token: str) -> None:
        with self._lock:
            held = self._held.get(key)
            if held is not None and held[0] == token:
                del self._held[key]

    @staticmethod
    def _active(held: tuple[str, str | None, JobRunner | None]) -> bool:
        _token, job_id, jobs = held
        if job_id is None or jobs is None:
            return True  # the request is still running
        try:
            return jobs.get(job_id).state in ("queued", "running")
        except FkError:
            return False


_BUSY = _BusySlots()


def _conflict(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=409, content={"error": {"code": BAD_REQUEST, "message": message}}
    )


_OPEN_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_OPEN_READ = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
MSG_UNKNOWN_SESSION = "Unknown light show. Start it again."


def _phone_dir(project_dir: Path) -> Path:
    """Create `<project>/calibration/.phone` ourselves; refuse it if any part is a symlink."""
    folder = project_dir
    for part in ("calibration", ".phone"):
        folder = folder / part
        if folder.is_symlink():
            raise FkError(BAD_REQUEST, "The project folder is not set up as expected.")
        folder.mkdir(exist_ok=True)
        if not folder.is_dir() or folder.is_symlink():
            raise FkError(BAD_REQUEST, "The project folder is not set up as expected.")
    return folder


def _session_path(project_dir: Path, sid: str) -> Path:
    _phone_dir(project_dir)
    # phone_upload_path validates the id (^phs_[0-9a-f]{8}$) and the directory.
    return phone.phone_upload_path(project_dir, sid).with_suffix(".json")


def _new_file(path: Path) -> int:
    """A fresh file descriptor; never follows or reuses an existing name."""
    return os.open(path, _OPEN_NEW, 0o600)


def _temp_name(final: Path) -> Path:
    return final.with_name(f"{final.stem}.{secrets.token_hex(6)}.part")


def _write_session(project_dir: Path, session: PhoneSession) -> None:
    path = _session_path(project_dir, session.id)
    tmp = _temp_name(path)
    try:
        with os.fdopen(_new_file(tmp), "wb") as f:
            f.write(session.model_dump_json().encode("utf-8"))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _sweep_phone_dir(project_dir: Path) -> None:
    """Delete sessions, uploads and partial files older than a day (docs/08: sessions expire)."""
    cutoff = time.time() - PHONE_SESSION_TTL_S
    for entry in _phone_dir(project_dir).iterdir():
        try:
            if entry.lstat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue  # a directory, or removed meanwhile


def _load_session(project_dir: Path, sid: str) -> PhoneSession:
    path = _session_path(project_dir, sid)
    try:
        with os.fdopen(os.open(path, _OPEN_READ), "rb") as f:
            raw = f.read(64 * 1024)
        session = PhoneSession.model_validate_json(raw)
        created = datetime.strptime(session.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (OSError, ValidationError, ValueError) as exc:
        raise FkError(NOT_FOUND, MSG_UNKNOWN_SESSION) from exc
    if session.id != sid:
        raise FkError(NOT_FOUND, MSG_UNKNOWN_SESSION)
    age = (datetime.now(UTC) - created).total_seconds()
    if age > PHONE_SESSION_TTL_S or age < -300:
        path.unlink(missing_ok=True)
        raise FkError(NOT_FOUND, MSG_SESSION_EXPIRED)
    return session


def _phone_advisor(state: AppState, learn: bool) -> ScanAdvisor | None:
    if not learn or state.scan_memory is None or not learning_enabled():
        return None
    return ScanAdvisor(state.scan_memory)


@router.post("/projects/{pid}/phone-scans", status_code=202, response_model=None)
def start_phone_scan(
    pid: str, state: State, body: PhoneScanRequest | None = None
) -> dict[str, str] | JSONResponse:
    body = body or PhoneScanRequest()
    project = state.store.load(pid)
    project_dir = state.store.project_dir(pid)
    proj_size = (project.projector.width, project.projector.height)
    display = _scan_display(state)
    _check_resolution(display.size, proj_size)

    settings = CalibSettings()
    strategy_id: str | None = None
    advisor = _phone_advisor(state, body.learn)
    if advisor is not None:
        strategy = advisor.remembered_strategy(phone.PHONE_CAMERA_ID) or STRATEGIES[0]
        settings = strategy.apply(settings)
        strategy_id = strategy.id

    _sweep_phone_dir(project_dir)
    key = f"show:{id(state)}"  # one projector per app
    token = _BUSY.claim(key)
    if token is None:
        return _conflict(MSG_SHOW_BUSY)
    try:
        session = PhoneSession(
            id=new_id("phs"),
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            proj_w=proj_size[0],
            proj_h=proj_size[1],
            skip_lsb=settings.skip_lsb,
            min_conf=settings.min_conf,
            strategy=strategy_id,
            learn=body.learn,
        )
        _write_session(project_dir, session)
        spec = PatternSpec(proj_size[0], proj_size[1], settings.skip_lsb)
        countdown_s = body.countdown_s

        def show_job(progress: Progress, cancel: Cancel) -> dict[str, Any]:
            try:
                phone.play_light_show(
                    display, spec, countdown_s=countdown_s, progress=progress, cancel=cancel
                )
            except FkError:
                raise
            except Exception as exc:  # no paths or internals in the job error
                log.exception("phone light show failed")
                raise FkError(INTERNAL, MSG_SHOW_JOB_FAILED) from exc
            finally:
                _BUSY.release(key, token)
            return {"session_id": session.id}

        job = state.jobs.submit("calibration", show_job)
        _BUSY.attach(key, token, state.jobs, job.id)
    except BaseException:
        _BUSY.release(key, token)
        raise
    return {"session_id": session.id, "job_id": job.id}


class _Reject(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _bad_upload(message: str) -> _Reject:
    return _Reject(400, BAD_REQUEST, message)


def _body_cap() -> int:
    return phone.MAX_VIDEO_BYTES + MULTIPART_OVERHEAD_BYTES


def _declared_length(request: Request) -> int | None:
    """Content-Length, or None when absent or when Transfer-Encoding is also sent (then the
    length is not trustworthy; RFC 9112 says Transfer-Encoding wins)."""
    raw = request.headers.get("content-length")
    if raw is None or "transfer-encoding" in request.headers:
        return None
    raw = raw.strip()
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 20:
        raise _bad_upload(MSG_BAD_UPLOAD)
    return int(raw)


def _boundary(request: Request) -> bytes:
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise _bad_upload("Send the video as a file upload.")
    return boundary


async def _receive_video(
    request: Request, part: Path, boundary: bytes, declared: int | None
) -> str:
    """Stream the multipart field "file" into the new file `part`; returns the client's file
    name, which the caller only checks for its extension (never logged, never used in a path).

    The byte caps are enforced on what actually arrives, not on Content-Length, and the
    stream is dropped when it stalls (`UPLOAD_IDLE_TIMEOUT_S`) or runs past `UPLOAD_MAX_S`.
    """
    file_cap = phone.MAX_VIDEO_BYTES
    body_cap = _body_cap()
    st: dict[str, Any] = {"field": b"", "value": b"", "headers": {}, "in_file": False}
    found: list[str] = []
    ended = [False]
    written = [0]
    pending = bytearray()

    def on_part_begin() -> None:
        st["headers"] = {}
        st["in_file"] = False

    def on_header_field(data: bytes, start: int, end: int) -> None:
        st["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        st["value"] += data[start:end]

    def on_header_end() -> None:
        st["headers"][bytes(st["field"]).lower()] = bytes(st["value"])
        st["field"] = b""
        st["value"] = b""

    def on_headers_finished() -> None:
        disposition, params = parse_options_header(st["headers"].get(b"content-disposition", b""))
        if disposition != b"form-data" or params.get(b"name") != b"file":
            return  # other fields are read and dropped
        if found:
            raise _bad_upload("Send one video at a time.")
        filename = params.get(b"filename")
        if filename is None:
            raise _bad_upload("Choose a video file.")
        name = filename.decode("utf-8", "replace")
        try:
            phone.check_upload_name(name)  # fail before up to 4 GB is streamed
        except FkError as exc:
            raise _bad_upload(exc.message) from exc
        found.append(name)
        st["in_file"] = True

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if not st["in_file"]:
            return
        written[0] += end - start
        if written[0] > file_cap:
            raise _bad_upload(MSG_TOO_LARGE)
        pending.extend(data[start:end])

    def on_part_end() -> None:
        st["in_file"] = False

    def on_end() -> None:
        ended[0] = True

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
            "on_end": on_end,
        },
    )
    received = 0
    deadline = anyio.current_time() + UPLOAD_MAX_S
    with os.fdopen(_new_file(part), "wb") as out:
        try:
            more = True
            while more:
                wait_s = min(UPLOAD_IDLE_TIMEOUT_S, deadline - anyio.current_time())
                if wait_s <= 0:
                    raise _bad_upload(MSG_UPLOAD_STALLED)
                try:
                    with anyio.fail_after(wait_s):
                        message = await request.receive()
                except TimeoutError as exc:
                    raise _bad_upload(MSG_UPLOAD_STALLED) from exc
                if message["type"] == "http.disconnect":
                    raise ClientDisconnect()
                if message["type"] != "http.request":
                    continue
                more = bool(message.get("more_body", False))
                chunk = message.get("body", b"")
                received += len(chunk)
                if received > body_cap:
                    raise _bad_upload(MSG_TOO_LARGE)
                if declared is not None and received > declared:
                    raise _bad_upload(MSG_BAD_UPLOAD)
                parser.write(chunk)
                if len(pending) >= UPLOAD_CHUNK_BYTES:
                    block = bytes(pending)
                    pending.clear()
                    await anyio.to_thread.run_sync(out.write, block)
            parser.finalize()
        except MultipartParseError as exc:
            raise _bad_upload(MSG_BAD_UPLOAD) from exc
        if pending:
            await anyio.to_thread.run_sync(out.write, bytes(pending))
    if not ended[0]:
        raise _bad_upload(MSG_BAD_UPLOAD)
    if not found:
        raise _bad_upload("Choose a video file.")
    return found[0]


def _drop_uploads(project_dir: Path) -> None:
    """Remove every leftover `<sid>.upload` (e.g. from a job cancelled while queued).

    Only called while holding the server-wide analysis slot, so no job reads one of them.
    `*.part` files are left alone: session-file temps use that name too.
    """
    if os.environ.get(KEEP_VIDEO_ENV) == "1":
        return
    for entry in _phone_dir(project_dir).glob("*.upload"):
        try:
            entry.unlink()
        except OSError:
            continue


def _cleanup(token: str | None, part: Path, dest: Path, key: str) -> None:
    part.unlink(missing_ok=True)
    if token is not None:  # only the slot holder may touch the session's upload
        dest.unlink(missing_ok=True)
        _BUSY.release(key, token)


@router.post("/projects/{pid}/phone-scans/{sid}/video", status_code=202, response_model=None)
async def upload_phone_video(
    pid: str, sid: str, request: Request, state: State
) -> dict[str, str] | JSONResponse:
    project_dir = state.store.project_dir(pid)  # NOT_FOUND for an unknown project
    _phone_dir(project_dir)  # created here; refused when it is a symlink
    dest = phone.phone_upload_path(project_dir, sid)  # refuses any id but ^phs_[0-9a-f]{8}$
    session = _load_session(project_dir, sid)  # NOT_FOUND when unknown or expired
    key = f"analysis:{id(state)}"  # one phone analysis at a time on this server
    part = _temp_name(dest)
    token: str | None = None
    try:
        declared = _declared_length(request)
        if declared is not None and declared > _body_cap():
            raise _bad_upload(MSG_TOO_LARGE)
        boundary = _boundary(request)
        needed = (declared if declared is not None else _body_cap()) + FREE_DISK_MARGIN_BYTES
        if shutil.disk_usage(project_dir).free < needed:
            raise _bad_upload(MSG_NO_SPACE)
        token = _BUSY.claim(key)
        if token is None:
            return _conflict(MSG_ANALYSIS_BUSY)
        _sweep_phone_dir(project_dir)  # no analysis runs now, so nothing in use is removed
        _drop_uploads(project_dir)
        filename = await _receive_video(request, part, boundary, declared)
        try:
            phone.check_upload_name(filename)
        except FkError as exc:
            raise _bad_upload(exc.message) from exc
        try:
            phone.check_video_file(part)  # container magic bytes, before ffmpeg ever runs
        except FkError as exc:
            status = 400 if exc.code == BAD_REQUEST else 422
            raise _Reject(status, exc.code, exc.message) from exc
        os.replace(part, dest)  # we hold the analysis slot, so no job is reading `dest`

        advisor = _phone_advisor(state, session.learn)
        settings = CalibSettings(skip_lsb=session.skip_lsb, min_conf=session.min_conf)
        proj_size = (session.proj_w, session.proj_h)
        claim = token

        def video_job(progress: Progress, cancel: Cancel) -> dict[str, Any]:
            try:
                cal = phone.run_phone_calibration(
                    project_dir,
                    dest,
                    settings,
                    proj_size=proj_size,
                    strategy=session.strategy,
                    advisor=advisor,
                    progress=progress,
                    cancel=cancel,
                )
                result = _finish(state, pid, cal.id)
                _session_path(project_dir, sid).unlink(missing_ok=True)
                return result
            except FkError:
                raise
            except Exception as exc:  # no paths or internals in the job error
                log.exception("phone video analysis failed")
                raise FkError(INTERNAL, MSG_PHONE_JOB_FAILED) from exc
            finally:
                if os.environ.get(KEEP_VIDEO_ENV) != "1":
                    dest.unlink(missing_ok=True)
                _BUSY.release(key, claim)

        job = state.jobs.submit("calibration", video_job)
        _BUSY.attach(key, token, state.jobs, job.id)
        return {"job_id": job.id}
    except _Reject as rej:
        _cleanup(token, part, dest, key)
        return JSONResponse(
            status_code=rej.status, content={"error": {"code": rej.code, "message": rej.message}}
        )
    except ClientDisconnect:
        _cleanup(token, part, dest, key)
        return JSONResponse(
            status_code=400, content={"error": {"code": BAD_REQUEST, "message": MSG_BAD_UPLOAD}}
        )
    except BaseException:
        _cleanup(token, part, dest, key)
        raise
