"""The calib3d capture format, version 1 (spec: docs/18-calib3d-capture-format.md).

A capture is a folder with ``manifest.json`` and the files it names. ``load_capture`` checks
everything it can before any array is used and reports every problem at once;
``CaptureWriter`` writes the same format (the simulator and the tests use it).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import cv2
import numpy as np

from facadekit.calib.capture import CaptureSet
from facadekit.calib.patterns import PatternFrame, PatternSpec, pattern_sequence
from facadekit.calib3d.geometry import Intrinsics, pose_from_arkit
from facadekit.errors import BAD_REQUEST, FkError

FORMAT_NAME = "facadekit-calib3d-capture"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
DEPTH_SUFFIXES = (".npy", ".png")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

PoseConvention = Literal["opencv", "arkit"]


class CaptureFormatError(FkError):
    """Every problem found in a capture folder, one per line in ``problems``."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        lines = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(
            BAD_REQUEST, f"Invalid calib3d capture ({len(problems)} problem(s)):\n{lines}"
        )


@dataclass
class DepthRef:
    file: str
    confidence: str | None = None
    intrinsics: Intrinsics | None = None  # None: the image intrinsics scaled to the depth size
    scale_m: float = 0.001  # metres per unit, 16-bit PNG depth only
    timestamp_s: float | None = None


@dataclass
class Shot:
    id: str
    timestamp_s: float
    image: str
    intrinsics: Intrinsics
    pose: np.ndarray  # 4x4 world_from_camera, OpenCV camera axes (converted on load)
    depth: DepthRef | None = None


@dataclass
class GrayFrameRef:
    frame: PatternFrame
    file: str


@dataclass
class GrayCodeRef:
    shot: str  # the shot whose intrinsics and pose are the Gray-code camera's
    kind: Literal["frames", "calibration"]
    path: str = ""  # folder for "calibration"; prefix for "frames" (files are relative to root)
    pattern: PatternSpec | None = None
    frames: list[GrayFrameRef] = field(default_factory=list)
    timestamp_s: float | None = None


@dataclass
class Capture3d:
    root: Path
    shots: list[Shot]
    graycode: GrayCodeRef
    created: str | None = None
    device: dict[str, Any] = field(default_factory=dict)
    ground_truth: dict[str, Any] | None = None  # simulator only; never used to solve
    warnings: list[str] = field(default_factory=list)

    def shot(self, shot_id: str) -> Shot:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(shot_id)

    @property
    def anchor(self) -> Shot:
        return self.shot(self.graycode.shot)

    def load_image(self, shot: Shot) -> np.ndarray:
        img = cv2.imread(str(self.root / shot.image), cv2.IMREAD_COLOR)
        if img is None:
            raise CaptureFormatError([f"shots[{shot.id}].image: cannot read {shot.image}"])
        return np.asarray(img)

    def load_depth(self, shot: Shot) -> tuple[np.ndarray, np.ndarray | None, Intrinsics]:
        """(depth float32 metres with NaN for no data, confidence uint8 or None, intrinsics)."""
        if shot.depth is None:
            raise CaptureFormatError([f"shots[{shot.id}]: no depth"])
        depth = _read_depth(self.root / shot.depth.file, shot.depth.scale_m)
        conf = None
        if shot.depth.confidence is not None:
            conf = _read_confidence(self.root / shot.depth.confidence)
            if conf.shape != depth.shape:
                raise CaptureFormatError(
                    [
                        f"shots[{shot.id}].depth.confidence: shape {conf.shape} "
                        f"!= depth {depth.shape}"
                    ]
                )
        h, w = depth.shape
        intr = shot.depth.intrinsics or shot.intrinsics.scaled(w, h)
        if (intr.width, intr.height) != (w, h):
            raise CaptureFormatError(
                [f"shots[{shot.id}].depth.intrinsics: {intr.width}x{intr.height} != file {w}x{h}"]
            )
        return depth, conf, intr

    def load_graycode(self) -> CaptureSet:
        """The Gray-code frames as the CaptureSet that ``calib.decode.decode`` reads."""
        g = self.graycode
        if g.kind != "frames" or g.pattern is None:
            raise CaptureFormatError(["graycode: not a frames capture"])
        anchor = self.anchor
        frames: list[PatternFrame] = []
        images: list[np.ndarray] = []
        white_bgr: np.ndarray | None = None
        problems: list[str] = []
        for ref in g.frames:
            raw = cv2.imread(str(self.root / ref.file), cv2.IMREAD_UNCHANGED)
            if raw is None:
                problems.append(f"graycode.frames: cannot read {ref.file}")
                continue
            arr = np.asarray(raw)
            scale = 255.0 / 65535.0 if arr.dtype == np.uint16 else 1.0
            if arr.ndim == 3:
                bgr = arr[:, :, :3]
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) * np.float32(scale)
            else:
                gray = arr.astype(np.float32) * np.float32(scale)
                bgr = cv2.cvtColor(np.clip(gray, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
            if gray.shape != (anchor.intrinsics.height, anchor.intrinsics.width):
                problems.append(
                    f"graycode.frames: {ref.file} is {gray.shape[1]}x{gray.shape[0]}, the "
                    f"Gray-code shot '{anchor.id}' is {anchor.intrinsics.width}x"
                    f"{anchor.intrinsics.height}"
                )
                continue
            if ref.frame.kind == "white":
                white_bgr = np.clip(bgr * scale, 0, 255).astype(np.uint8) if scale != 1 else bgr
            frames.append(ref.frame)
            images.append(gray)
        if problems:
            raise CaptureFormatError(problems)
        assert white_bgr is not None  # validated: the white frame is listed
        return CaptureSet(g.pattern, frames, np.stack(images), white_bgr, controls_applied={})


# ---------------------------------------------------------------- reading files


def _read_depth(path: Path, scale_m: float) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        arr = np.load(path, allow_pickle=False)
        if arr.ndim != 2 or arr.dtype not in (np.float32, np.float64, np.float16):
            raise CaptureFormatError([f"{path.name}: depth .npy must be a 2-D float array"])
        depth = arr.astype(np.float32)
    else:
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None or raw.ndim != 2 or raw.dtype != np.uint16:
            raise CaptureFormatError([f"{path.name}: depth .png must be single-channel 16-bit"])
        depth = raw.astype(np.float32) * np.float32(scale_m)
    depth[~np.isfinite(depth) | (depth <= 0)] = np.nan
    return np.asarray(depth, dtype=np.float32)


def _read_confidence(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        arr = np.load(path, allow_pickle=False)
    else:
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise CaptureFormatError([f"{path.name}: cannot read confidence"])
        arr = np.asarray(raw)
    if arr.ndim != 2 or not np.issubdtype(arr.dtype, np.integer) or int(arr.max(initial=0)) > 2:
        raise CaptureFormatError([f"{path.name}: confidence must be 2-D integers 0, 1 or 2"])
    return np.asarray(arr, dtype=np.uint8)


# ---------------------------------------------------------------- validation


class _Checker:
    def __init__(self, root: Path, check_files: bool) -> None:
        self.root = root
        self.check_files = check_files
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.problems.append(f"{where}: {msg}")

    def number(
        self, obj: Any, where: str, *, positive: bool = False, minimum: float | None = None
    ) -> float | None:
        if isinstance(obj, bool) or not isinstance(obj, (int, float)) or not math.isfinite(obj):
            self.err(where, f"must be a finite number, got {obj!r}")
            return None
        if positive and obj <= 0:
            self.err(where, f"must be > 0, got {obj}")
            return None
        if minimum is not None and obj < minimum:
            self.err(where, f"must be >= {minimum}, got {obj}")
            return None
        return float(obj)

    def integer(self, obj: Any, where: str) -> int | None:
        if isinstance(obj, bool) or not isinstance(obj, int) or obj <= 0:
            self.err(where, f"must be a positive integer, got {obj!r}")
            return None
        return obj

    def rel_file(self, obj: Any, where: str, suffixes: tuple[str, ...]) -> str | None:
        if not isinstance(obj, str) or not obj:
            self.err(where, "must be a relative file path")
            return None
        p = PurePosixPath(obj)
        if p.is_absolute() or ".." in p.parts or "\\" in obj:
            self.err(where, f"must be a relative path inside the capture folder, got {obj!r}")
            return None
        if p.suffix.lower() not in suffixes:
            self.err(where, f"{obj!r} must end in one of {', '.join(suffixes)}")
            return None
        if self.check_files and not (self.root / obj).is_file():
            self.err(where, f"file not found: {obj}")
            return None
        return obj

    def intrinsics(self, obj: Any, where: str) -> Intrinsics | None:
        if not isinstance(obj, dict):
            self.err(where, "must be an object {fx, fy, cx, cy, width, height}")
            return None
        missing = [k for k in ("fx", "fy", "cx", "cy", "width", "height") if k not in obj]
        if missing:
            self.err(where, f"missing {', '.join(missing)}")
            return None
        fx = self.number(obj["fx"], f"{where}.fx", positive=True)
        fy = self.number(obj["fy"], f"{where}.fy", positive=True)
        cx = self.number(obj["cx"], f"{where}.cx")
        cy = self.number(obj["cy"], f"{where}.cy")
        w = self.integer(obj["width"], f"{where}.width")
        h = self.integer(obj["height"], f"{where}.height")
        if None in (fx, fy, cx, cy, w, h):
            return None
        assert fx is not None and fy is not None and cx is not None and cy is not None
        assert w is not None and h is not None
        if not (-w <= cx <= 2 * w and -h <= cy <= 2 * h):
            self.err(where, f"principal point ({cx}, {cy}) is far outside the {w}x{h} image")
            return None
        return Intrinsics(fx, fy, cx, cy, w, h)

    def pose(self, obj: Any, where: str) -> np.ndarray | None:
        try:
            m = np.asarray(obj, dtype=np.float64)
        except (TypeError, ValueError):
            m = np.zeros(0)
        if m.shape == (16,):
            m = m.reshape(4, 4)
        if m.shape != (4, 4) or not np.isfinite(m).all():
            self.err(where, "must be a 4x4 matrix of finite numbers (row-major nested lists)")
            return None
        if not np.allclose(m[3], [0, 0, 0, 1], atol=1e-6):
            self.err(where, "last row must be [0, 0, 0, 1]")
            return None
        r = m[:3, :3]
        if np.abs(r.T @ r - np.eye(3)).max() > 1e-3 or np.linalg.det(r) <= 0:
            self.err(where, "top-left 3x3 must be a rotation (orthonormal, det +1); no scale")
            return None
        return m


def _pattern_frame(obj: dict[str, Any]) -> PatternFrame:
    kind = obj.get("kind")
    if kind in ("black", "white"):
        return PatternFrame(kind)
    return PatternFrame("pattern", obj.get("axis"), obj.get("bit"), bool(obj.get("inverse", False)))


def parse_manifest(obj: Any, root: Path, *, check_files: bool = True) -> Capture3d:
    """Validate a manifest (already parsed JSON) and return the capture; raise
    ``CaptureFormatError`` listing every problem."""
    c = _Checker(root, check_files)
    if not isinstance(obj, dict):
        raise CaptureFormatError(["manifest: must be a JSON object"])
    if obj.get("format") != FORMAT_NAME:
        c.err("format", f"must be {FORMAT_NAME!r}, got {obj.get('format')!r}")
    version = obj.get("version")
    if version != FORMAT_VERSION:
        if isinstance(version, int) and not isinstance(version, bool) and version > FORMAT_VERSION:
            c.err("version", f"{version} is newer than this FacadeKit reads ({FORMAT_VERSION})")
        else:
            c.err("version", f"must be {FORMAT_VERSION}, got {version!r}")
    if obj.get("units", "metres") not in ("metres", "meters"):
        c.err("units", f"must be 'metres', got {obj.get('units')!r}")
    convention = obj.get("pose_convention")
    if convention not in ("opencv", "arkit"):
        c.err("pose_convention", f"must be 'opencv' or 'arkit', got {convention!r}")
    known = {
        "format", "version", "units", "pose_convention", "created", "device", "shots",
        "graycode", "ground_truth", "notes",
    }  # fmt: skip
    for key in sorted(set(obj) - known):
        c.warnings.append(f"unknown top-level key {key!r} ignored")

    shots: list[Shot] = []
    raw_shots = obj.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        c.err("shots", "must be a non-empty list")
        raw_shots = []
    seen: set[str] = set()
    for i, s in enumerate(raw_shots):
        where = f"shots[{i}]"
        if not isinstance(s, dict):
            c.err(where, "must be an object")
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not _ID.match(sid):
            c.err(f"{where}.id", f"must match {_ID.pattern}, got {sid!r}")
            continue
        if sid in seen:
            c.err(f"{where}.id", f"duplicate id {sid!r}")
        seen.add(sid)
        where = f"shots[{sid}]"
        ts = c.number(s.get("timestamp_s"), f"{where}.timestamp_s", minimum=0.0)
        image = c.rel_file(s.get("image"), f"{where}.image", IMAGE_SUFFIXES)
        intr = c.intrinsics(s.get("intrinsics"), f"{where}.intrinsics")
        pose = c.pose(s.get("pose"), f"{where}.pose")
        depth: DepthRef | None = None
        d = s.get("depth")
        if d is not None:
            if not isinstance(d, dict):
                c.err(f"{where}.depth", "must be an object or absent")
            else:
                dfile = c.rel_file(d.get("file"), f"{where}.depth.file", DEPTH_SUFFIXES)
                conf = None
                if d.get("confidence") is not None:
                    conf = c.rel_file(
                        d.get("confidence"), f"{where}.depth.confidence", DEPTH_SUFFIXES
                    )
                dintr = None
                if d.get("intrinsics") is not None:
                    dintr = c.intrinsics(d.get("intrinsics"), f"{where}.depth.intrinsics")
                scale = c.number(d.get("scale_m", 0.001), f"{where}.depth.scale_m", positive=True)
                dts = None
                if d.get("timestamp_s") is not None:
                    dts = c.number(d.get("timestamp_s"), f"{where}.depth.timestamp_s", minimum=0.0)
                if dfile is not None and scale is not None:
                    depth = DepthRef(dfile, conf, dintr, scale, dts)
        if None in (ts, image, intr) or pose is None:
            continue
        assert ts is not None and image is not None and intr is not None
        if convention == "arkit":
            pose = pose_from_arkit(pose)
        shots.append(Shot(sid, ts, image, intr, pose, depth))
    if raw_shots and not any(s.depth is not None for s in shots) and not c.problems:
        c.err("shots", "no shot has depth; at least one depth map is needed")

    graycode = _parse_graycode(obj.get("graycode"), c, seen)
    if c.problems:
        raise CaptureFormatError(c.problems)
    assert graycode is not None

    gt = obj.get("ground_truth")
    if gt is not None and (not isinstance(gt, dict) or gt.get("source") != "simulator"):
        raise CaptureFormatError(
            ["ground_truth: only the simulator writes it (source 'simulator')"]
        )
    device = obj.get("device") if isinstance(obj.get("device"), dict) else {}
    created = obj.get("created") if isinstance(obj.get("created"), str) else None
    return Capture3d(root, shots, graycode, created, device or {}, gt, c.warnings)


def _parse_graycode(g: Any, c: _Checker, shot_ids: set[str]) -> GrayCodeRef | None:
    if not isinstance(g, dict):
        c.err("graycode", "must be an object")
        return None
    shot = g.get("shot")
    if shot not in shot_ids:
        c.err("graycode.shot", f"must name one of the shots, got {shot!r}")
    kind = g.get("kind")
    ts = None
    if g.get("timestamp_s") is not None:
        ts = c.number(g.get("timestamp_s"), "graycode.timestamp_s", minimum=0.0)
    if kind == "calibration":
        path = g.get("path")
        if (
            not isinstance(path, str)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
        ):
            c.err("graycode.path", "must be a relative folder inside the capture")
            return None
        if c.check_files and not (c.root / path / "p2c.npy").is_file():
            c.err("graycode.path", f"{path}/p2c.npy not found (a FacadeKit calibration folder)")
        return GrayCodeRef(str(shot), "calibration", path, None, [], ts)
    if kind != "frames":
        c.err("graycode.kind", f"must be 'frames' or 'calibration', got {kind!r}")
        return None
    pat = g.get("pattern")
    spec: PatternSpec | None = None
    if not isinstance(pat, dict):
        c.err("graycode.pattern", "must be {width, height, skip_lsb}")
    else:
        w = c.integer(pat.get("width"), "graycode.pattern.width")
        h = c.integer(pat.get("height"), "graycode.pattern.height")
        skip = pat.get("skip_lsb", 2)
        if w is not None and h is not None:
            try:
                spec = PatternSpec(w, h, int(skip))
                expected = pattern_sequence(spec)
            except (ValueError, TypeError) as exc:
                c.err("graycode.pattern.skip_lsb", str(exc))
                spec = None
    frames: list[GrayFrameRef] = []
    raw = g.get("frames")
    if not isinstance(raw, list) or not raw:
        c.err("graycode.frames", "must be a non-empty list")
        raw = []
    for i, f in enumerate(raw):
        where = f"graycode.frames[{i}]"
        if not isinstance(f, dict):
            c.err(where, "must be an object")
            continue
        file = c.rel_file(f.get("file"), f"{where}.file", IMAGE_SUFFIXES)
        try:
            frame = _pattern_frame(f)
        except (TypeError, ValueError):
            c.err(where, "bad frame description")
            continue
        if frame.kind == "pattern" and (
            frame.axis not in ("x", "y") or not isinstance(frame.bit, int)
        ):
            c.err(where, "a pattern frame needs axis 'x'|'y' and an integer bit")
            continue
        if file is not None:
            frames.append(GrayFrameRef(frame, file))
    if spec is not None and raw:
        have = {r.frame for r in frames}
        missing = [f for f in expected if f not in have]
        if missing:
            names = ", ".join(
                f.kind if f.kind != "pattern" else f"{f.axis}{f.bit}{'-inv' if f.inverse else ''}"
                for f in missing[:6]
            )
            c.err("graycode.frames", f"{len(missing)} frame(s) missing for the pattern: {names}")
        if len(have) != len(frames):
            c.err("graycode.frames", "a frame is listed twice")
    return GrayCodeRef(str(shot), "frames", "", spec, frames, ts)


def load_capture(root: Path | str, *, check_files: bool = True) -> Capture3d:
    root = Path(root)
    path = root / MANIFEST
    if not path.is_file():
        raise CaptureFormatError([f"{MANIFEST} not found in {root}"])
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureFormatError([f"{MANIFEST}: not valid JSON ({exc})"]) from exc
    return parse_manifest(obj, root, check_files=check_files)


# ---------------------------------------------------------------- writing


def _frame_name(f: PatternFrame) -> str:
    if f.kind != "pattern":
        return f.kind
    return f"{f.axis}{f.bit:02d}{'_inv' if f.inverse else ''}"


class CaptureWriter:
    """Writes a version-1 capture folder. Depth is stored as float32 .npy (metres), confidence
    as 8-bit PNG, images and Gray-code frames as PNG."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.shots: list[dict[str, Any]] = []
        self.graycode: dict[str, Any] | None = None

    def _write_png(self, rel: str, img: np.ndarray) -> str:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), img):
            raise FkError(BAD_REQUEST, f"cannot write {rel}")
        return rel

    def add_shot(
        self,
        shot_id: str,
        timestamp_s: float,
        image_bgr: np.ndarray,
        intrinsics: Intrinsics,
        pose: np.ndarray,
        *,
        depth: np.ndarray | None = None,
        confidence: np.ndarray | None = None,
        depth_intrinsics: Intrinsics | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "id": shot_id,
            "timestamp_s": float(timestamp_s),
            "image": self._write_png(f"shots/{shot_id}.png", image_bgr),
            "intrinsics": intrinsics.to_json(),
            "pose": np.asarray(pose, dtype=np.float64).tolist(),
        }
        if depth is not None:
            rel = f"shots/{shot_id}_depth.npy"
            d = np.asarray(depth, dtype=np.float32).copy()
            d[~np.isfinite(d)] = 0
            np.save(self.root / rel, d)
            dref: dict[str, Any] = {"file": rel}
            if confidence is not None:
                dref["confidence"] = self._write_png(
                    f"shots/{shot_id}_confidence.png", confidence.astype(np.uint8)
                )
            if depth_intrinsics is not None:
                dref["intrinsics"] = depth_intrinsics.to_json()
            entry["depth"] = dref
        self.shots.append(entry)

    def set_graycode_frames(
        self,
        shot_id: str,
        spec: PatternSpec,
        frames: list[PatternFrame],
        images: list[np.ndarray],
        *,
        timestamp_s: float | None = None,
    ) -> None:
        refs = []
        for f, img in zip(frames, images, strict=True):
            rel = self._write_png(f"graycode/{_frame_name(f)}.png", img)
            ref: dict[str, Any] = {"kind": f.kind, "file": rel}
            if f.kind == "pattern":
                ref.update({"axis": f.axis, "bit": f.bit, "inverse": f.inverse})
            refs.append(ref)
        self.graycode = {
            "shot": shot_id,
            "kind": "frames",
            "pattern": {"width": spec.width, "height": spec.height, "skip_lsb": spec.skip_lsb},
            "frames": refs,
        }
        if timestamp_s is not None:
            self.graycode["timestamp_s"] = float(timestamp_s)

    def set_graycode_calibration(self, shot_id: str, folder: str) -> None:
        self.graycode = {"shot": shot_id, "kind": "calibration", "path": folder}

    def manifest(
        self,
        *,
        created: str | None = None,
        device: dict[str, Any] | None = None,
        ground_truth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "units": "metres",
            "pose_convention": "opencv",
            "shots": self.shots,
            "graycode": self.graycode,
        }
        if created is not None:
            out["created"] = created
        if device:
            out["device"] = device
        if ground_truth is not None:
            out["ground_truth"] = ground_truth
        return out

    def write(self, **kwargs: Any) -> Path:
        path = self.root / MANIFEST
        path.write_text(json.dumps(self.manifest(**kwargs), indent=1), encoding="utf-8")
        return path
