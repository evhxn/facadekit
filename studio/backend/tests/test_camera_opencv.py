from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import pytest

from facadekit.devices.camera_opencv import OpenCvCamera, list_cameras
from facadekit.errors import FkError


class FakeCapture:
    def __init__(
        self, opened: bool = True, size: tuple[int, int] = (640, 480), retrieve_ok: bool = True
    ) -> None:
        self.opened = opened
        self.size = size
        self.retrieve_ok = retrieve_ok
        self.released = False
        self.set_result = True
        self.set_calls: list[tuple[int, float]] = []

    def isOpened(self) -> bool:  # noqa: N802
        return self.opened

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return self.set_result

    def get(self, prop: int) -> float:
        return 0.0

    def _frame(self) -> np.ndarray:
        return np.zeros((self.size[1], self.size[0], 3), dtype=np.uint8)

    def grab(self) -> bool:
        return True

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        return (True, self._frame()) if self.retrieve_ok else (False, None)

    def read(self) -> tuple[bool, np.ndarray | None]:
        return (True, self._frame()) if self.opened else (False, None)

    def release(self) -> None:
        self.released = True


def test_open_and_read() -> None:
    cap = FakeCapture(size=(640, 480))
    cam = OpenCvCamera(0, capture_factory=lambda i: cap)
    cam.open()
    assert cam.resolution == (640, 480)
    frame = cam.read()
    assert frame.shape == (480, 640, 3)
    assert frame.dtype == np.uint8
    cam.close()
    cam.close()
    assert cap.released


def test_open_failed() -> None:
    cap = FakeCapture(opened=False)
    cam = OpenCvCamera(3, capture_factory=lambda i: cap)
    with pytest.raises(FkError) as exc:
        cam.open()
    assert exc.value.code == "CAMERA_OPEN_FAILED"


def test_read_failed() -> None:
    cap = FakeCapture(retrieve_ok=False)
    cam = OpenCvCamera(0, capture_factory=lambda i: cap)
    cam.open()
    with pytest.raises(FkError) as exc:
        cam.read()
    assert exc.value.code == "CAMERA_READ_FAILED"


def test_set_controls_mirrors_fake() -> None:
    cap = FakeCapture()
    cam = OpenCvCamera(0, capture_factory=lambda i: cap)
    cam.open()
    res = cam.set_controls(auto_exposure=False, exposure=0.5)
    assert set(res) == {"auto_exposure", "exposure"}
    assert res["auto_exposure"] is True
    cap.set_result = False
    res = cam.set_controls(auto_exposure=False, exposure=0.5)
    assert res == {"auto_exposure": False, "exposure": False}
    assert cv2.CAP_PROP_AUTO_EXPOSURE in {p for p, _ in cap.set_calls}


def test_list_cameras_releases_all() -> None:
    caps: list[Any] = []

    def factory(i: int) -> FakeCapture:
        cap = FakeCapture(opened=i in (0, 1))
        caps.append(cap)
        return cap

    infos = list_cameras(5, capture_factory=factory)
    assert [c.id for c in infos] == ["0", "1"]
    assert infos[0].name == "Camera 0"
    assert (infos[0].width, infos[0].height) == (640, 480)
    assert caps and all(c.released for c in caps)
    assert len(caps) == 4  # stopped after 2 consecutive failures
