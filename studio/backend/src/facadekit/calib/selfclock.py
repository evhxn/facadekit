"""Pi-driven calibration: a pattern video with a built-in clock, and the recording analysis.

The projector shows a video played by FPP. Nothing is synchronised over the network: the video
starts with a marker (black, then three white flashes) from which ``segment_recording`` recovers
the video clock, and every pattern is cut out of a continuous webcam recording.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from facadekit.calib.capture import CaptureSet
from facadekit.calib.patterns import PatternSpec, pattern_sequence, render_pattern
from facadekit.devices.base import Camera
from facadekit.errors import FkError
from facadekit.render.encoder import Encoder
from facadekit.render.presets import get_preset, output_size

# FPP names and encoding choices: parameters, not literals (spikes S-1, S-2, S-8 not run).
SCAN_PRESET = "pi3-1080p30"
SCAN_MEDIA_TEMPLATE = "fk_{project_id}_scan.mp4"
SCAN_PLAYLIST = "FacadeKit - scan"
VIDEO_FPS = 30
HOLD_FRAMES = 30  # each pattern is held for 1 s at 30 fps
LEAD_IN = ["black"] * 2 + ["white", "black"] * 3  # 2 s black, three white/black flashes
TAIL_BLACK_S = 2
RECORD_MARGIN_S = 15.0  # record this long beyond the video length (FPP start delay)

_MARKER_SEARCH_S = 15.0  # threshold statistics come from the first 15 s
_SEG_MIN, _SEG_MAX = 0.6, 1.4  # allowed marker segment length (s)
_CENTRAL = 0.25  # skip this fraction at both ends of a hold window
_SMALL_WIDTH = 160
_COLOUR_INTERVAL_S = 0.4  # one colour frame this often (shorter than the central window)

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]

_LOST = "Could not follow the pattern video. Make sure nothing else is playing on FPP."


def scan_media_name(project_id: str) -> str:
    return SCAN_MEDIA_TEMPLATE.format(project_id=project_id)


def video_seconds(n_patterns: int) -> float:
    """Length of the pattern video in seconds."""
    return (len(LEAD_IN) + n_patterns) * HOLD_FRAMES / VIDEO_FPS + TAIL_BLACK_S


def render_pattern_video(spec: PatternSpec, out_path: Path) -> list[str]:
    """Write the self-clocked pattern video; return the label of every segment in order."""
    preset = get_preset(SCAN_PRESET)
    width, height = output_size(preset, spec.width, spec.height)
    patterns = pattern_sequence(spec)
    black = np.zeros((height, width, 3), np.uint8)
    white = np.full((height, width, 3), 255, np.uint8)

    def bgr(gray: np.ndarray) -> np.ndarray:
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        if img.shape[:2] != (height, width):
            img = cv2.resize(img, (width, height), interpolation=cv2.INTER_NEAREST)
        return np.ascontiguousarray(img)

    holds: list[tuple[str, np.ndarray, int]] = [
        (label, white if label == "white" else black, HOLD_FRAMES) for label in LEAD_IN
    ]
    holds += [(f"p{k}", bgr(render_pattern(spec, f)), HOLD_FRAMES) for k, f in enumerate(patterns)]
    holds.append(("black", black, TAIL_BLACK_S * VIDEO_FPS))

    duration_ms = round(sum(n for _, _, n in holds) * 1000 / VIDEO_FPS)
    enc = Encoder(
        out_path,
        width=width,
        height=height,
        fps=VIDEO_FPS,
        preset=preset,
        audio_path=None,
        duration_ms=duration_ms,
    )
    try:
        for _label, frame, count in holds:
            for _ in range(count):
                enc.write(frame)
        enc.close()
    except BaseException:
        enc.abort()
        raise
    return [label for label, _, _ in holds]


def _runs(t: np.ndarray, b: np.ndarray, thr: float) -> list[tuple[bool, float, float]]:
    """Runs of (high?, start_edge, end_edge); edges are interpolated threshold crossings.

    The first and last run have no outer edge and are dropped.
    """
    high = b > thr
    edges: list[tuple[bool, float]] = []  # (state after the edge, crossing time)
    for i in np.flatnonzero(high[1:] != high[:-1]) + 1:
        b0, b1 = float(b[i - 1]), float(b[i])
        frac = 0.5 if b1 == b0 else (thr - b0) / (b1 - b0)
        edges.append(
            (bool(high[i]), float(t[i - 1] + min(max(frac, 0.0), 1.0) * (t[i] - t[i - 1])))
        )
    return [(edges[i][0], edges[i][1], edges[i + 1][1]) for i in range(len(edges) - 1)]


def _find_marker(runs: list[tuple[bool, float, float]]) -> tuple[float, float, list[int]] | None:
    """Return (t_0, t_a, indices of HIGH runs) for the first HIGH LOW HIGH LOW HIGH train."""
    for i in range(len(runs) - 4):
        if not runs[i][0]:
            continue
        group = runs[i : i + 5]
        if all(
            h == (j % 2 == 0) and _SEG_MIN <= e - s <= _SEG_MAX for j, (h, s, e) in enumerate(group)
        ):
            return group[0][1], group[4][2], [i, i + 2, i + 4]
    return None


def _levels(t: np.ndarray, b: np.ndarray, runs: list[tuple[bool, float, float]], idx: int) -> float:
    s, e = runs[idx][1], runs[idx][2]
    inner = (t >= s + 0.25 * (e - s)) & (t <= e - 0.25 * (e - s))
    return float(np.median(b[inner])) if inner.any() else float(np.median(b))


def segment_recording(
    times: np.ndarray, frames_gray: np.ndarray, n_patterns: int
) -> list[tuple[int, int]]:
    """Return, per pattern, the (first, last) recorded-frame indices inside its hold period."""
    t = np.asarray(times, dtype=np.float64)
    if t.ndim != 1 or len(t) < 8 or len(frames_gray) != len(t):
        raise FkError("SCAN_FAILED", _LOST)
    b = frames_gray.reshape(len(t), -1).mean(axis=1).astype(np.float64)
    padded = np.concatenate([b[:1], b, b[-1:]])
    b = np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)

    early = b[t - t[0] <= _MARKER_SEARCH_S]
    lo, hi = float(np.percentile(early, 5)), float(np.percentile(early, 95))
    if hi - lo < 1.0:
        raise FkError("SCAN_FAILED", _LOST)
    thr = (lo + hi) / 2
    runs = _runs(t, b, thr)
    marker = _find_marker(runs)
    if marker is None:
        raise FkError("SCAN_FAILED", _LOST)
    t_0, t_a, high_idx = marker
    seg = (t_a - t_0) / 5

    def window(k: int) -> tuple[float, float]:
        start = t_a + (1 + k) * seg
        return start + _CENTRAL * seg, start + (1 - _CENTRAL) * seg

    if window(n_patterns - 1)[1] > t[-1]:
        raise FkError("SCAN_FAILED", _LOST)

    high = float(np.median([_levels(t, b, runs, i) for i in high_idx]))
    low = float(np.median([_levels(t, b, runs, i + 1) for i in high_idx[:2]]))
    out: list[tuple[int, int]] = []
    for k in range(n_patterns):
        w0, w1 = window(k)
        inside = np.flatnonzero((t >= w0) & (t <= w1))
        if len(inside) == 0:
            raise FkError("SCAN_FAILED", _LOST)
        out.append((int(inside[0]), int(inside[-1])))
    if n_patterns >= 2:
        mean0 = float(b[out[0][0] : out[0][1] + 1].mean())
        mean1 = float(b[out[1][0] : out[1][1] + 1].mean())
        if mean1 - mean0 < 0.5 * (high - low):
            raise FkError("SCAN_FAILED", _LOST)
    return out


@dataclass
class _Recording:
    times: list[float]
    small: list[np.ndarray]
    colour_times: list[float]


def record_and_capture(
    camera: Camera,
    spec: PatternSpec,
    start_playback: Callable[[], None],
    *,
    total_seconds: float,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> CaptureSet:
    """Record while the pattern video plays, then cut the recording into per-pattern images."""
    controls = camera.set_controls(auto_exposure=False, auto_wb=False, autofocus=False)
    cam_w, cam_h = camera.resolution
    frames = pattern_sequence(spec)
    rec = _Recording([], [], [])
    small_h = max(1, round(cam_h * _SMALL_WIDTH / cam_w))
    fd, gray_name = tempfile.mkstemp(suffix=".gray")
    cfd, colour_name = tempfile.mkstemp(suffix=".bgr")
    try:
        with os.fdopen(fd, "wb") as gray_file, os.fdopen(cfd, "wb") as colour_file:
            start_playback()
            t_begin = clock()
            next_colour = t_begin
            last_report = t_begin - 1.0
            while True:
                now = clock()
                if now - t_begin >= total_seconds:
                    break
                if cancel is not None and cancel():
                    raise FkError("CANCELLED", "Calibration cancelled")
                bgr = camera.read()
                t = clock()
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                gray_file.write(gray.tobytes())
                rec.times.append(t)
                rec.small.append(
                    cv2.resize(gray, (_SMALL_WIDTH, small_h), interpolation=cv2.INTER_AREA).astype(
                        np.float32
                    )
                )
                if t >= next_colour:
                    colour_file.write(np.ascontiguousarray(bgr).tobytes())
                    rec.colour_times.append(t)
                    next_colour = t + _COLOUR_INTERVAL_S
                if progress is not None and t - last_report >= 1.0:
                    last_report = t
                    progress(min(1.0, (t - t_begin) / total_seconds) * 0.9, "Recording")

        if progress is not None:
            progress(0.9, "Cutting the recording into patterns")
        times = np.asarray(rec.times, dtype=np.float64)
        windows = segment_recording(times, np.stack(rec.small), len(frames))
        grays = np.memmap(gray_name, dtype=np.uint8, mode="r", shape=(len(times), cam_h, cam_w))
        images = np.empty((len(frames), cam_h, cam_w), np.float32)
        for k, (first, last) in enumerate(windows):
            images[k] = grays[first : last + 1].mean(axis=0, dtype=np.float32)
        white_k = next(k for k, f in enumerate(frames) if f.kind == "white")
        w_mid = 0.5 * (times[windows[white_k][0]] + times[windows[white_k][1]])
        ctimes = np.asarray(rec.colour_times)
        j = int(np.argmin(np.abs(ctimes - w_mid)))
        size = cam_w * cam_h * 3
        with open(colour_name, "rb") as f:
            f.seek(j * size)
            white_bgr = np.frombuffer(f.read(size), np.uint8).reshape(cam_h, cam_w, 3).copy()
        del grays
    finally:
        for name in (gray_name, colour_name):
            try:
                os.unlink(name)
            except OSError:
                pass
    return CaptureSet(spec, frames, images, white_bgr, controls)
