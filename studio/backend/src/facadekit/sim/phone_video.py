"""Simulated phone recording of the light show (test tool; also used for spike S-10 comparisons).

The phone starts recording ``preroll_s`` before the light show (the projector is dark), sees
every hold ``lag_frames`` late, and stops ``tail_s`` after the show. Optionally the phone moves
by ``shift_px`` from ``shift_after_s`` on.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np

from facadekit.calib.patterns import PatternSpec, pattern_sequence, render_pattern
from facadekit.calib.selfclock import HOLD_FRAMES, LEAD_IN, TAIL_BLACK_S, VIDEO_FPS
from facadekit.errors import FFMPEG_FAILED, FkError
from facadekit.media.ffmpeg import ffmpeg_exe
from facadekit.sim.scene import SimScene

_NOISE_FIELDS = 4


def make_phone_video(
    scene: SimScene,
    spec: PatternSpec,
    out: Path,
    *,
    preroll_s: float = 6.0,
    tail_s: float = 3.0,
    fps: int = 30,
    shift_after_s: float | None = None,
    shift_px: tuple[int, int] = (0, 0),
    lag_frames: int = 2,
    seed: int = 0,
) -> None:
    pw, ph = spec.width, spec.height
    black = np.zeros((ph, pw), np.uint8)
    white = np.full((ph, pw), 255, np.uint8)
    hold_s = HOLD_FRAMES / VIDEO_FPS
    projected = [white if label == "white" else black for label in LEAD_IN]
    projected += [render_pattern(spec, f) for f in pattern_sequence(spec)]
    show_s = len(projected) * hold_s + TAIL_BLACK_S

    quiet = replace(scene, noise_sigma=0.0)
    rng = np.random.default_rng(seed)
    dark = quiet.render_camera(None, rng)
    renders = [quiet.render_camera(img, rng) for img in projected]
    cam_w, cam_h = scene.cam_size
    noise = [
        np.rint(rng.normal(0, scene.noise_sigma, (cam_h, cam_w, 3))).astype(np.int16)
        for _ in range(_NOISE_FIELDS)
    ]

    n_frames = round((preroll_s + show_s + tail_s) * fps)
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{cam_w}x{cam_h}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
        str(out),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for j in range(n_frames):
            t_seen = (j - lag_frames) / fps - preroll_s  # light-show time the camera shows
            k = int(np.floor(t_seen / hold_s)) if t_seen >= 0 else -1
            base = renders[k] if 0 <= k < len(renders) else dark
            frame = np.clip(base.astype(np.int16) + noise[j % _NOISE_FIELDS], 0, 255)
            frame = frame.astype(np.uint8)
            if shift_after_s is not None and j / fps >= shift_after_s:
                frame = np.roll(frame, (shift_px[1], shift_px[0]), axis=(0, 1))
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
        proc.stdin.close()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    stderr = proc.stderr.read() if proc.stderr is not None else b""
    if proc.wait() != 0:
        raise FkError(FFMPEG_FAILED, stderr.decode("utf-8", errors="replace")[-500:])
