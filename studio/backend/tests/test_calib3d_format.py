"""calib3d capture format v1 (docs/18): round trips and validation errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from facadekit.calib.patterns import PatternSpec, pattern_sequence, render_pattern
from facadekit.calib3d.capture_format import (
    FORMAT_NAME,
    FORMAT_VERSION,
    CaptureFormatError,
    CaptureWriter,
    load_capture,
)
from facadekit.calib3d.geometry import ARKIT_TO_OPENCV, Intrinsics, look_at

CAM = Intrinsics(120.0, 120.0, 79.5, 59.5, 160, 120)
SPEC = PatternSpec(64, 32, skip_lsb=0)


def _pose(x: float = 0.0) -> np.ndarray:
    return look_at(np.array([x, 0.5, 2.0]), np.array([0.0, 0.5, 0.0]), np.array([0.0, 1.0, 0.0]))


def _write(root: Path, *, with_graycode: bool = True) -> tuple[CaptureWriter, dict[str, Any]]:
    rng = np.random.default_rng(0)
    w = CaptureWriter(root)
    data: dict[str, Any] = {"depth": [], "conf": [], "images": []}
    for i in range(2):
        img = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)
        depth = rng.uniform(1.0, 3.0, (24, 32)).astype(np.float32)
        depth[0, 0] = np.nan  # no data
        conf = rng.integers(0, 3, (24, 32)).astype(np.uint8)
        w.add_shot(f"s{i:03d}", 1.5 + i, img, CAM, _pose(0.1 * i), depth=depth, confidence=conf)
        data["depth"].append(depth)
        data["conf"].append(conf)
        data["images"].append(img)
    if with_graycode:
        frames = pattern_sequence(SPEC)
        imgs = [cv2.resize(render_pattern(SPEC, f), (160, 120), interpolation=cv2.INTER_NEAREST)
                for f in frames]  # fmt: skip
        imgs = [cv2.cvtColor(im, cv2.COLOR_GRAY2BGR) if f.kind == "white" else im
                for f, im in zip(frames, imgs, strict=True)]  # fmt: skip
        w.set_graycode_frames("s000", SPEC, frames, imgs, timestamp_s=0.5)
        data["frames"] = imgs
    return w, data


def _manifest(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = json.loads((root / "manifest.json").read_text())
    return out


def _rewrite(root: Path, m: dict[str, Any]) -> None:
    (root / "manifest.json").write_text(json.dumps(m))


def _problems(root: Path) -> list[str]:
    with pytest.raises(CaptureFormatError) as exc:
        load_capture(root)
    assert exc.value.code == "BAD_REQUEST"
    return exc.value.problems


def test_round_trip(tmp_path: Path) -> None:
    w, data = _write(tmp_path)
    w.write(created="2026-11-23T10:00:00Z", device={"model": "test"})
    cap = load_capture(tmp_path)
    assert [s.id for s in cap.shots] == ["s000", "s001"]
    assert cap.created == "2026-11-23T10:00:00Z" and cap.device == {"model": "test"}
    assert cap.ground_truth is None and cap.warnings == []
    for i, shot in enumerate(cap.shots):
        assert shot.timestamp_s == pytest.approx(1.5 + i)
        assert shot.intrinsics == CAM
        assert np.allclose(shot.pose, _pose(0.1 * i))
        assert np.array_equal(cap.load_image(shot), data["images"][i])
        depth, conf, dintr = cap.load_depth(shot)
        ref = data["depth"][i]
        assert np.isnan(depth[0, 0])
        assert np.array_equal(depth[1:], ref[1:]) and np.array_equal(depth[0, 1:], ref[0, 1:])
        assert conf is not None and np.array_equal(conf, data["conf"][i])
        assert dintr == CAM.scaled(32, 24)  # default: image intrinsics scaled to the depth size
    g = cap.graycode
    assert g.kind == "frames" and g.pattern == SPEC and g.timestamp_s == 0.5
    cs = cap.load_graycode()
    assert cs.spec == SPEC and cs.frames == pattern_sequence(SPEC)
    assert cs.images.shape == (len(cs.frames), 120, 160)
    for f, img, ref_img in zip(cs.frames, cs.images, data["frames"], strict=True):
        gray = cv2.cvtColor(ref_img, cv2.COLOR_BGR2GRAY) if f.kind == "white" else ref_img
        assert np.array_equal(img, gray.astype(np.float32))
    # writing the loaded manifest again gives the same manifest
    m1 = _manifest(tmp_path)
    w.write(created="2026-11-23T10:00:00Z", device={"model": "test"})
    assert _manifest(tmp_path) == m1


def test_arkit_poses_are_converted(tmp_path: Path) -> None:
    w, _ = _write(tmp_path)
    w.write()
    m = _manifest(tmp_path)
    m["pose_convention"] = "arkit"
    for s in m["shots"]:
        s["pose"] = (np.asarray(s["pose"]) @ np.linalg.inv(ARKIT_TO_OPENCV)).tolist()
    _rewrite(tmp_path, m)
    cap = load_capture(tmp_path)
    assert np.allclose(cap.shots[1].pose, _pose(0.1))


def test_png_depth_in_millimetres(tmp_path: Path) -> None:
    w, _ = _write(tmp_path)
    w.write()
    mm = np.full((24, 32), 2500, np.uint16)
    mm[3, 4] = 0
    cv2.imwrite(str(tmp_path / "shots" / "d.png"), mm)
    m = _manifest(tmp_path)
    m["shots"][0]["depth"] = {"file": "shots/d.png", "scale_m": 0.001}
    _rewrite(tmp_path, m)
    cap = load_capture(tmp_path)
    depth, conf, _ = cap.load_depth(cap.shots[0])
    assert conf is None
    assert depth[0, 0] == pytest.approx(2.5) and np.isnan(depth[3, 4])


def test_unknown_key_is_a_warning(tmp_path: Path) -> None:
    w, _ = _write(tmp_path)
    w.write()
    m = _manifest(tmp_path)
    m["future_field"] = 1
    _rewrite(tmp_path, m)
    assert load_capture(tmp_path).warnings == ["unknown top-level key 'future_field' ignored"]


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        (lambda m: m.update(format="other"), "format: must be"),
        (lambda m: m.update(version=FORMAT_VERSION + 1), "newer than this FacadeKit reads"),
        (lambda m: m.update(version="1"), "version: must be 1"),
        (lambda m: m.update(pose_convention="opengl"), "pose_convention"),
        (lambda m: m.update(units="feet"), "units"),
        (lambda m: m.update(shots=[]), "shots: must be a non-empty list"),
        (lambda m: m["shots"][1].update(id="s000"), "duplicate id"),
        (lambda m: m["shots"][0].update(id="a b"), "must match"),
        (lambda m: m["shots"][0].update(image="../escape.png"), "relative path inside"),
        (lambda m: m["shots"][0].update(image="/abs/x.png"), "relative path inside"),
        (lambda m: m["shots"][0].update(image="shots/missing.png"), "file not found"),
        (lambda m: m["shots"][0].update(image="shots/s000.bmp"), "must end in one of"),
        (lambda m: m["shots"][0].update(timestamp_s=-1), "timestamp_s: must be >= 0"),
        (lambda m: m["shots"][0].update(timestamp_s="now"), "timestamp_s: must be a finite number"),
        (lambda m: m["shots"][0]["intrinsics"].pop("fx"), "missing fx"),
        (lambda m: m["shots"][0]["intrinsics"].update(fy=0), "fy: must be > 0"),
        (
            lambda m: m["shots"][0]["intrinsics"].update(width=1.5),
            "width: must be a positive integer",
        ),
        (lambda m: m["shots"][0]["intrinsics"].update(cx=1e6), "principal point"),
        (lambda m: m["shots"][0].update(pose=[[1, 0, 0], [0, 1, 0]]), "4x4 matrix"),
        (lambda m: m["shots"][0].update(pose=(2 * np.eye(4)).tolist()), "last row"),
        (
            lambda m: m["shots"][0].update(pose=np.diag([2.0, 1, 1, 1]).tolist()),
            "must be a rotation",
        ),
        (
            lambda m: m["shots"][0].update(pose=np.diag([-1.0, 1, 1, 1]).tolist()),
            "must be a rotation",
        ),
        (lambda m: m["shots"][0]["depth"].update(file="shots/x.tiff"), "must end in one of"),
        (lambda m: [s.pop("depth") for s in m["shots"]], "no shot has depth"),
        (lambda m: m["graycode"].update(shot="nope"), "graycode.shot"),
        (lambda m: m["graycode"].update(kind="video"), "graycode.kind"),
        (lambda m: m["graycode"]["frames"].pop(3), "1 frame(s) missing"),
        (
            lambda m: m["graycode"]["frames"].append(dict(m["graycode"]["frames"][2])),
            "listed twice",
        ),
        (lambda m: m["graycode"]["pattern"].update(skip_lsb=99), "skip_lsb"),
        (lambda m: m["graycode"]["frames"][2].pop("axis"), "needs axis"),
        (lambda m: m.update(ground_truth={"projector": {}}), "only the simulator"),
    ],
)
def test_validation_errors(tmp_path: Path, change: Any, expect: str) -> None:
    w, _ = _write(tmp_path)
    w.write()
    m = _manifest(tmp_path)
    change(m)
    _rewrite(tmp_path, m)
    problems = _problems(tmp_path)
    assert any(expect in p for p in problems), problems


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    w, _ = _write(tmp_path)
    w.write()
    m = _manifest(tmp_path)
    m["version"] = 7
    m["shots"][0]["intrinsics"]["fx"] = -1
    m["shots"][1]["image"] = "nope.png"
    _rewrite(tmp_path, m)
    assert len(_problems(tmp_path)) == 3


def test_missing_and_broken_manifest(tmp_path: Path) -> None:
    assert "manifest.json not found" in _problems(tmp_path)[0]
    (tmp_path / "manifest.json").write_text("{not json")
    assert "not valid JSON" in _problems(tmp_path)[0]
    (tmp_path / "manifest.json").write_text("[]")
    assert "must be a JSON object" in _problems(tmp_path)[0]


def test_bad_arrays_are_caught_on_load(tmp_path: Path) -> None:
    w, _ = _write(tmp_path)
    w.write()
    cap = load_capture(tmp_path)
    cv2.imwrite(str(tmp_path / "shots" / "s000_confidence.png"), np.full((24, 32), 7, np.uint8))
    with pytest.raises(CaptureFormatError, match="0, 1 or 2"):
        cap.load_depth(cap.shots[0])
    np.save(tmp_path / "shots" / "s001_depth.npy", np.zeros((2, 3, 4), np.float32))
    with pytest.raises(CaptureFormatError, match="2-D float"):
        cap.load_depth(cap.shots[1])
    # a Gray-code frame of the wrong size
    cv2.imwrite(str(tmp_path / "graycode" / "x00.png"), np.zeros((10, 10), np.uint8))
    with pytest.raises(CaptureFormatError, match="is 10x10"):
        cap.load_graycode()


def test_calibration_reference(tmp_path: Path) -> None:
    w, _ = _write(tmp_path, with_graycode=False)
    (tmp_path / "cal").mkdir()
    np.save(tmp_path / "cal" / "p2c.npy", np.zeros((32, 64, 2), np.float32))
    w.set_graycode_calibration("s000", "cal")
    w.write()
    cap = load_capture(tmp_path)
    assert cap.graycode.kind == "calibration" and cap.graycode.path == "cal"
    m = _manifest(tmp_path)
    m["graycode"]["path"] = "../elsewhere"
    _rewrite(tmp_path, m)
    assert any("relative folder" in p for p in _problems(tmp_path))
    m["graycode"]["path"] = "nocal"
    _rewrite(tmp_path, m)
    assert any("p2c.npy not found" in p for p in _problems(tmp_path))


def test_format_constants() -> None:
    assert FORMAT_NAME == "facadekit-calib3d-capture" and FORMAT_VERSION == 1
