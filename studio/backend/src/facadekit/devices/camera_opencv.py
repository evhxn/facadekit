from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel

from facadekit import errors
from facadekit.devices import avfoundation
from facadekit.errors import FkError

log = logging.getLogger(__name__)

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
    kind: str = "unknown"  # "built_in" | "external" | "phone" | "unknown"


def probe_cameras(
    max_index: int = 5,
    capture_factory: Callable[[int], Any] | None = None,
    known: Mapping[int, CameraInfo] | None = None,
) -> list[CameraInfo]:
    """Find cameras by opening index 0, 1, 2... Slow, and it switches each camera on.

    Only for platforms that cannot list devices any other way. An index in `known` (a camera
    that is in use right now) is taken from there and not opened a second time.
    """
    factory = capture_factory or _default_capture_factory
    known = known or {}
    found: list[CameraInfo] = []
    failures = 0
    for i in range(max_index):
        if i in known:
            found.append(known[i])
            failures = 0
            continue
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


list_cameras = probe_cameras  # the name before cameras could be listed without opening them

V4L_ROOT = Path("/sys/class/video4linux")


def v4l_cameras(size: tuple[int, int], root: Path = V4L_ROOT) -> list[CameraInfo] | None:
    """Linux: names from sysfs. None when sysfs has no video4linux tree (then probe)."""
    if not root.is_dir():
        return None
    found: list[tuple[int, CameraInfo]] = []
    for node in root.glob("video*"):
        number = node.name.removeprefix("video")
        if not (number.isascii() and number.isdecimal()):
            continue
        try:
            # One camera makes several nodes; only the first ("index" 0) delivers pictures.
            if (node / "index").read_text().strip() != "0":
                continue
            name = (node / "name").read_text().strip()
        except OSError:
            continue
        info = CameraInfo(
            id=number,
            name=name or f"Camera {number}",
            width=size[0],
            height=size[1],
            kind="unknown",
        )
        found.append((int(number), info))
    return [info for _, info in sorted(found, key=lambda item: item[0])]


def named_cameras(size: tuple[int, int]) -> list[CameraInfo] | None:
    """Cameras with their real names, found without opening any. None: not possible here.

    `size` is the picture size FacadeKit asks for; the true size is known once a camera is open.
    """
    if sys.platform == "darwin":
        if not avfoundation.available():
            return None
        return [
            CameraInfo(
                id=str(i),
                name=device.name or f"Camera {i}",
                width=size[0],
                height=size[1],
                kind=device.kind,
            )
            for i, device in enumerate(avfoundation.devices())
        ]
    if sys.platform.startswith("linux"):
        return v4l_cameras(size)
    return None


def access_problem() -> str | None:
    """Why macOS will not let this process use a camera, as a message for the user; else None."""
    if sys.platform != "darwin":
        return None
    status = avfoundation.authorization()
    app = avfoundation.host_app()
    if status == "denied":
        return (
            f"macOS is blocking the camera for {app}. Open System Settings > Privacy & "
            f"Security > Camera, switch on {app}, then quit and restart FacadeKit."
        )
    if status == "restricted":
        return (
            "This Mac does not allow camera use (a restriction set by its administrator or "
            "by Screen Time). Use the phone scan instead."
        )
    if status == "not_determined":
        return (
            f"macOS has not been told yet whether {app} may use the camera. Click Allow in "
            "the macOS window that asks. If no window is showing, quit and restart FacadeKit."
        )
    return None


def prepare_camera_access(capture_factory: Callable[[int], Any] | None = None) -> str | None:
    """Call once from the main thread before the server starts (macOS only does anything).

    macOS shows its "allow camera" window only when the first request comes from the main
    thread. Requests from server threads printed `can not spin main run loop from other
    thread` and failed. So ask here once, then tell OpenCV to stop asking on its own.
    Returns the message to print, if any.
    """
    if sys.platform != "darwin" or not avfoundation.available():
        return None
    if avfoundation.authorization() == "not_determined":
        factory = capture_factory or (lambda i: cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION))
        try:
            factory(0).release()  # OpenCV asks macOS; this first open is expected to fail
        except Exception:
            log.warning("could not ask macOS for camera access", exc_info=True)
    os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"
    return access_problem()


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
        self._real = capture_factory is None  # a test's fake capture needs no macOS permission
        self._cap: Any = None
        self._resolution = (width, height)
        self._platform = _platform_key()
        self._table = _CONTROL_TABLE[self._platform]

    @property
    def resolution(self) -> tuple[int, int]:
        return self._resolution

    def open(self) -> None:
        self.close()
        problem = access_problem() if self._real else None
        if problem is not None:  # do not let OpenCV try: it only prints and fails
            raise FkError(errors.CAMERA_ACCESS_DENIED, problem)
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
