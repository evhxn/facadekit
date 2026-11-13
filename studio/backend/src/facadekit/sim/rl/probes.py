"""Stripe-modulation probe per scene: what the wizard's camera check measures (docs/08).

Six extra shots before a scan show which stripe widths the camera can actually resolve, blur
included. The pre-check does not take them today; this table lets the simulation ask what a
policy could do if it did.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path

import cv2

from facadekit.calib.patterns import PatternSpec
from facadekit.calib.quality import PROBE_BITS, blur_floor, probe_stripes
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.rl.outcomes import default_workers
from facadekit.sim.rl.scenes import SceneSpec, build_scene

__all__ = ["blur_floor", "build_probes", "probe"]


def probe(spec: SceneSpec) -> dict[int, float]:
    scene = build_scene(spec)
    display = SimDisplay(scene)
    camera = SimCamera(scene, display)
    camera.open()
    try:
        pattern = PatternSpec(*scene.proj_size, skip_lsb=2)
        settings = CalibSettings(discard_frames=0)
        return probe_stripes(
            display, camera, pattern, settings, bits=PROBE_BITS, sleep=lambda _s: None
        )
    finally:
        camera.close()


def _job(spec: SceneSpec) -> tuple[str, dict[int, float]]:
    cv2.setNumThreads(1)
    return spec.id, probe(spec)


def build_probes(specs: list[SceneSpec], cache: Path) -> dict[str, dict[int, float]]:
    have: dict[str, dict[int, float]] = {}
    if cache.exists():
        have = {
            k: {int(b): m for b, m in v.items()} for k, v in json.loads(cache.read_text()).items()
        }
    todo = [s for s in specs if s.id not in have]
    if todo:
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        with mp.get_context("spawn").Pool(default_workers()) as pool:
            for sid, mod in pool.imap_unordered(_job, todo, chunksize=2):
                have[sid] = mod
        cache.write_text(json.dumps(have))
    return have
