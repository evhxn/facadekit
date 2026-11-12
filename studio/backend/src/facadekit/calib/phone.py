"""Phone as camera: play the light show on the laptop's projector window, then turn a video that
a phone recorded with its own camera app into a calibration (docs/08 section 4).

The video is untrusted input (it may come from an upload). Before ffmpeg sees it, the file must
pass ``check_video_file``: a size cap and a container magic-byte check, which also decides the
demuxer that is forced for every decode. Decoding only uses the ``file`` protocol, every decode
has a frame cap and a wall-clock timeout, and the picture size is bounded before any buffer is
allocated.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np

from facadekit.calib.capture import CaptureSet, no_projection_error
from facadekit.calib.learn import ScanAdvisor
from facadekit.calib.patterns import PatternSpec, pattern_sequence, render_pattern
from facadekit.calib.pipeline import run_calibration_from_capture
from facadekit.calib.quality import lit_blob, projection_found
from facadekit.calib.roi import suggest_roi
from facadekit.calib.selfclock import (
    HOLD_FRAMES,
    LEAD_IN,
    TAIL_BLACK_S,
    VIDEO_FPS,
    segment_recording,
    video_seconds,
)
from facadekit.devices.base import ProjectorDisplay
from facadekit.errors import (
    BAD_REQUEST,
    CANCELLED,
    PHONE_MOVED,
    PHONE_VIDEO_NO_PATTERN,
    PHONE_VIDEO_UNREADABLE,
    SCAN_FAILED,
    FkError,
)
from facadekit.media.ffmpeg import ffmpeg_exe
from facadekit.media.probe import probe
from facadekit.models.calibration import Calibration, CalibSettings, CameraRoi

PHONE_CAMERA_ID = "phone"
ANALYSIS_FPS = 30
MAX_SHIFT_PX = 2.0  # full-frame camera px between start and end of the sequence
BIN2_ABOVE_PX = 6_000_000  # crop area above which frames are averaged 2x2
MAX_VIDEO_S = 600

# Untrusted-input limits.
MAX_VIDEO_BYTES = 4 * 1024**3  # docs/08: uploads over 4 GB are refused
MIN_VIDEO_SIDE = 160  # px, displayed size
MAX_VIDEO_SIDE = 4096
MAX_VIDEO_PX = 4096 * 2304  # 4K plus a little; 8K phone video is refused
DECODE_TIMEOUT_S = 1200.0  # wall clock per ffmpeg decode pass
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".webm"})
_ISO_BMFF_BOXES = frozenset({b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"})
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_SESSION_ID = re.compile(r"^phs_[0-9a-f]{8}$")

_SMALL_WIDTH = 160
_MOVED_SUFFIX = " Did the phone move? Use a tripod or lean it on something steady."

MSG_UNREADABLE = (
    "FacadeKit cannot read this video. Record at 1080p or 4K in the 'Most Compatible' format "
    "and try again."
)
MSG_NO_PATTERN = (
    "The video does not contain the whole light show. Start recording before the projector "
    "flashes and stop after it goes dark."
)
MSG_MOVED = (
    "The phone moved during the light show. Use a tripod or lean it on something steady, "
    "then try again."
)

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]


def _unreadable(message: str = MSG_UNREADABLE) -> FkError:
    return FkError(PHONE_VIDEO_UNREADABLE, message)


# --- untrusted input checks ---------------------------------------------------------------


def check_upload_name(filename: str) -> None:
    """Refuse a client-supplied file name whose extension is not a video container.

    The name is only checked, never used to build a path: stored files get server names.
    """
    if Path(filename.replace("\\", "/")).suffix.lower() not in VIDEO_EXTENSIONS:
        raise FkError(BAD_REQUEST, "Choose a video file (.mp4 or .mov).")


def check_video_file(path: Path) -> str:
    """Check size and container magic bytes; return the ffmpeg demuxer to force."""
    try:
        if not path.is_file():
            raise _unreadable()
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(12)
    except OSError as exc:
        raise _unreadable() from exc
    if size > MAX_VIDEO_BYTES:
        raise FkError(BAD_REQUEST, "The video is larger than 4 GB.")
    if len(head) >= 8 and head[4:8] in _ISO_BMFF_BOXES:
        return "mov"  # MP4, MOV, M4V, 3GP
    if head.startswith(_EBML_MAGIC):
        return "matroska"  # MKV, WebM
    raise _unreadable()


def phone_upload_path(project_dir: Path, session_id: str) -> Path:
    """Server-generated location for an uploaded phone video; refuses any other session id."""
    if not _SESSION_ID.fullmatch(session_id):
        raise FkError(BAD_REQUEST, "Unknown light show.")
    root = (project_dir / "calibration" / ".phone").resolve()
    out = (root / f"{session_id}.upload").resolve()
    if out.parent != root:
        raise FkError(BAD_REQUEST, "Unknown light show.")
    return out


# --- light show ---------------------------------------------------------------------------


def light_show_seconds(spec: PatternSpec, countdown_s: float) -> float:
    return countdown_s + video_seconds(len(pattern_sequence(spec)))


def play_light_show(
    display: ProjectorDisplay,
    spec: PatternSpec,
    *,
    countdown_s: float = 15.0,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Show the self-clocked sequence; hold k appears at the absolute deadline t0 + k * hold."""
    w, h = display.size
    black = np.zeros((h, w), np.uint8)
    white = np.full((h, w), 255, np.uint8)
    holds = [white if label == "white" else black for label in LEAD_IN]
    holds += [render_pattern(spec, f) for f in pattern_sequence(spec)]
    holds.append(black)  # tail, held TAIL_BLACK_S
    hold_s = HOLD_FRAMES / VIDEO_FPS
    n = len(holds)
    total = countdown_s + (n - 1) * hold_s + TAIL_BLACK_S

    def report(t: float, message: str) -> None:
        if progress is not None:
            progress(min(1.0, max(0.0, t / total)), message)

    def check_cancel() -> None:
        if cancel is not None and cancel():
            display.show(black)
            raise FkError(CANCELLED, "Light show cancelled")

    display.show(black)
    t0 = clock() + countdown_s
    for left in range(math.ceil(countdown_s), 0, -1):
        check_cancel()
        sleep(max(0.0, t0 - left - clock()))
        report(countdown_s - left, f"Starting in {left} s")
    for k, img in enumerate(holds):
        check_cancel()
        sleep(max(0.0, t0 + k * hold_s - clock()))
        display.show(img)
        report(countdown_s + k * hold_s, f"Light show {k + 1} of {n}")
    sleep(max(0.0, t0 + (n - 1) * hold_s + TAIL_BLACK_S - clock()))


# --- video reading ------------------------------------------------------------------------


def _decode(
    path: Path,
    demuxer: str,
    vf: str,
    size: tuple[int, int],
    *,
    colour: bool = False,
    start_s: float | None = None,
    dur_s: float | None = None,
    max_frames: int,
    timeout_s: float = DECODE_TIMEOUT_S,
) -> Iterator[np.ndarray]:
    """Yield uint8 frames (gray (h, w) or BGR (h, w, 3)) of exactly `size` = (w, h)."""
    w, h = size
    shape = (h, w, 3) if colour else (h, w)
    frame_bytes = w * h * (3 if colour else 1)
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    cmd += ["-protocol_whitelist", "file", "-f", demuxer]
    if start_s is not None:
        cmd += ["-ss", f"{max(0.0, start_s):.3f}"]
    if dur_s is not None:
        cmd += ["-t", f"{max(dur_s, 1 / ANALYSIS_FPS):.3f}"]
    cmd += ["-i", f"file:{path.resolve()}", "-map", "0:v:0", "-an", "-sn", "-dn"]
    cmd += ["-vf", vf, "-frames:v", str(max_frames)]
    cmd += ["-pix_fmt", "bgr24" if colour else "gray", "-f", "rawvideo", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise _unreadable() from exc
    timed_out = threading.Event()

    def on_timeout() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout_s, on_timeout)
    timer.daemon = True
    timer.start()
    finished = False
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, np.uint8).reshape(shape)
        proc.wait()
        finished = True
    finally:
        timer.cancel()
        if not finished:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
    if timed_out.is_set():
        raise _unreadable("Reading the video took too long.")
    if proc.returncode != 0:
        raise _unreadable()


def _video_size(path: Path, n_patterns: int) -> tuple[int, int, float]:
    """Displayed (width, height) and duration in seconds, with the limits applied.

    Called only after ``check_video_file``, so ffmpeg's own probing sees a known container.
    """
    try:
        asset = probe(path.resolve())
    except FkError as exc:
        raise _unreadable() from exc
    if asset.kind != "video" or not asset.width or not asset.height or not asset.duration_ms:
        raise _unreadable()
    # probe() already swaps width and height for +-90 degree rotation (_ROTATION_RE).
    w, h = asset.width, asset.height
    if min(w, h) < MIN_VIDEO_SIDE or max(w, h) > MAX_VIDEO_SIDE or w * h > MAX_VIDEO_PX:
        raise _unreadable()
    seconds = asset.duration_ms / 1000
    if seconds > MAX_VIDEO_S:
        raise _unreadable("The video is longer than 10 minutes. Record only the light show.")
    if seconds < video_seconds(n_patterns):
        raise FkError(PHONE_VIDEO_NO_PATTERN, MSG_NO_PATTERN)
    return w, h, seconds


def _mean_of(frames: Iterator[np.ndarray], shape: tuple[int, ...]) -> np.ndarray:
    acc = np.zeros(shape, np.float64)
    count = 0
    for f in frames:
        acc += f
        count += 1
    if count == 0:
        raise FkError(PHONE_VIDEO_NO_PATTERN, MSG_NO_PATTERN)
    return (acc / count).astype(np.float32)


def _even(v: int) -> int:
    return v - v % 2


def analyse_phone_video(
    path: Path,
    spec: PatternSpec,
    *,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
) -> tuple[CaptureSet, CameraRoi]:
    frames = pattern_sequence(spec)
    n = len(frames)

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    def check_cancel() -> None:
        if cancel is not None and cancel():
            raise FkError(CANCELLED, "Calibration cancelled")

    demuxer = check_video_file(path)
    full_w, full_h, seconds = _video_size(path, n)
    max_frames = math.ceil(min(seconds + 2, MAX_VIDEO_S) * ANALYSIS_FPS)
    fps = f"fps={ANALYSIS_FPS}"
    full = f"scale={full_w}:{full_h}"  # no-op for a correct probe; pins the frame size

    # Pass 1: brightness trace (segment_recording only uses the per-frame mean).
    report(0.0, "Finding the light show in the video")
    small_h = max(2, _even(round(full_h * _SMALL_WIDTH / full_w)))
    means: list[float] = []
    for i, f in enumerate(
        _decode(
            path, demuxer, f"{fps},scale={_SMALL_WIDTH}:{small_h}:flags=area",
            (_SMALL_WIDTH, small_h), max_frames=max_frames,
        )
    ):  # fmt: skip
        means.append(float(f.mean()))
        if i % ANALYSIS_FPS == 0:
            check_cancel()
    if not means:
        raise _unreadable()
    times = np.arange(len(means), dtype=np.float64) / ANALYSIS_FPS
    trace = np.asarray(means, np.float32).reshape(-1, 1, 1)
    try:
        windows = segment_recording(times, trace, n, marker_search_s=None)
    except FkError as exc:
        raise FkError(PHONE_VIDEO_NO_PATTERN, MSG_NO_PATTERN) from exc

    # Pass 2a: BLACK and WHITE at full size -> lit blob -> crop.
    report(0.25, "Finding the lit area")
    black_k = next(k for k, f in enumerate(frames) if f.kind == "black")
    white_k = next(k for k, f in enumerate(frames) if f.kind == "white")
    means2: dict[int, np.ndarray] = {}
    for k in (black_k, white_k):
        first, last = windows[k]
        check_cancel()
        means2[k] = _mean_of(
            _decode(
                path, demuxer, f"{fps},{full}", (full_w, full_h),
                start_s=(first + 1) / ANALYSIS_FPS, dur_s=(last - first - 1) / ANALYSIS_FPS,
                max_frames=last - first + 1,
            ),
            (full_h, full_w),
        )  # fmt: skip
    contrast = means2[white_k] - means2[black_k]
    blob = lit_blob(contrast)
    if blob is None or not projection_found(blob):
        raise no_projection_error()
    roi = suggest_roi(blob, full_w, full_h, max_area_fraction=1.0)
    if roi is None:  # cannot happen with max_area_fraction=1.0
        raise no_projection_error()
    if roi.w * roi.h > BIN2_ABOVE_PX:
        roi = CameraRoi(
            x=roi.x, y=roi.y, w=_even(roi.w), h=_even(roi.h),
            frame_width=full_w, frame_height=full_h, bin=2,
        )  # fmt: skip
    cw, ch = roi.w // roi.bin, roi.h // roi.bin
    crop = f"{fps},{full},crop={roi.w}:{roi.h}:{roi.x}:{roi.y}"
    if roi.bin == 2:
        crop += f",scale={cw}:{ch}:flags=area"

    # Pass 2b: the whole video once, cropped, summed per window.
    report(0.30, "Reading the patterns")
    end = windows[-1][1] + 1
    window_of = np.full(end, -1, np.int64)
    for k, (first, last) in enumerate(windows):
        window_of[first : last + 1] = k
    sums = np.zeros((n, ch, cw), np.float32)
    counts = np.zeros(n, np.int64)
    for i, f in enumerate(_decode(path, demuxer, crop, (cw, ch), max_frames=end)):
        k = int(window_of[i])
        if k >= 0:
            sums[k] += f
            counts[k] += 1
        if i % ANALYSIS_FPS == 0:
            check_cancel()
            report(0.30 + 0.45 * i / end, "Reading the patterns")
    if (counts == 0).any():
        raise FkError(PHONE_VIDEO_NO_PATTERN, MSG_NO_PATTERN)
    images = sums / counts[:, None, None].astype(np.float32)
    del sums

    # White colour photo: the middle frame of the WHITE window.
    first, last = windows[white_k]
    mid = (first + last) // 2
    photo = list(
        _decode(
            path, demuxer, crop, (cw, ch), colour=True,
            start_s=mid / ANALYSIS_FPS, max_frames=1,
        )
    )  # fmt: skip
    if not photo:
        raise FkError(PHONE_VIDEO_NO_PATTERN, MSG_NO_PATTERN)
    white_bgr = photo[0].copy()

    # Motion check: WHITE - BLACK (start) against max(last pattern pair) - BLACK (end).
    a = (images[white_k] - images[black_k]).astype(np.float32)
    b = (np.maximum(images[-2], images[-1]) - images[black_k]).astype(np.float32)
    window = cv2.createHanningWindow((cw, ch), cv2.CV_32F)
    (dx, dy), _response = cv2.phaseCorrelate(a, b, window)
    if math.hypot(dx, dy) * roi.bin > MAX_SHIFT_PX:
        raise FkError(PHONE_MOVED, MSG_MOVED)
    report(0.78, "Patterns read")
    return CaptureSet(spec, frames, images, white_bgr, controls_applied={}), roi


def run_phone_calibration(
    project_dir: Path,
    video: Path,
    settings: CalibSettings,
    *,
    proj_size: tuple[int, int],
    strategy: str | None,
    advisor: ScanAdvisor | None,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
) -> Calibration:
    spec = PatternSpec(*proj_size, skip_lsb=settings.skip_lsb)
    capture, roi = analyse_phone_video(video, spec, progress=progress, cancel=cancel)
    settings = settings.model_copy(update={"roi": roi})
    try:
        return run_calibration_from_capture(
            project_dir, capture, PHONE_CAMERA_ID, settings,
            progress=progress, cancel=cancel, strategy=strategy, advisor=advisor,
        )  # fmt: skip
    except FkError as exc:
        if exc.code == SCAN_FAILED:
            raise FkError(exc.code, exc.message + _MOVED_SUFFIX) from exc
        raise
