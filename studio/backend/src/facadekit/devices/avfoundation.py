"""Ask macOS which cameras exist and whether this process may use them, without opening one.

OpenCV can only say "index 2 did not open". AVFoundation knows the names, and reading them
does not turn a camera on or need the camera permission. The calls go through the
Objective-C runtime with `ctypes`, so there is no extra dependency.

The index of a device is its position in the list OpenCV builds
(`cap_avfoundation_mac.mm`): video devices, then muxed devices, sorted by `uniqueID`.
`devices()` builds the same list the same way, so `devices()[i]` is `cv2.VideoCapture(i)`.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, Literal

AuthStatus = Literal["not_determined", "restricted", "denied", "authorized"]
_AUTH: dict[int, AuthStatus] = {
    0: "not_determined",
    1: "restricted",
    2: "denied",
    3: "authorized",
}
Kind = Literal["built_in", "external", "phone", "unknown"]

_lock = threading.Lock()


@dataclass(frozen=True)
class AvDevice:
    unique_id: str
    name: str
    model: str
    device_type: str  # e.g. "AVCaptureDeviceTypeBuiltInWideAngleCamera"; "" when unknown

    @property
    def kind(self) -> Kind:
        return classify(self.name, self.model, self.device_type)


def classify(name: str, model: str, device_type: str) -> Kind:
    text = f"{name} {model}".lower()
    if "continuity" in device_type.lower() or "iphone" in text or "ipad" in text:
        return "phone"
    if "builtin" in device_type.lower():
        return "built_in"
    if "external" in device_type.lower():
        return "external"
    return "unknown"


class _Runtime:
    """The handful of Objective-C calls needed, typed one call at a time."""

    def __init__(self) -> None:
        objc_path = ctypes.util.find_library("objc")
        if objc_path is None:
            raise OSError("libobjc not found")
        self.objc = ctypes.CDLL(objc_path)
        self.avf = ctypes.CDLL("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
        self.objc.objc_getClass.restype = ctypes.c_void_p
        self.objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = ctypes.c_void_p
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.video = ctypes.c_void_p.in_dll(self.avf, "AVMediaTypeVideo")
        self.muxed = ctypes.c_void_p.in_dll(self.avf, "AVMediaTypeMuxed")
        self.device_class = self.objc.objc_getClass(b"AVCaptureDevice")
        if not self.device_class:
            raise OSError("AVCaptureDevice class not found")

    def send(self, restype: Any, receiver: Any, selector: bytes, *args: Any) -> Any:
        """objc_msgSend with pointer arguments (all the calls here take objects only)."""
        fn = self.objc.objc_msgSend
        fn.restype = restype
        fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_void_p] * len(args)
        return fn(receiver, self.objc.sel_registerName(selector), *args)

    def responds(self, receiver: Any, selector: bytes) -> bool:
        fn = self.objc.objc_msgSend
        fn.restype = ctypes.c_bool
        fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        return bool(
            fn(
                receiver,
                self.objc.sel_registerName(b"respondsToSelector:"),
                self.objc.sel_registerName(selector),
            )
        )

    def text(self, nsstring: Any) -> str:
        if not nsstring:
            return ""
        raw = self.send(ctypes.c_char_p, nsstring, b"UTF8String")
        return raw.decode("utf-8", "replace") if raw else ""

    def array(self, nsarray: Any) -> list[Any]:
        if not nsarray:
            return []
        count = int(self.send(ctypes.c_ulong, nsarray, b"count"))
        fn = self.objc.objc_msgSend
        out = []
        for i in range(count):
            fn.restype = ctypes.c_void_p
            fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            out.append(fn(nsarray, self.objc.sel_registerName(b"objectAtIndex:"), i))
        return out


@functools.cache
def _runtime() -> _Runtime | None:
    if sys.platform != "darwin":
        return None
    try:
        return _Runtime()
    except (OSError, ValueError, AttributeError):
        return None


def available() -> bool:
    return _runtime() is not None


def authorization() -> AuthStatus | None:
    """Camera permission of this process (really: of the app that started it). None: unknown."""
    rt = _runtime()
    if rt is None:
        return None
    with _lock:
        status = rt.send(
            ctypes.c_long, rt.device_class, b"authorizationStatusForMediaType:", rt.video
        )
    return _AUTH.get(int(status))


def devices() -> list[AvDevice]:
    """Capture devices in OpenCV's index order. Opens nothing."""
    rt = _runtime()
    if rt is None:
        return []
    with _lock:
        pool = rt.send(ctypes.c_void_p, rt.objc.objc_getClass(b"NSAutoreleasePool"), b"new")
        try:
            handles = []
            for media in (rt.video, rt.muxed):
                listed = rt.send(ctypes.c_void_p, rt.device_class, b"devicesWithMediaType:", media)
                handles.extend(rt.array(listed))
            found = []
            for handle in handles:
                unique = rt.send(ctypes.c_void_p, handle, b"uniqueID")
                device_type = (
                    rt.text(rt.send(ctypes.c_void_p, handle, b"deviceType"))
                    if rt.responds(handle, b"deviceType")
                    else ""
                )
                found.append(
                    (
                        unique,
                        AvDevice(
                            unique_id=rt.text(unique),
                            name=rt.text(rt.send(ctypes.c_void_p, handle, b"localizedName")),
                            model=rt.text(rt.send(ctypes.c_void_p, handle, b"modelID")),
                            device_type=device_type,
                        ),
                    )
                )

            def by_unique_id(a: tuple[Any, AvDevice], b: tuple[Any, AvDevice]) -> int:
                # The comparison OpenCV sorts with, so the order cannot differ from its own.
                return int(rt.send(ctypes.c_long, a[0], b"compare:", b[0]))

            found.sort(key=functools.cmp_to_key(by_unique_id))
            return [device for _, device in found]
        finally:
            rt.send(None, pool, b"drain")


def host_app() -> str:
    """The app macOS lists under Privacy > Camera: the one this process was started from."""
    names = {
        "Apple_Terminal": "Terminal",
        "iTerm.app": "iTerm",
        "vscode": "Visual Studio Code",
        "WarpTerminal": "Warp",
        "ghostty": "Ghostty",
    }
    program = os.environ.get("TERM_PROGRAM", "")
    return names.get(program, program or "the app you started FacadeKit from")
