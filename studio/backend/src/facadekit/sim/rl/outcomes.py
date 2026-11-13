"""Outcome tables: scan every scene with every arm, in parallel, cached on disk.

One line of JSON per (scene, arm, realisation). A policy is then evaluated by replaying it
against the table, which costs microseconds instead of a scan.

Run: ``python -m facadekit.sim.rl scan --split train --n 200 --cache <dir>``
"""

from __future__ import annotations

import gzip
import json
import multiprocessing as mp
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from facadekit.calib.learn import STRATEGIES, ScanAdvisor, ScanMemory, Strategy
from facadekit.calib.pipeline import run_calibration
from facadekit.errors import FkError
from facadekit.geom.homography import apply_h
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.rl.scenes import SceneSpec, build_scene, make_specs
from facadekit.sim.scene import SimScene, true_px_per_proj_px

ERR_GRID_STEP = 24  # projector px between the points where the map is checked

# The five shipped strategies, then a wider grid to ask whether they are the right five.
PRODUCT_ARMS: tuple[Strategy, ...] = STRATEGIES
GRID_ARMS: tuple[Strategy, ...] = tuple(
    Strategy(f"s{skip}a{avg}c{int(conf * 100):02d}", "", skip, avg, conf, (1.0, 1.0))
    for skip in (2, 3, 4)
    for avg in (1, 3, 6)
    for conf in (0.06, 0.10, 0.16)
)


def arm_key(arm: Strategy) -> str:
    return f"{arm.skip_lsb}:{arm.average_frames}:{arm.min_conf}"


@dataclass(frozen=True)
class Outcome:
    """What one scan gave. `pre` is None when the pre-check refused the scene."""

    ok: bool
    coverage: float | None
    error_code: str | None
    # Map accuracy against the simulator's ground truth, in projector pixels, over a grid of
    # points that lie inside the projector frame and were really measured. (Door corners are
    # not used: in many scenes they fall outside the frame, where the map is extrapolated.)
    map_err_median: float | None
    map_err_p95: float | None
    planarity: float | None
    pre: dict[str, float] | None
    seconds: float

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


class _Capture(ScanAdvisor):
    def __init__(self, memory: ScanMemory, arm: Strategy) -> None:
        super().__init__(memory)
        self.arm = arm
        self.pre: dict[str, float] | None = None

    def choose(self, camera_id: str, pre: dict[str, float]) -> Strategy:
        self.pre = {k: float(v) for k, v in pre.items()}
        return self.arm

    def _record(self, *_a: object, **_k: object) -> None:  # the table is the record
        return


def scan(spec: SceneSpec, arm: Strategy, realisation: int) -> Outcome:
    scene = build_scene(spec, realisation)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        advisor = _Capture(ScanMemory(Path(tmp) / "scans.jsonl"), arm)
        display = SimDisplay(scene)
        try:
            cal = run_calibration(
                Path(tmp), display, SimCamera(scene, display), "sim",
                CalibSettings(discard_frames=0), sleep=lambda _s: None, advisor=advisor,
            )  # fmt: skip
        except FkError as exc:
            return Outcome(
                False, None, exc.code, None, None, None, advisor.pre, time.perf_counter() - t0
            )
        folder = Path(tmp) / "calibration" / cal.id
        p2c = np.load(folder / "p2c.npy")
        mask = cv2.imread(str(folder / "measured_mask.png"), cv2.IMREAD_GRAYSCALE)
        assert mask is not None
        measured = np.asarray(mask) > 0
        median, p95 = map_error(scene, p2c, measured)
        assert cal.quality is not None
        return Outcome(
            True, cal.quality.coverage, None, median, p95, cal.quality.planarity,
            advisor.pre, time.perf_counter() - t0,
        )  # fmt: skip


def map_error(
    scene: SimScene, p2c: np.ndarray, measured: np.ndarray
) -> tuple[float | None, float | None]:
    """(median, 95th percentile) distance in projector px between the map and the truth."""
    h, w = p2c.shape[:2]
    ys, xs = np.mgrid[
        ERR_GRID_STEP // 2 : h : ERR_GRID_STEP, ERR_GRID_STEP // 2 : w : ERR_GRID_STEP
    ]
    ys, xs = ys.ravel(), xs.ravel()
    keep = measured[ys, xs]
    if not keep.any():
        return None, None
    ys, xs = ys[keep], xs[keep]
    centres = np.stack([xs + 0.5, ys + 0.5], axis=1).astype(np.float64)
    cam_err = np.linalg.norm(p2c[ys, xs].astype(np.float64) - apply_h(scene.h_p2c, centres), axis=1)
    err = cam_err / true_px_per_proj_px(scene)
    return float(np.median(err)), float(np.percentile(err, 95))


def _init_worker() -> None:
    cv2.setNumThreads(1)  # one scan per core; the caller sets the niceness of the whole job


def _job(task: tuple[SceneSpec, Strategy, int]) -> tuple[str, dict[str, Any]]:
    spec, arm, r = task
    key = f"{spec.id}|{arm_key(arm)}|{r}"
    try:
        return key, scan(spec, arm, r).to_json()
    except Exception as exc:  # a simulator crash is data too, never a silent hole
        crash = Outcome(False, None, f"CRASH:{type(exc).__name__}", None, None, None, None, 0.0)
        return key, crash.to_json()


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Read a JSON-lines outcome table; `<path>.gz` is used when `path` does not exist (the
    tables behind docs/09 are committed gzipped in studio/backend/rl_data)."""
    out: dict[str, dict[str, Any]] = {}
    if path.exists():
        text = path.read_text()
    elif path.with_name(path.name + ".gz").exists():
        text = gzip.decompress(path.with_name(path.name + ".gz").read_bytes()).decode()
    else:
        return out
    for line in text.splitlines():
        if line.strip():
            key, value = json.loads(line)
            out[key] = value
    return out


def default_workers() -> int:
    return max(1, (os.cpu_count() or 4) - 2)


def build_table(
    specs: list[SceneSpec],
    arms: tuple[Strategy, ...],
    realisations: int,
    cache: Path,
    workers: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Fill `cache` (JSON lines) for every (scene, arm, realisation) and return it."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    have = load_cache(cache)
    tasks = [
        (s, a, r)
        for s in specs
        for a in arms
        for r in range(realisations)
        if f"{s.id}|{arm_key(a)}|{r}" not in have
    ]
    if tasks:
        t0 = time.time()
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
        os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
        ctx = mp.get_context("spawn")
        n = min(workers or default_workers(), default_workers())
        with ctx.Pool(n, initializer=_init_worker) as pool, cache.open("a") as out:
            for done, (key, value) in enumerate(pool.imap_unordered(_job, tasks, chunksize=1), 1):
                have[key] = value
                out.write(json.dumps([key, value]) + "\n")
                out.flush()
                if done % 50 == 0 or done == len(tasks):
                    rate = done / (time.time() - t0)
                    eta = (len(tasks) - done) / max(rate, 1e-9) / 60
                    print(f"{done}/{len(tasks)} scans, {rate:.2f}/s, eta {eta:.1f} min", flush=True)
    return have


MAX_CAM_WIDTH = 1920


def specs_for(split: str, n: int, max_cam_width: int = MAX_CAM_WIDTH) -> list[SceneSpec]:
    """The first `n` scene specs of a split, without cameras wider than `max_cam_width`.

    The generator also draws 2560x1440 (phone-sized) cameras for one scene in ten. They cost
    six times as much to simulate and are left out of the default run; the remaining scenes
    are unchanged, so tables scanned with and without them agree scene for scene.
    """
    return [s for s in make_specs(split, n) if s.params["cam"][0] <= max_cam_width]
