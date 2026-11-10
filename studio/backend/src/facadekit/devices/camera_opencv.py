from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel

from facadekit import errors
from facadekit.errors import FkError

# Best-guess camera control values; to be corrected by hardware spike S-3.
# Keys are platform names: "v4l2" (Linux), "dshow" (Windows), "avfoundation" (macOS).
_CONTROL_TABLE: dict[str, dict[str, Any]] = {
    "v4l2": {
        "backend": "CAP_V4L2",
        "ae_manual": 0.25,
        "ae_auto": 0.75,
        "exposure_base": 1,
        "exposure_span": 999,
    },
    "dshow": {
        "backend": "CAP_DSHOW",
        "ae_manual": 0,
        "ae_auto": 1,
        "exposure_base": -11,
        "exposure_span": 10,
    },
    "avfoundation": {
        "backend": "CAP_AVFOUNDATION",
        "ae_manual": 0,
        "ae_auto": 1,
        "exposure_base": None,  # AVFoundation ignores exposure
        "exposure_span": 0,
    },
}


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "dshow"
    if sys.platform == "darwin":
        return "avfoundation"
    return "v4l2"


def _default_capture_factory(index: int) -> Any:
    backend = int(getattr(cv2, _CONTROL_TABLE[_platform_key()]["backend"], cv2.CAP_ANY))
    cap = cv2.VideoCapture(index, backend)
    if cap.isOpened():
        return cap
    cap.release()
    return cv2.VideoCapture(index, cv2.CAP_ANY)


class CameraInfo(BaseModel):
    id: str
    name: str
    width: int
    height: int


def list_cameras(
    max_index: int = 5,
    capture_factory: Callable[[int], Any] | None = None,
) -> list[CameraInfo]:
    factory = capture_factory or _default_capture_factory
    found: list[CameraInfo] = []
    failures = 0
    for i in range(max_index):
        ok = False
        cap = None
        try:
            cap = factory(i)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    h, w = frame.shape[:2]
                    found.append(CameraInfo(id=str(i), name=f"Camera {i}", width=w, height=h))
                    ok = True
        except Exception:
            ok = False
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
        if ok:
            failures = 0
        elif found:
            failures += 1
            if failures >= 2:
                break
    return found


class OpenCvCamera:
    def __init__(
        self,
        index: int,
        width: int = 1280,
        height: int = 720,
        capture_factory: Callable[[int], Any] | None = None,
    ) -> None:
        self._index = index
        self._req_width = width
        self._req_height = height
        self._factory = capture_factory or _default_capture_factory
        self._cap: Any = None
        self._resolution = (width, height)
        self._platform = _platform_key()
        self._table = _CONTROL_TABLE[self._platform]

    @property
    def resolution(self) -> tuple[int, int]:
        return self._resolution

    def open(self) -> None:
        self.close()
        cap = self._factory(self._index)
        try:
            if not cap.isOpened():
                raise FkError(errors.CAMERA_OPEN_FAILED, f"Could not open camera {self._index}")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._req_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._req_height)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            ret, frame = cap.read()
            if not ret or frame is None:
                raise FkError(errors.CAMERA_OPEN_FAILED, f"Could not open camera {self._index}")
        except BaseException:
            try:
                cap.release()
            except Exception:
                pass
            raise
        h, w = frame.shape[:2]
        self._resolution = (int(w), int(h))
        self._cap = cap

    def close(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()

    def read(self) -> np.ndarray:
        if self._cap is None:
            raise FkError(errors.CAMERA_READ_FAILED, "camera is not open")
        for _ in range(2):
            if self._cap.grab():
                ret, frame = self._cap.retrieve()
                if ret and frame is not None:
                    return np.asarray(frame)
        raise FkError(errors.CAMERA_READ_FAILED, f"Could not read from camera {self._index}")

    def _set(self, prop: int, value: float) -> bool:
        if self._cap is None:
            return False
        try:
            return bool(self._cap.set(prop, value))
        except Exception:
            return False

    def set_controls(
        self,
        *,
        auto_exposure: bool | None = None,
        exposure: float | None = None,
        auto_wb: bool | None = None,
        autofocus: bool | None = None,
    ) -> dict[str, bool]:
        result: dict[str, bool] = {}
        t = self._table
        if auto_exposure is not None:
            key = "ae_auto" if auto_exposure else "ae_manual"
            first = t[key]
            alt = _CONTROL_TABLE["dshow" if self._platform == "v4l2" else "v4l2"][key]
            ok = self._set(cv2.CAP_PROP_AUTO_EXPOSURE, first)
            if not ok:
                ok = self._set(cv2.CAP_PROP_AUTO_EXPOSURE, alt)
            result["auto_exposure"] = ok
        if exposure is not None:
            base = t["exposure_base"]
            if base is None:
                result["exposure"] = False
            else:
                value = round(base + min(max(exposure, 0.0), 1.0) * t["exposure_span"])
                result["exposure"] = self._set(cv2.CAP_PROP_EXPOSURE, value)
        if auto_wb is not None:
            result["auto_wb"] = self._set(cv2.CAP_PROP_AUTO_WB, 1 if auto_wb else 0)
        if autofocus is not None:
            result["autofocus"] = self._set(cv2.CAP_PROP_AUTOFOCUS, 1 if autofocus else 0)
        return result
