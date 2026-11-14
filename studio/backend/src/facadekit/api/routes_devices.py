from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Annotated

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from facadekit.api.deps import AppState, get_state
from facadekit.config import camera_size
from facadekit.devices import avfoundation
from facadekit.devices.base import Camera
from facadekit.devices.camera_opencv import (
    CameraInfo,
    OpenCvCamera,
    access_problem,
    named_cameras,
    probe_cameras,
)
from facadekit.errors import BAD_REQUEST, CAMERA_READ_FAILED, INTERNAL, NOT_FOUND, FkError

router = APIRouter()
log = logging.getLogger(__name__)

State = Annotated[AppState, Depends(get_state)]

SIM_CAMERA_ID = "sim"
IDLE_CLOSE_S = 10.0
BUSY_MESSAGE = "Camera is busy scanning"
GRAB_INTERVAL_S = 1 / 15  # the preview does not need more, and it keeps a slow laptop cool
FIRST_FRAME_TIMEOUT_S = 5.0
STALE_FRAME_S = 3.0  # no new picture for this long: the camera has stopped delivering
MAX_READ_FAILURES = 5
RETRY_AFTER_S = 2.0  # a camera that failed to open is not tried again before this


class CameraBusyError(FkError):
    """Raised while a scan job holds the camera; mapped to HTTP 409 by the routes."""

    def __init__(self) -> None:
        super().__init__(BAD_REQUEST, BUSY_MESSAGE)


class _LiveSession:
    """One open camera read by a background thread; `latest()` never waits for the device.

    Opening a webcam takes about a second and restarts its auto exposure, so the preview
    must not open it per picture. The thread keeps the newest frame; a snapshot copies it.
    """

    def __init__(
        self, camera_id: str, camera: Camera, *, interval: float, threaded: bool = True
    ) -> None:
        self.camera_id = camera_id
        self._camera = camera
        self._interval = interval
        self._threaded = threaded  # False: read when asked (the simulated camera)
        self._lock = threading.Lock()  # guards the camera object
        self._frame: np.ndarray | None = None
        self._frame_at = 0.0
        self._error: FkError | None = None
        self._have_frame = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"camera-{camera_id}", daemon=True)

    def start(self) -> None:
        self._camera.open()
        if self._threaded:
            self._thread.start()

    @property
    def resolution(self) -> tuple[int, int]:
        return self._camera.resolution

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                with self._lock:
                    frame = self._camera.read()
            except FkError as exc:
                failures += 1
                if failures >= MAX_READ_FAILURES:
                    log.warning("camera %s stopped delivering: %s", self.camera_id, exc.message)
                    self._error = exc
                    self._have_frame.set()
                    return
            except Exception as exc:
                log.exception("camera %s reader crashed", self.camera_id)
                self._error = FkError(CAMERA_READ_FAILED, f"Camera stopped working: {exc}")
                self._have_frame.set()
                return
            else:
                failures = 0
                self._frame, self._frame_at = frame, time.monotonic()
                self._have_frame.set()
            self._stop.wait(self._interval)

    def latest(self) -> np.ndarray:
        if not self._threaded:
            with self._lock:
                return self._camera.read()
        if not self._have_frame.wait(FIRST_FRAME_TIMEOUT_S):
            raise FkError(CAMERA_READ_FAILED, "The camera opened but sent no picture.")
        if self._error is not None:
            raise self._error
        frame = self._frame
        assert frame is not None
        if time.monotonic() - self._frame_at > STALE_FRAME_S:
            raise FkError(CAMERA_READ_FAILED, "The camera stopped sending pictures.")
        return frame

    @property
    def error(self) -> FkError | None:
        """Set once the reader thread has given up on the camera."""
        return self._error

    def set_controls(self, **controls: bool | float | None) -> dict[str, bool]:
        with self._lock:
            return self._camera.set_controls(**controls)  # type: ignore[arg-type]

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        # A driver that hangs inside read() still holds the lock: do not wait for it forever.
        if not self._lock.acquire(timeout=2):
            log.warning("camera %s did not stop reading; leaving it open", self.camera_id)
            return
        try:
            self._camera.close()
        finally:
            self._lock.release()


class CameraManager:
    """Owns at most one open preview camera and hands cameras to scan jobs.

    `camera_factory` and `lister` replace the real devices in tests.
    """

    def __init__(
        self,
        *,
        sim_camera_factory: Callable[[], Camera] | None = None,
        camera_factory: Callable[[str], Camera] | None = None,
        lister: Callable[[], list[CameraInfo] | None] | None = None,
        prober: Callable[[dict[int, CameraInfo]], list[CameraInfo]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sim_factory = sim_camera_factory
        self._camera_factory = camera_factory
        self._lister = lister or (lambda: named_cameras(camera_size()))
        self._prober = prober or (lambda known: probe_cameras(known=known))
        self._clock = clock
        self._lock = threading.Lock()
        self._session: _LiveSession | None = None
        self._last_used = 0.0
        self._busy: set[str] = set()
        self._timer: threading.Timer | None = None
        self._listed: list[CameraInfo] | None = None  # None: not looked yet
        self._sizes: dict[str, tuple[int, int]] = {}  # true picture size, once a camera opened
        self._failed: dict[str, tuple[float, FkError]] = {}
        self.opens = 0  # how often a device was opened (tests and diagnostics)

    # -- listing -----------------------------------------------------------

    def list(self, *, refresh: bool = False) -> list[CameraInfo]:
        """The cameras, with real names where the system gives them.

        Where the system can list devices (macOS, Linux) no camera is opened and the list is
        always current. Elsewhere cameras are found by opening them: once, then again only
        when `refresh` is set, never while a scan runs, and never the camera in use.
        """
        if self._sim_factory is not None:
            camera = self._sim_factory()
            width, height = camera.resolution
            return [
                CameraInfo(id=SIM_CAMERA_ID, name="Simulated camera", width=width, height=height)
            ]
        named = self._lister()
        with self._lock:
            if named is not None:
                self._listed = named
            elif self._listed is None or (refresh and not self._busy):
                known: dict[int, CameraInfo] = {}
                session = self._session
                if session is not None and self._listed is not None:
                    for info in self._listed:
                        if info.id == session.camera_id:
                            known[int(info.id)] = info
                self._listed = self._prober(known)
            return [self._with_size(info) for info in self._listed]

    def _with_size(self, info: CameraInfo) -> CameraInfo:
        size = self._sizes.get(info.id)
        if size is None:
            return info
        return info.model_copy(update={"width": size[0], "height": size[1]})

    # -- opening -----------------------------------------------------------

    def _new_camera(self, camera_id: str) -> Camera:
        if self._sim_factory is not None:
            if camera_id == SIM_CAMERA_ID:
                return self._sim_factory()
        elif camera_id.isdecimal() and camera_id.isascii():
            if self._camera_factory is not None:
                return self._camera_factory(camera_id)
            return OpenCvCamera(int(camera_id), *camera_size())
        raise FkError(NOT_FOUND, f"camera {camera_id!r} not found")

    def validate(self, camera_id: str) -> None:
        """Raise NOT_FOUND unless `camera_id` is a valid id for this mode."""
        self._new_camera(camera_id)

    def _close_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        session, self._session = self._session, None
        if session is not None:
            session.stop()

    def _expire(self) -> None:
        with self._lock:
            if self._session is not None and self._clock() - self._last_used >= IDLE_CLOSE_S:
                self._close_locked()

    def _touch_locked(self) -> None:
        self._last_used = self._clock()
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_CLOSE_S + 0.1, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _preview_locked(self, camera_id: str) -> _LiveSession:
        if camera_id in self._busy:
            raise CameraBusyError()
        session = self._session
        if session is not None and session.camera_id == camera_id:
            if session.error is None:
                self._touch_locked()
                return session
            # The reader gave up. Report it once and close; reopening waits RETRY_AFTER_S.
            self._close_locked()
            self._failed[camera_id] = (self._clock(), session.error)
            raise session.error
        # Build (and so validate) the new camera before touching the open one: a request for
        # a camera that does not exist must not switch off the one that is being watched.
        camera = self._new_camera(camera_id)
        failed = self._failed.get(camera_id)
        if failed is not None and self._clock() - failed[0] < RETRY_AFTER_S:
            raise failed[1]  # asked again too soon: answer from memory, leave the device alone
        self._close_locked()
        session = _LiveSession(
            camera_id, camera, interval=GRAB_INTERVAL_S, threaded=self._sim_factory is None
        )
        self.opens += 1
        try:
            session.start()
        except FkError as exc:
            self._failed[camera_id] = (self._clock(), exc)
            raise
        self._failed.pop(camera_id, None)
        self._sizes[camera_id] = session.resolution
        self._session = session
        self._touch_locked()
        return session

    def snapshot(self, camera_id: str) -> np.ndarray:
        with self._lock:
            session = self._preview_locked(camera_id)
        try:
            return session.latest()
        except FkError as exc:
            with self._lock:
                if self._session is session:  # close it; the next request may try again
                    self._close_locked()
                    self._failed[camera_id] = (self._clock(), exc)
            raise

    def set_controls(self, camera_id: str, **controls: bool | float | None) -> dict[str, bool]:
        with self._lock:
            session = self._preview_locked(camera_id)
        return session.set_controls(**controls)

    def acquire(self, camera_id: str) -> Camera:
        """Close any preview camera, mark `camera_id` busy and return a fresh unopened Camera."""
        with self._lock:
            if camera_id in self._busy:
                raise CameraBusyError()
            camera = self._new_camera(camera_id)
            self._close_locked()
            self._failed.pop(camera_id, None)
            self._busy.add(camera_id)
            return camera

    def busy(self) -> bool:
        """True while a scan or camera check holds a camera."""
        with self._lock:
            return bool(self._busy)

    def release(self, camera_id: str) -> None:
        with self._lock:
            self._busy.discard(camera_id)

    def close(self) -> None:
        with self._lock:
            self._close_locked()


class CameraAccess(BaseModel):
    """Whether the operating system lets FacadeKit use cameras at all."""

    status: str  # "authorized" | "denied" | "restricted" | "not_determined" | "unknown"
    message: str | None  # what the user should do; None when nothing is wrong


def camera_access(*, sim: bool) -> CameraAccess:
    if sim:
        return CameraAccess(status="authorized", message=None)
    status = avfoundation.authorization() if sys.platform == "darwin" else None
    return CameraAccess(status=status or "unknown", message=access_problem())


class ControlsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_exposure: bool | None = None
    exposure: float | None = None
    auto_wb: bool | None = None
    autofocus: bool | None = None


def _busy_response(exc: CameraBusyError) -> JSONResponse:
    return JSONResponse(
        status_code=409, content={"error": {"code": exc.code, "message": exc.message}}
    )


def _parse_crop(crop: str) -> tuple[int, int, int, int]:
    parts = crop.split(",")
    if len(parts) != 4 or not all(p.isascii() and p.isdecimal() for p in parts):
        raise FkError(BAD_REQUEST, "crop: expected x,y,w,h as four non-negative integers")
    x, y, w, h = (int(p) for p in parts)
    return x, y, w, h


def _apply_crop(frame: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Clamp the crop to the frame and slice (before JPEG encoding)."""
    x, y, w, h = box
    fh, fw = frame.shape[:2]
    x0, y0 = min(x, fw), min(y, fh)
    x1, y1 = min(x + w, fw), min(y + h, fh)
    if x1 <= x0 or y1 <= y0:
        raise FkError(BAD_REQUEST, "crop: lies outside the camera picture")
    return np.ascontiguousarray(frame[y0:y1, x0:x1])


@router.get("/devices/cameras")
def get_cameras(state: State, refresh: bool = False) -> list[CameraInfo]:
    return state.cameras.list(refresh=refresh)


@router.get("/devices/cameras/access")
def get_camera_access(state: State) -> CameraAccess:
    return camera_access(sim=state.sim)


@router.get("/devices/cameras/{cid}/snapshot.jpg")
def camera_snapshot(cid: str, state: State, crop: str | None = None) -> Response:
    box = _parse_crop(crop) if crop is not None else None
    try:
        frame = state.cameras.snapshot(cid)
    except CameraBusyError as exc:
        return _busy_response(exc)
    if box is not None:
        frame = _apply_crop(frame, box)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise FkError(INTERNAL, "snapshot could not be encoded as JPEG")
    return Response(
        content=buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


@router.post("/devices/cameras/{cid}/controls", response_model=None)
def camera_controls(
    cid: str, body: ControlsRequest, state: State
) -> dict[str, dict[str, bool]] | JSONResponse:
    try:
        applied = state.cameras.set_controls(cid, **body.model_dump(exclude_none=True))
    except CameraBusyError as exc:
        return _busy_response(exc)
    return {"applied": applied}
