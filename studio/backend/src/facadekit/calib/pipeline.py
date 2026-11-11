from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from facadekit.calib.capture import CaptureSet, precheck, run_capture
from facadekit.calib.decode import decode
from facadekit.calib.learn import ScanAdvisor, Strategy
from facadekit.calib.maps import build_maps, make_proj_view
from facadekit.calib.patterns import PatternSpec
from facadekit.calib.tuning import measure_latency
from facadekit.devices.base import Camera, ProjectorDisplay
from facadekit.errors import FkError
from facadekit.ids import new_id
from facadekit.models.calibration import CalibQuality, Calibration, CalibSettings
from facadekit.models.jobs import utc_now_iso
from facadekit.models.project import ProjectorSpec

_CAL_ID = re.compile(r"^cal_[0-9a-f]{8}$")

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]


def _check_cancel(cancel: Cancel | None) -> None:
    if cancel is not None and cancel():
        raise FkError("CANCELLED", "Calibration cancelled")


def _write_png(path: Path, image: np.ndarray) -> None:
    if not cv2.imwrite(str(path), image):
        raise FkError("INTERNAL", f"Could not write {path.name}")


def _commit(project_dir: Path, cal: Calibration, files: dict[str, np.ndarray]) -> None:
    """Write files into calibration/.tmp_<id>/ then atomically rename to calibration/<id>/."""
    root = project_dir / "calibration"
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".tmp_{cal.id}"
    final = root / cal.id
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        for name, data in files.items():
            if name.endswith(".npy"):
                np.save(tmp / name, data)
            else:
                _write_png(tmp / name, data)
        (tmp / "calibration.json").write_text(cal.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def run_calibration(
    project_dir: Path,
    display: ProjectorDisplay,
    camera: Camera,
    camera_id: str,
    settings: CalibSettings,
    *,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    advisor: ScanAdvisor | None = None,
) -> Calibration:
    """Scan and write a calibration.

    With an `advisor`, the scan strategy (skip_lsb, average_frames, min_conf) is chosen from
    earlier scans once the lighting has been measured, and the outcome is reported back.
    """

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    def capture_progress(fraction: float, message: str) -> None:
        report(0.05 + 0.75 * fraction, message)

    spec = PatternSpec(*display.size, skip_lsb=settings.skip_lsb)
    camera.open()
    try:
        tuned: CalibSettings | None = None
        if settings.settle_ms == 0:  # automatic: measure the projector-to-camera delay
            report(0.0, "Tuning camera…")
            lat = measure_latency(display, camera, clock=clock, sleep=sleep)
            settings = settings.model_copy(
                update={"settle_ms": lat.settle_ms, "discard_frames": lat.discard_frames}
            )
            tuned = settings
        else:
            report(0.0, "Checking setup")
        _check_cancel(cancel)
        pre = precheck(display, camera, spec, settings=tuned, sleep=sleep)
        report(0.05, "Checking setup")
        _check_cancel(cancel)
        strategy: Strategy | None = None
        if advisor is not None:
            strategy = advisor.choose(camera_id, pre)
            settings = strategy.apply(settings)
            spec = PatternSpec(*display.size, skip_lsb=settings.skip_lsb)
        capture = run_capture(
            display, camera, spec, settings, progress=capture_progress, cancel=cancel, sleep=sleep
        )
    finally:
        camera.close()

    try:
        cal = run_calibration_from_capture(
            project_dir,
            capture,
            camera_id,
            settings,
            progress=progress,
            cancel=cancel,
            pre=pre,
            strategy=strategy.id if strategy is not None else None,
        )
    except FkError as exc:
        if advisor is not None and strategy is not None and exc.code == "SCAN_FAILED":
            advisor.failed(camera_id, pre, strategy, exc.code)
        raise
    if advisor is not None and strategy is not None:
        advisor.succeeded(camera_id, pre, strategy, cal)
    return cal


def _contrast_stats(capture: CaptureSet) -> dict[str, float]:
    """Quality numbers from the black (first) and white (second) pattern images."""
    contrast = capture.images[1] - capture.images[0]
    thr = max(12.0, 0.15 * float(np.percentile(contrast, 95)))
    lit = contrast > thr
    median = float(np.median(contrast[lit])) if lit.any() else 0.0
    return {"lit_fraction": float(lit.mean()), "median_contrast": median}


def run_calibration_from_capture(
    project_dir: Path,
    capture: CaptureSet,
    camera_id: str,
    settings: CalibSettings,
    *,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
    pre: dict[str, float] | None = None,
    strategy: str | None = None,
) -> Calibration:
    """Decode a finished capture, build the maps and write the calibration files."""
    spec = capture.spec
    camera_h, camera_w = capture.images.shape[1:]
    if pre is None:  # a recorded (Pi-driven) capture had no separate precheck
        pre = _contrast_stats(capture)
        if pre["lit_fraction"] < 0.05:
            raise FkError(
                "CAMERA_NO_PROJECTION",
                "The camera does not see the projected light. Check that the camera faces the "
                "projection and that the projector is on.",
            )

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    _check_cancel(cancel)
    report(0.80, "Decoding")
    decoded = decode(capture, min_conf=settings.min_conf)
    _check_cancel(cancel)
    report(0.90, "Building map")
    maps = build_maps(decoded, spec)
    if maps.coverage < 0.50:
        pct = round(maps.coverage * 100)
        raise FkError("SCAN_FAILED", f"Only {pct}% of the projection was measured.")
    _check_cancel(cancel)
    proj_view = make_proj_view(capture.white_bgr, maps)

    warnings: list[str] = []
    if maps.coverage < 0.85:
        warnings.append("LOW_COVERAGE")
    if maps.planarity < 0.5:
        warnings.append("NON_PLANAR")
    if capture.controls_applied.get("auto_exposure") is False:
        warnings.append("CAMERA_CONTROLS_UNAVAILABLE")

    cal = Calibration(
        id=new_id("cal"),
        created_at=utc_now_iso(),
        mode="scan",
        projector_width=spec.width,
        projector_height=spec.height,
        camera_id=camera_id,
        camera_width=camera_w,
        camera_height=camera_h,
        settings=settings,
        quality=CalibQuality(
            coverage=maps.coverage,
            planarity=maps.planarity,
            median_contrast=pre["median_contrast"],
            lit_fraction=pre["lit_fraction"],
        ),
        h_c2p=maps.h_c2p.tolist() if maps.h_c2p is not None else None,
        warnings=warnings,
        strategy=strategy,
    )
    report(0.97, "Saving")
    _commit(
        project_dir,
        cal,
        {
            "proj_view.png": proj_view,
            "p2c.npy": maps.p2c.astype(np.float32),
            "measured_mask.png": maps.measured.astype(np.uint8) * 255,
        },
    )
    report(1.0, "Saving")
    return cal


def _manual_view(width: int, height: int) -> np.ndarray:
    img = np.full((height, width, 3), (40, 40, 40), np.uint8)
    line = (90, 90, 90)
    for x in range(0, width, 120):
        img[:, x] = line
    for y in range(0, height, 120):
        img[y, :] = line
    for y in range(0, height, 120):
        for x in range(0, width, 120):
            cv2.putText(
                img,
                f"{x},{y}",
                (x + 3, y + 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (140, 140, 140),
                1,
                cv2.LINE_AA,
            )
    return img


def create_manual_calibration(project_dir: Path, projector: ProjectorSpec) -> Calibration:
    cal = Calibration(
        id=new_id("cal"),
        created_at=utc_now_iso(),
        mode="manual",
        projector_width=projector.width,
        projector_height=projector.height,
        camera_id=None,
        camera_width=None,
        camera_height=None,
        settings=None,
        quality=None,
        h_c2p=None,
        warnings=[],
    )
    _commit(project_dir, cal, {"proj_view.png": _manual_view(projector.width, projector.height)})
    return cal


def load_calibration(project_dir: Path, calibration_id: str) -> Calibration:
    if not _CAL_ID.match(calibration_id):
        raise FkError("NOT_FOUND", f"Calibration {calibration_id} not found.")
    path = project_dir / "calibration" / calibration_id / "calibration.json"
    if not path.is_file():
        raise FkError("NOT_FOUND", f"Calibration {calibration_id} not found.")
    try:
        return Calibration.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise FkError("INTERNAL", f"Calibration {calibration_id} is unreadable: {e}") from e
