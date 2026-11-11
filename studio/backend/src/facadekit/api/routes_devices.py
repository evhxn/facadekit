from __future__ import annotations

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
from facadekit.devices.base import Camera
from facadekit.devices.camera_opencv import CameraInfo, OpenCvCamera, list_cameras
from facadekit.errors import BAD_REQUEST, INTERNAL, NOT_FOUND, FkError

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]

SIM_CAMERA_ID = "sim"
IDLE_CLOSE_S = 10.0
BUSY_MESSAGE = "Camera is busy scanning"


class CameraBusyError(FkError):
    """Raised while a scan job holds the camera; mapped to HTTP 409 by the routes."""

    def __init__(self) -> None:
        super().__init__(BAD_REQUEST, BUSY_MESSAGE)


class CameraManager:
    """Owns at most one open camera at a time and serialises access with a lock."""

    def __init__(self, *, sim_camera_factory: Callable[[], Camera] | None = None) -> None:
        self._sim_factory = sim_camera_factory
        self._lock = threading.Lock()
        self._open_id: str | None = None
        self._open: Camera | None = None
        self._last_used = 0.0
        self._busy: set[str] = set()
        self._timer: threading.Timer | None = None

    def list(self) -> list[CameraInfo]:
        if self._sim_factory is not None:
            camera = self._sim_factory()
            width, height = camera.resolution
            return [
                CameraInfo(id=SIM_CAMERA_ID, name="Simulated camera", width=width, height=height)
            ]
        return list_cameras()

    def _new_camera(self, camera_id: str) -> Camera:
        if self._sim_factory is not None:
            if camera_id == SIM_CAMERA_ID:
                return self._sim_factory()
        elif camera_id.isdecimal() and camera_id.isascii():
            return OpenCvCamera(int(camera_id), *camera_size())
        raise FkError(NOT_FOUND, f"camera {camera_id!r} not found")

    def validate(self, camera_id: str) -> None:
        """Raise NOT_FOUND unless `camera_id` is a valid id for this mode."""
        self._new_camera(camera_id)

    def _close_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._open is not None:
            try:
                self._open.close()
            finally:
                self._open = None
                self._open_id = None

    def _expire(self) -> None:
        with self._lock:
            if self._open is not None and time.monotonic() - self._last_used >= IDLE_CLOSE_S:
                self._close_locked()

    def _touch_locked(self) -> None:
        self._last_used = time.monotonic()
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_CLOSE_S + 0.1, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _preview_locked(self, camera_id: str) -> Camera:
        if camera_id in self._busy:
            raise CameraBusyError()
        if self._open is not None and time.monotonic() - self._last_used >= IDLE_CLOSE_S:
            self._close_locked()
        if self._open is None or self._open_id != camera_id:
            self._close_locked()
            camera = self._new_camera(camera_id)
            camera.open()
            self._open, self._open_id = camera, camera_id
        self._touch_locked()
        assert self._open is not None
        return self._open

    def snapshot(self, camera_id: str) -> np.ndarray:
        with self._lock:
            return self._preview_locked(camera_id).read()

    def set_controls(self, camera_id: str, **controls: bool | float | None) -> dict[str, bool]:
        with self._lock:
            camera = self._preview_locked(camera_id)
            return camera.set_controls(**controls)  # type: ignore[arg-type]

    def acquire(self, camera_id: str) -> Camera:
        """Close any preview camera, mark `camera_id` busy and return a fresh unopened Camera."""
        with self._lock:
            if camera_id in self._busy:
                raise CameraBusyError()
            camera = self._new_camera(camera_id)
            self._close_locked()
            self._busy.add(camera_id)
            return camera

    def release(self, camera_id: str) -> None:
        with self._lock:
            self._busy.discard(camera_id)

    def close(self) -> None:
        with self._lock:
            self._close_locked()


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
def get_cameras(state: State) -> list[CameraInfo]:
    return state.cameras.list()


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
