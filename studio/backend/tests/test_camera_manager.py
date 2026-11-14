"""Camera layer regressions from the first hardware test (report items A to D)."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.api import routes_devices
from facadekit.api.app import create_app
from facadekit.api.routes_devices import CameraBusyError, CameraManager
from facadekit.devices import avfoundation, camera_opencv
from facadekit.devices.avfoundation import AvDevice
from facadekit.devices.camera_opencv import (
    CameraInfo,
    OpenCvCamera,
    access_problem,
    named_cameras,
    prepare_camera_access,
    probe_cameras,
    v4l_cameras,
)
from facadekit.errors import FkError

API = "/api/v1"


class FakeCamera:
    """Counts opens and closes; frames carry a running number in every pixel."""

    def __init__(self, log: list[str], name: str, *, fail_open: bool = False) -> None:
        self.log = log
        self.name = name
        self.fail_open = fail_open
        self.fail_reads = False
        self.count = 0
        self.is_open = False

    @property
    def resolution(self) -> tuple[int, int]:
        return (64, 48)

    def open(self) -> None:
        self.log.append(f"open {self.name}")
        if self.fail_open:
            raise FkError("CAMERA_OPEN_FAILED", f"Could not open camera {self.name}")
        self.is_open = True

    def close(self) -> None:
        if self.is_open:
            self.log.append(f"close {self.name}")
        self.is_open = False

    def read(self) -> np.ndarray:
        if not self.is_open or self.fail_reads:
            raise FkError("CAMERA_READ_FAILED", "no picture")
        self.count += 1
        return np.full((48, 64, 3), self.count % 256, np.uint8)

    def set_controls(self, **controls: object) -> dict[str, bool]:
        return {name: True for name in controls}


class Rig:
    def __init__(self, *, fail_open: set[str] | None = None) -> None:
        self.log: list[str] = []
        self.made: list[FakeCamera] = []
        self.fail_open = fail_open or set()
        self.now = 1000.0
        self.manager = CameraManager(
            camera_factory=self._make,
            lister=lambda: [CameraInfo(id="0", name="Desk cam", width=1280, height=720)],
            clock=lambda: self.now,
        )

    def _make(self, camera_id: str) -> FakeCamera:
        camera = FakeCamera(self.log, camera_id, fail_open=camera_id in self.fail_open)
        self.made.append(camera)
        return camera

    def opens(self) -> int:
        return sum(1 for line in self.log if line.startswith("open"))


@pytest.fixture
def rig() -> Rig:
    r = Rig()
    yield r  # type: ignore[misc]
    r.manager.close()


def test_preview_opens_the_camera_once_for_many_snapshots(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item C: the preview must not open and release the device per picture."""
    monkeypatch.setattr(routes_devices, "GRAB_INTERVAL_S", 0.001)
    for _ in range(30):
        rig.manager.snapshot("0")
    assert rig.log == ["open 0"]
    assert rig.manager.opens == 1


def test_snapshot_does_not_wait_for_the_device(rig: Rig) -> None:
    """The reader thread holds the newest frame; a snapshot only copies it."""
    rig.manager.snapshot("0")  # opens, waits for the first frame
    started = time.perf_counter()
    for _ in range(200):
        rig.manager.snapshot("0")
    assert (time.perf_counter() - started) / 200 < 0.005


def test_frames_keep_arriving_between_snapshots(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_devices, "GRAB_INTERVAL_S", 0.001)
    first = int(rig.manager.snapshot("0")[0, 0, 0])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and rig.made[0].count < first + 5:
        time.sleep(0.005)
    assert int(rig.manager.snapshot("0")[0, 0, 0]) != first


def test_unknown_camera_does_not_close_the_one_in_use(rig: Rig) -> None:
    """Item D caused item C's pulse: a stale tab asked for camera "sim" once a second, and
    every such request closed the real camera before answering 404."""
    rig.manager.snapshot("0")
    for _ in range(5):
        with pytest.raises(FkError) as err:
            rig.manager.snapshot("sim")
        assert err.value.code == "NOT_FOUND"
        rig.manager.snapshot("0")
    assert rig.log == ["open 0"]


def test_failed_open_is_not_retried_in_a_tight_loop() -> None:
    """Item A: a camera that will not open was opened again on every poll."""
    rig = Rig(fail_open={"0"})
    for _ in range(20):
        with pytest.raises(FkError) as err:
            rig.manager.snapshot("0")
        assert err.value.code == "CAMERA_OPEN_FAILED"
    assert rig.opens() == 1
    rig.now += routes_devices.RETRY_AFTER_S + 0.1
    with pytest.raises(FkError):
        rig.manager.snapshot("0")
    assert rig.opens() == 2


def test_dead_camera_reports_an_error_and_is_closed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes_devices, "GRAB_INTERVAL_S", 0.001)
    rig.manager.snapshot("0")
    rig.made[0].fail_reads = True
    deadline = time.monotonic() + 5
    code = None
    while time.monotonic() < deadline and code is None:
        try:
            rig.manager.snapshot("0")
            time.sleep(0.01)
        except FkError as exc:
            code = exc.code
    assert code == "CAMERA_READ_FAILED"
    assert rig.log == ["open 0", "close 0"]
    with pytest.raises(FkError):  # asked again at once: no second open yet
        rig.manager.snapshot("0")
    assert rig.opens() == 1


def test_scan_takes_the_camera_from_the_preview(rig: Rig) -> None:
    rig.manager.snapshot("0")
    camera = rig.manager.acquire("0")
    assert rig.log == ["open 0", "close 0"]
    assert camera is rig.made[-1] and not camera.is_open  # type: ignore[comparison-overlap]
    with pytest.raises(CameraBusyError):
        rig.manager.snapshot("0")
    rig.manager.release("0")
    rig.manager.snapshot("0")
    assert rig.opens() == 2


def test_reader_thread_ends_when_the_manager_closes(rig: Rig) -> None:
    rig.manager.snapshot("0")
    assert any(t.name == "camera-0" for t in threading.enumerate())
    rig.manager.close()
    assert not any(t.name == "camera-0" and t.is_alive() for t in threading.enumerate())


def test_listing_reports_the_true_size_once_a_camera_was_opened(rig: Rig) -> None:
    assert rig.manager.list()[0].width == 1280
    rig.manager.snapshot("0")
    info = rig.manager.list()[0]
    assert (info.name, info.width, info.height) == ("Desk cam", 64, 48)


# -- listing without opening (item B) ------------------------------------------------------


def _fake_mac(
    monkeypatch: pytest.MonkeyPatch, status: str | None = "authorized", app: str = "Terminal"
) -> None:
    devices = [
        AvDevice("0x1", "MacBook Pro Camera", "MacBook Pro Camera", "AVCaptureDeviceTypeBuiltIn"),
        AvDevice("0x2", "Sam’s iPhone Camera", "iPhone18,1", "AVCaptureDeviceTypeExternal"),
        AvDevice("0x3", "Logi C920", "UVC Camera", "AVCaptureDeviceTypeExternal"),
    ]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(avfoundation, "available", lambda: True)
    monkeypatch.setattr(avfoundation, "devices", lambda: devices)
    monkeypatch.setattr(avfoundation, "authorization", lambda: status)
    monkeypatch.setattr(avfoundation, "host_app", lambda: app)


def test_mac_cameras_are_named_and_no_camera_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_mac(monkeypatch)
    opened: list[int] = []
    monkeypatch.setattr(camera_opencv, "_default_capture_factory", opened.append)
    manager = CameraManager()
    for _ in range(3):
        listed = manager.list(refresh=True)
    assert [(c.id, c.name, c.kind) for c in listed] == [
        ("0", "MacBook Pro Camera", "built_in"),
        ("1", "Sam’s iPhone Camera", "phone"),
        ("2", "Logi C920", "external"),
    ]
    assert opened == []


def test_linux_cameras_are_named_from_sysfs(tmp_path: Path) -> None:
    for number, index, name in ((0, 0, "HD Webcam: HD Webcam"), (1, 1, "HD Webcam: HD Webcam"),
                                (2, 0, "USB Capture"), (10, 0, "Late one")):  # fmt: skip
        node = tmp_path / f"video{number}"
        node.mkdir()
        (node / "index").write_text(f"{index}\n")
        (node / "name").write_text(f"{name}\n")
    (tmp_path / "v4l-subdev0").mkdir()
    cams = v4l_cameras((1280, 720), tmp_path)
    assert cams is not None
    assert [(c.id, c.name) for c in cams] == [
        ("0", "HD Webcam: HD Webcam"),
        ("2", "USB Capture"),
        ("10", "Late one"),
    ]
    assert v4l_cameras((1280, 720), tmp_path / "missing") is None


def test_probing_platform_probes_once_then_only_on_refresh() -> None:
    calls: list[dict[int, CameraInfo]] = []

    def prober(known: dict[int, CameraInfo]) -> list[CameraInfo]:
        calls.append(dict(known))
        return [CameraInfo(id="0", name="Camera 0", width=640, height=480)]

    log: list[str] = []
    manager = CameraManager(
        lister=lambda: None, prober=prober, camera_factory=lambda cid: FakeCamera(log, cid)
    )
    try:
        for _ in range(5):
            manager.list()
        assert len(calls) == 1  # the first hardware log shows a probe on every listing
        manager.snapshot("0")
        manager.list(refresh=True)
        assert len(calls) == 2
        assert list(calls[1]) == [0]  # the camera in use is passed as known, not opened again
        manager.release("0")
        manager.acquire("0")  # a scan is running
        manager.list(refresh=True)
        assert len(calls) == 2
    finally:
        manager.close()


def test_probe_skips_known_cameras() -> None:
    opened: list[int] = []

    class Cap:
        def __init__(self, index: int) -> None:
            opened.append(index)

        def isOpened(self) -> bool:  # noqa: N802
            return False

        def release(self) -> None:
            pass

    known = {0: CameraInfo(id="0", name="Camera 0", width=640, height=480)}
    assert probe_cameras(5, capture_factory=Cap, known=known) == [known[0]]
    assert 0 not in opened


# -- macOS permission (item A) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        ("denied", "System Settings > Privacy & Security > Camera, switch on iTerm"),
        ("not_determined", "Click Allow"),
        ("restricted", "does not allow camera use"),
    ],
)
def test_access_problem_says_what_to_do(
    monkeypatch: pytest.MonkeyPatch, status: str, needle: str
) -> None:
    _fake_mac(monkeypatch, status, app="iTerm")
    problem = access_problem()
    assert problem is not None and needle in problem


def test_no_access_problem_when_authorized_or_not_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_mac(monkeypatch, "authorized")
    assert access_problem() is None
    _fake_mac(monkeypatch, "denied")
    monkeypatch.setattr(sys, "platform", "linux")
    assert access_problem() is None


def test_real_camera_is_not_opened_without_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenCV would print `not authorized ... can not spin main run loop` and fail."""
    _fake_mac(monkeypatch, "denied")
    opened: list[int] = []
    monkeypatch.setattr(camera_opencv, "_default_capture_factory", opened.append)
    with pytest.raises(FkError) as err:
        OpenCvCamera(0).open()
    assert err.value.code == "CAMERA_ACCESS_DENIED"
    assert "System Settings" in err.value.message
    assert opened == []


def test_denied_camera_is_asked_once_not_per_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_mac(monkeypatch, "denied")
    asked: list[int] = []
    monkeypatch.setattr(avfoundation, "authorization", lambda: asked.append(1) or "denied")
    manager = CameraManager()
    for _ in range(50):
        with pytest.raises(FkError) as err:
            manager.snapshot("0")
        assert err.value.code == "CAMERA_ACCESS_DENIED"
    assert manager.opens == 1 and len(asked) == 1


def test_permission_is_requested_on_the_calling_thread_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_mac(monkeypatch, "not_determined")
    monkeypatch.delenv("OPENCV_AVFOUNDATION_SKIP_AUTH", raising=False)
    threads: list[str] = []

    class Cap:
        def release(self) -> None:
            pass

    def factory(index: int) -> Cap:
        threads.append(threading.current_thread().name)
        return Cap()

    message = prepare_camera_access(factory)
    assert threads == [threading.current_thread().name]
    assert message is not None and "Click Allow" in message
    import os

    assert os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] == "1"  # OpenCV must not ask again


def test_no_request_when_already_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCV_AVFOUNDATION_SKIP_AUTH", raising=False)
    for status, has_message in (("authorized", False), ("denied", True)):
        _fake_mac(monkeypatch, status)
        opened: list[int] = []
        message = prepare_camera_access(opened.append)
        assert opened == [] and (message is not None) == has_message


def test_named_cameras_is_none_where_the_system_cannot_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert named_cameras((1280, 720)) is None


# -- routes --------------------------------------------------------------------------------


def test_access_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_mac(monkeypatch, "denied")
    with TestClient(create_app(data_dir=tmp_path)) as c:
        body = c.get(f"{API}/devices/cameras/access").json()
        assert body["status"] == "denied" and "System Settings" in body["message"]
        listed = c.get(f"{API}/devices/cameras").json()
        assert [cam["name"] for cam in listed][0] == "MacBook Pro Camera"
        res = c.get(f"{API}/devices/cameras/0/snapshot.jpg")
        assert res.status_code == 500
        assert res.json()["error"]["code"] == "CAMERA_ACCESS_DENIED"
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as c:
        assert c.get(f"{API}/devices/cameras/access").json() == {
            "status": "authorized",
            "message": None,
        }


def test_sim_listing_and_stale_real_id(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as c:
        assert c.get(f"{API}/devices/cameras?refresh=true").json()[0]["id"] == "sim"
        assert c.get(f"{API}/devices/cameras/0/snapshot.jpg").status_code == 404
        assert c.get(f"{API}/devices/cameras/sim/snapshot.jpg").status_code == 200
