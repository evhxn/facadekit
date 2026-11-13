from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.api.app import create_app
from facadekit.calib.patterns import PatternSpec, pattern_sequence
from facadekit.calib.pipeline import run_calibration_from_capture
from facadekit.calib.selfclock import (
    HOLD_FRAMES,
    LEAD_IN,
    VIDEO_FPS,
    record_and_capture,
    render_pattern_video,
    segment_recording,
)
from facadekit.errors import FkError
from facadekit.media.probe import probe
from facadekit.media.reader import VideoReader
from facadekit.models.calibration import CalibSettings
from facadekit.sim.scene import SimScene, default_scene

HOLD_S = HOLD_FRAMES / VIDEO_FPS


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _lagged(levels: np.ndarray, dt: np.ndarray, tau: float) -> np.ndarray:
    out = np.empty_like(levels)
    cur = levels[0]
    for i, (target, step) in enumerate(zip(levels, dt, strict=True)):
        cur += (target - cur) * (1 - np.exp(-step / tau))
        out[i] = cur
    return out


def _trace(delay: float, rng: np.random.Generator, n_patterns: int = 26) -> tuple[np.ndarray, ...]:
    """Frame times and a brightness-only recording of the pattern video, with lag and jitter."""
    video_levels = [0.0 if s == "black" else 200.0 for s in LEAD_IN]
    video_levels += [0.0, 200.0] + [100.0] * (n_patterns - 2) + [0.0, 0.0]
    total = delay + len(video_levels) * HOLD_S + 8
    times = np.cumsum(np.full(int(total * 20), 0.05) + rng.uniform(-0.01, 0.01, int(total * 20)))
    idx = np.floor((times - delay) / HOLD_S).astype(int)
    target = np.where(
        (idx >= 0) & (idx < len(video_levels)),
        np.array(video_levels + [0.0])[np.clip(idx, 0, len(video_levels))],
        0.0,
    )
    dt = np.diff(times, prepend=times[0] - 0.05)
    level = _lagged(target.astype(np.float64), dt, 0.15 / 2.2)  # ~150 ms to settle
    level += rng.normal(0, 0.5, len(level))
    frames = np.broadcast_to(level[:, None, None], (len(level), 4, 4)).astype(np.float32)
    return times, frames


def test_render_pattern_video_duration(tmp_path: Path) -> None:
    out = tmp_path / "scan.mp4"
    labels = render_pattern_video(PatternSpec(320, 180), out)
    n = len(pattern_sequence(PatternSpec(320, 180)))
    assert n == 2 + 2 * (7 + 6)  # the ticket's "26" is a slip: 2 + 2*13 = 28
    assert labels[: len(LEAD_IN)] == LEAD_IN
    assert labels[len(LEAD_IN) : len(LEAD_IN) + n] == [f"p{k}" for k in range(n)]
    assert labels[-1] == "black"
    asset = probe(out)
    assert asset.duration_ms is not None
    assert abs(asset.duration_ms / 1000 - (len(LEAD_IN) + n + 2)) <= 0.2


def test_segment_recording_synthetic() -> None:
    rng = np.random.default_rng(1)
    delay = 3.7
    times, frames = _trace(delay, rng)
    windows = segment_recording(times, frames, 26)
    assert len(windows) == 26
    for k, (first, last) in enumerate(windows):
        start = delay + (len(LEAD_IN) + k) * HOLD_S
        assert first <= last
        assert times[first] > start, k
        assert times[last] < start + HOLD_S, k


def test_segment_recording_whole_trace_thresholds() -> None:
    """A phone recording may start long before the marker: 25 s of black lead-in."""
    rng = np.random.default_rng(3)
    delay = 25.0 + 3.7
    times, frames = _trace(delay, rng)
    with pytest.raises(FkError):  # the default only looks at the first 15 s
        segment_recording(times, frames, 26)
    windows = segment_recording(times, frames, 26, marker_search_s=None)
    assert len(windows) == 26
    for k, (first, last) in enumerate(windows):
        start = delay + (len(LEAD_IN) + k) * HOLD_S
        assert first <= last
        assert times[first] > start, k
        assert times[last] < start + HOLD_S, k


def test_segment_recording_without_marker() -> None:
    rng = np.random.default_rng(2)
    times = np.cumsum(np.full(600, 0.05))
    frames = np.full((600, 4, 4), 20.0, np.float32) + rng.normal(0, 0.5, (600, 4, 4)).astype(
        np.float32
    )
    with pytest.raises(FkError) as e:
        segment_recording(times, frames, 26)
    assert e.value.code == "SCAN_FAILED"


class SimPlaybackCamera:
    """Simulated camera that sees the pattern video as FPP would play it.

    The video is decoded once; the camera image of each held video segment is pre-rendered from
    the decoded frame (so H.264 is in the loop). Reading advances a fake clock by 1/20 s plus
    +-10 ms jitter, and the picture follows the on-screen segment through a short exposure lag.
    """

    def __init__(
        self, scene: SimScene, video: Path, labels: list[str], clock: FakeClock, delay_s: float
    ) -> None:
        self._clock = clock
        self._start = clock.now + delay_s
        self._rng = np.random.default_rng(7)
        self._scene = scene
        w, h = scene.proj_size
        self._segments: list[np.ndarray] = []
        reader = VideoReader(video, fps=VIDEO_FPS, width=w, height=h)
        try:
            for _ in labels:
                frames = [reader.read() for _ in range(HOLD_FRAMES)]
                mid = frames[HOLD_FRAMES // 2]
                assert mid is not None
                self._segments.append(
                    scene.render_camera(mid, np.random.default_rng(scene.seed)).astype(np.float32)
                )
        finally:
            reader.close()
        self._dark = scene.render_camera(None, np.random.default_rng(scene.seed)).astype(np.float32)
        self._last = clock.now
        self._shown = self._dark.copy()
        self.started = False
        self.controls: dict[str, bool] = {}

    @property
    def resolution(self) -> tuple[int, int]:
        return self._scene.cam_size

    def open(self) -> None: ...

    def close(self) -> None: ...

    def set_controls(self, **kwargs: bool | float | None) -> dict[str, bool]:
        self.controls = {k: True for k, v in kwargs.items() if v is not None}
        return self.controls

    def read(self) -> np.ndarray:
        self._clock.now += 0.05 + self._rng.uniform(-0.01, 0.01)
        video_t = self._clock.now - self._start
        seg = int(video_t // HOLD_S)
        target = self._segments[seg] if 0 <= seg < len(self._segments) else self._dark
        alpha = 1 - np.exp(-(self._clock.now - self._last) / (0.15 / 2.2))
        self._last = self._clock.now
        self._shown += (target - self._shown) * alpha
        return np.clip(self._shown, 0, 255).astype(np.uint8)


def test_simulator_run(tmp_path: Path) -> None:
    scene = default_scene()
    spec = PatternSpec(*scene.proj_size)
    video = tmp_path / "scan.mp4"
    labels = render_pattern_video(spec, video)
    clock = FakeClock()
    cam = SimPlaybackCamera(scene, video, labels, clock, delay_s=2.3)
    total = len(labels) * HOLD_S + 15
    capture = record_and_capture(cam, spec, lambda: None, total_seconds=total, clock=clock)
    assert set(cam.controls) == {"auto_exposure", "auto_wb", "autofocus"}
    cal = run_calibration_from_capture(tmp_path, capture, "sim", CalibSettings())
    assert cal.quality is not None
    print("coverage", cal.quality.coverage)
    assert cal.quality.coverage > 0.85
    p2c = np.load(tmp_path / "calibration" / cal.id / "p2c.npy")
    worst = 0.0
    for cx, cy in scene.feature_quad_proj("garage_door"):
        got = p2c[int(round(cy)), int(round(cx))]
        want = scene.h_p2c @ np.array([cx, cy, 1.0])
        want = want[:2] / want[2]
        worst = max(worst, float(np.abs(got - want).max()))
    print("corner error", worst)
    assert worst < 3.0


def test_route_requires_host(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as client:
        pid = client.post("/api/v1/projects", json={"name": "demo"}).json()["id"]
        resp = client.post(
            f"/api/v1/projects/{pid}/calibrations", json={"mode": "scan_pi", "camera_id": "sim"}
        )
        assert resp.status_code == 422
