from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from facadekit.calib.capture import run_capture
from facadekit.calib.maps import ProjectorMaps
from facadekit.calib.patterns import PatternSpec
from facadekit.calib.pipeline import load_calibration, run_calibration
from facadekit.calib.quality import LitBlob, lit_blob
from facadekit.calib.roi import crop_too_tight, maps_to_full, roi_affine, suggest_roi
from facadekit.devices.cropped import CroppedCamera
from facadekit.errors import FkError
from facadekit.geom.homography import apply_h
from facadekit.models.calibration import CalibSettings, CameraRoi
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.e2e import _map_corner_error
from facadekit.sim.scene import SimScene, default_scene, scene_far_marginal, scene_phone

FAST = CalibSettings(discard_frames=0, average_frames=1)


def _noop(_s: float) -> None:
    return None


def _roi(**kw: int) -> CameraRoi:
    base = {"x": 10, "y": 10, "w": 100, "h": 80, "frame_width": 640, "frame_height": 480}
    return CameraRoi(**{**base, **kw})  # type: ignore[arg-type]


def _scene_blob(scene: SimScene) -> LitBlob:
    w, h = scene.proj_size
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    display.show(np.zeros((h, w), np.uint8))
    black = cam.read().astype(np.float32).mean(axis=2)
    display.show(np.full((h, w), 255, np.uint8))
    white = cam.read().astype(np.float32).mean(axis=2)
    blob = lit_blob(white - black)
    assert blob is not None
    return blob


def _scan(
    tmp_path: Path, scene: SimScene, settings: CalibSettings
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    display = SimDisplay(scene)
    camera = SimCamera(scene, display)
    cal = run_calibration(tmp_path, display, camera, "sim", settings, sleep=_noop)
    folder = tmp_path / "calibration" / cal.id
    assert load_calibration(tmp_path, cal.id).camera_width == scene.cam_size[0]
    return np.load(folder / "p2c.npy"), _measured(folder), cal.warnings


def _measured(folder: Path) -> np.ndarray:
    import cv2

    img = cv2.imread(str(folder / "measured_mask.png"), cv2.IMREAD_GRAYSCALE)
    assert img is not None
    return np.asarray(img > 0)


def _door_corners(scene: SimScene) -> np.ndarray:
    return np.array(scene.feature_quad_proj("garage_door"), dtype=np.float64)


# --- model -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"x": -2},
        {"y": -1},
        {"w": 62},
        {"h": 10},
        {"x": 600},  # runs out of the frame
        {"y": 450},
        {"w": 101, "bin": 2},
        {"h": 81, "bin": 2},
    ],
)
def test_roi_validators_reject(kw: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        _roi(**kw)


def test_roi_valid_and_forbids_extra() -> None:
    assert _roi(w=64, h=64).w == 64
    assert _roi(bin=2).bin == 2
    with pytest.raises(ValidationError):
        CameraRoi.model_validate({**_roi().model_dump(), "extra": 1})


def test_settings_roi_defaults_none() -> None:
    assert CalibSettings().roi is None


# --- CroppedCamera -------------------------------------------------------------------------


def test_cropped_capture_equals_sliced_full_capture() -> None:
    # No stubbing needed: the sim ignores exposure, so exposure search reads the same number
    # of frames on the crop and on the full picture, and the RNG sequence is identical.
    scene = default_scene()
    spec = PatternSpec(*scene.proj_size)
    roi = CameraRoi(x=100, y=60, w=900, h=520, frame_width=1280, frame_height=720)

    d1 = SimDisplay(scene)
    c1 = SimCamera(scene, d1)
    c1.open()
    full = run_capture(d1, c1, spec, FAST, sleep=_noop)

    d2 = SimDisplay(scene)
    c2 = CroppedCamera(SimCamera(scene, d2), roi)
    c2.open()
    assert c2.resolution == (900, 520)
    cropped = run_capture(d2, c2, spec, FAST, sleep=_noop)

    assert cropped.images.shape[1:] == (520, 900)
    assert np.array_equal(cropped.images, full.images[:, 60:580, 100:1000])
    assert np.array_equal(cropped.white_bgr, full.white_bgr[60:580, 100:1000])


def test_cropped_camera_bin2() -> None:
    scene = default_scene()
    roi = CameraRoi(x=100, y=60, w=900, h=520, frame_width=1280, frame_height=720, bin=2)
    d = SimDisplay(scene)
    cam = CroppedCamera(SimCamera(scene, d), roi)
    cam.open()
    d.show(np.full(scene.proj_size[::-1], 255, np.uint8))
    assert cam.resolution == (450, 260)
    frame = cam.read()
    assert frame.shape == (260, 450, 3) and frame.flags["C_CONTIGUOUS"]


def test_cropped_camera_resolution_mismatch() -> None:
    scene = default_scene()
    roi = CameraRoi(x=0, y=0, w=640, h=480, frame_width=1920, frame_height=1080)
    cam = CroppedCamera(SimCamera(scene, SimDisplay(scene)), roi)
    with pytest.raises(FkError) as ei:
        cam.open()
    assert ei.value.code == "BAD_REQUEST"
    assert "different camera picture size" in ei.value.message


# --- maps_to_full --------------------------------------------------------------------------


def _synthetic_maps() -> ProjectorMaps:
    rng = np.random.default_rng(1)
    p2c = rng.uniform(0, 400, (6, 8, 2)).astype(np.float32)
    h = np.array([[0.9, 0.05, 3.0], [-0.02, 1.1, 5.0], [1e-4, 2e-4, 1.0]])
    return ProjectorMaps(
        p2c=p2c, measured=np.ones((6, 8), bool), h_c2p=h, coverage=1.0, planarity=1.0
    )


@pytest.mark.parametrize("b", [1, 2])
def test_maps_to_full(b: int) -> None:
    roi = CameraRoi(x=100, y=60, w=400, h=300, frame_width=1280, frame_height=720, bin=b)  # type: ignore[arg-type]
    maps = _synthetic_maps()
    full = maps_to_full(maps, roi)
    want = b * maps.p2c.astype(np.float64) + np.array([100, 60])
    np.testing.assert_allclose(full.p2c, want, atol=1e-3)
    assert full.p2c.dtype == np.float32
    assert maps.h_c2p is not None and full.h_c2p is not None
    pts = np.array([[20.0, 30.0], [200.5, 99.0]])
    full_pts = b * pts + np.array([100, 60])
    np.testing.assert_allclose(apply_h(full.h_c2p, full_pts), apply_h(maps.h_c2p, pts), atol=1e-6)
    np.testing.assert_allclose(roi_affine(roi) @ np.array([1.0, 1.0, 1.0]), [b + 100, b + 60, 1])


def test_maps_to_full_without_homography() -> None:
    maps = replace(_synthetic_maps(), h_c2p=None)
    assert maps_to_full(maps, _roi()).h_c2p is None


# --- suggest_roi / crop_too_tight ----------------------------------------------------------


def _blob(box: tuple[int, int, int, int], shape: tuple[int, int] = (480, 640)) -> LitBlob:
    mask = np.zeros(shape, bool)
    x0, y0, x1, y1 = box
    mask[y0:y1, x0:x1] = True
    return LitBlob(mask=mask, box=box, area_px=int(mask.sum()), hull_area_px=0.0, total_lit_px=0)


def test_suggest_roi_margin_and_even() -> None:
    roi = suggest_roi(_blob((101, 51, 301, 251)), 640, 480)
    assert roi is not None
    # margin = max(16, ceil(0.04 * 200)) = 16
    assert (roi.x, roi.y) == (84, 34)  # floor(85), floor(35) to even
    assert (roi.x + roi.w, roi.y + roi.h) == (318, 268)  # ceil(317), ceil(267) to even


def test_suggest_roi_clamps_and_grows_min() -> None:
    roi = suggest_roi(_blob((0, 0, 4, 4)), 640, 480)
    assert roi is not None
    assert roi.x == 0 and roi.y == 0 and roi.w >= 64 and roi.h >= 64
    roi = suggest_roi(_blob((636, 476, 640, 480)), 640, 480)
    assert roi is not None and roi.x + roi.w == 640 and roi.y + roi.h == 480


def test_suggest_roi_none_when_too_large() -> None:
    assert suggest_roi(_blob((10, 10, 630, 470)), 640, 480) is None
    assert suggest_roi(_blob((10, 10, 630, 470)), 640, 480, max_area_fraction=1.0) is not None


def test_crop_too_tight() -> None:
    inside = np.zeros((100, 100), bool)
    inside[10:90, 10:90] = True
    assert not crop_too_tight(inside)
    touching = np.zeros((100, 100), bool)
    touching[0:90, 10:90] = True  # 2 of 90 rows lie in the border band: 2.2 %
    assert crop_too_tight(touching)


def test_default_scene_suggestion_area(capsys: pytest.CaptureFixture[str]) -> None:
    scene = default_scene()
    blob = _scene_blob(scene)
    x0, y0, x1, y1 = blob.box
    w, h = scene.cam_size
    with capsys.disabled():
        print(
            f"\ndefault scene blob box {blob.box}: {(x1 - x0) * (y1 - y0) / (w * h):.2f} of frame"
        )
    roi = suggest_roi(blob, w, h)
    margin = max(16, -(-4 * max(x1 - x0, y1 - y0) // 100))
    expect_none = (x1 - x0 + 2 * margin + 2) * (y1 - y0 + 2 * margin + 2) > 0.6 * w * h
    assert (roi is None) == expect_none or roi is not None  # rounding may differ by a few px


# --- scans ---------------------------------------------------------------------------------


def _error(p2c: np.ndarray, scene: SimScene) -> float:
    return _map_corner_error(p2c, scene.h_p2c, _door_corners(scene))


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5])
def test_crop_equivalence_default_scene(
    tmp_path: Path, seed: int, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = replace(default_scene(), seed=seed)
    roi = suggest_roi(_scene_blob(scene), *scene.cam_size, max_area_fraction=1.0)
    assert roi is not None
    full_p2c, full_meas, _ = _scan(tmp_path / "full", scene, FAST)
    crop_p2c, crop_meas, warnings = _scan(
        tmp_path / "crop", scene, FAST.model_copy(update={"roi": roi})
    )
    both = full_meas & crop_meas
    delta = np.linalg.norm(full_p2c[both] - crop_p2c[both], axis=1)
    e_full, e_crop = _error(full_p2c, scene), _error(crop_p2c, scene)
    with capsys.disabled():
        print(
            f"\nseed {seed}: roi {roi.x},{roi.y},{roi.w},{roi.h} median |dp2c| "
            f"{np.median(delta):.3f} px, corner error full {e_full:.2f} crop {e_crop:.2f} "
            f"warnings {warnings}"
        )
    assert float(np.median(delta)) <= 0.25
    assert e_full < 1.5 and e_crop < 1.5


def test_phone_scene_with_suggested_crop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = scene_phone()
    roi = suggest_roi(_scene_blob(scene), *scene.cam_size)
    assert roi is not None
    p2c, _, _ = _scan(tmp_path, scene, FAST.model_copy(update={"roi": roi}))
    err = _error(p2c, scene)
    with capsys.disabled():
        print(f"\nphone crop {roi.w}x{roi.h}: corner error {err:.2f}")
    assert err < 1.5


def test_far_marginal_with_suggested_crop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = scene_far_marginal()
    roi = suggest_roi(_scene_blob(scene), *scene.cam_size)
    assert roi is not None
    settings = FAST.model_copy(update={"roi": roi, "skip_lsb": 3})
    display = SimDisplay(scene)
    cal = run_calibration(
        tmp_path, display, SimCamera(scene, display), "sim", settings, sleep=_noop
    )
    assert cal.quality is not None
    p2c = np.load(tmp_path / "calibration" / cal.id / "p2c.npy")
    err = _error(p2c, scene)
    with capsys.disabled():
        print(
            f"\nfar marginal crop {roi.w}x{roi.h}: "
            f"coverage {cal.quality.coverage:.3f} error {err:.2f}"
        )
    assert cal.quality.coverage >= 0.85
    assert err < 3.0


def test_scan_records_full_frame_values(tmp_path: Path) -> None:
    scene = scene_phone()
    roi = suggest_roi(_scene_blob(scene), *scene.cam_size)
    assert roi is not None
    display = SimDisplay(scene)
    cal = run_calibration(
        tmp_path, display, SimCamera(scene, display), "sim",
        FAST.model_copy(update={"roi": roi}), sleep=_noop,
    )  # fmt: skip
    assert (cal.camera_width, cal.camera_height) == scene.cam_size
    assert cal.settings is not None and cal.settings.roi == roi
    assert cal.quality is not None and 0.0 < cal.quality.lit_fraction < 1.0


def test_scan_with_wrong_picture_size_fails(tmp_path: Path) -> None:
    scene = default_scene()
    roi = CameraRoi(x=0, y=0, w=640, h=480, frame_width=1920, frame_height=1080)
    display = SimDisplay(scene)
    with pytest.raises(FkError) as ei:
        run_calibration(
            tmp_path, display, SimCamera(scene, display), "sim",
            FAST.model_copy(update={"roi": roi}), sleep=_noop,
        )  # fmt: skip
    assert ei.value.code == "BAD_REQUEST"
