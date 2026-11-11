"""Evaluation harness for scan memory (calib/learn.py), simulator only, no extra dependencies.

It builds synthetic scenes from code, scans each scene once with every strategy (the outcome
table), then replays the bandit against that table to measure regret: how much reward is lost
before the policy settles on a good strategy for a given camera.

Run: ``uv run python -m facadekit.sim.learn_eval [--n 12]``
Scenes with ``seed % 3 == 0`` are held out: never look at them while tuning.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from facadekit.calib.learn import (
    STRATEGIES,
    ScanAdvisor,
    ScanMemory,
    ScanRecord,
    Strategy,
    choose,
    reward,
)
from facadekit.calib.pipeline import run_calibration
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.e2e import _map_corner_error
from facadekit.sim.scene import SimScene

Outcome = dict[str, ScanRecord | None]  # strategy id -> record (None: scan aborted pre-check)


@dataclass(frozen=True)
class EvalScene:
    seed: int
    scene: SimScene

    @property
    def held_out(self) -> bool:
        return self.seed % 3 == 0


def make_scenes(n: int, base_seed: int = 1000) -> list[EvalScene]:
    """Deterministic spread over blur, noise, ambient light, gamma, brightness and door position."""
    out: list[EvalScene] = []
    for i in range(n):
        seed = base_seed + i
        rng = np.random.default_rng(seed)
        dx, dy = float(rng.uniform(-80, 80)), float(rng.uniform(-60, 60))
        door = (500 + dx, 480 + dy, 1100 + dx, 800 + dy)
        scene = SimScene(
            blur_sigma=float(rng.choice([0.5, 1.0, 1.5, 2.5, 3.5, 5.0])),
            noise_sigma=float(rng.choice([1.0, 2.0, 4.0, 8.0, 12.0])),
            ambient=float(rng.uniform(0.02, 0.3)),
            gain=float(rng.uniform(0.35, 1.0)),
            gamma=float(rng.uniform(1.8, 2.6)),
            seed=seed,
        )
        scene.features_world["garage_door"] = door
        out.append(EvalScene(seed, scene))
    return out


class _Fixed(ScanAdvisor):
    """Advisor that always uses one strategy, so the outcome table covers every arm."""

    def __init__(self, memory: ScanMemory, strategy: Strategy) -> None:
        super().__init__(memory)
        self._strategy = strategy

    def choose(self, camera_id: str, pre: dict[str, float]) -> Strategy:
        return self._strategy


def scan_once(es: EvalScene, strategy: Strategy) -> tuple[ScanRecord | None, float | None]:
    """(log record, door-corner error in px or None) for one scan of one scene."""
    scene = replace(es.scene)
    with tempfile.TemporaryDirectory() as tmp:
        advisor = _Fixed(ScanMemory(Path(tmp) / "scans.jsonl"), strategy)
        display = SimDisplay(scene)
        try:
            cal = run_calibration(
                Path(tmp), display, SimCamera(scene, display), "sim",
                CalibSettings(discard_frames=0), sleep=lambda _s: None, advisor=advisor,
            )  # fmt: skip
        except FkError:
            recs = advisor.memory.records()
            return (recs[-1] if recs else None), None
        rec = advisor.memory.records()[-1]
        truth = np.array(scene.feature_quad_proj("garage_door"), dtype=np.float64)
        p2c = np.load(Path(tmp) / "calibration" / cal.id / "p2c.npy")
        return rec, _map_corner_error(p2c, scene.h_p2c, truth)


def outcome_table(
    scenes: list[EvalScene], cache: Path | None = None
) -> list[tuple[Outcome, dict[str, float | None]]]:
    """Scan every scene with every strategy. `cache` (JSON) keeps outcomes between runs, keyed by
    seed and the strategy's settings, so policy changes can be re-scored without rescanning."""
    store: dict[str, dict[str, object]] = {}
    if cache is not None and cache.exists():
        store = json.loads(cache.read_text())
    table = []
    for es in scenes:
        recs: Outcome = {}
        errs: dict[str, float | None] = {}
        for s in STRATEGIES:
            key = f"{es.seed}:{s.skip_lsb}:{s.average_frames}:{s.min_conf}"
            if key not in store:
                rec, err = scan_once(es, s)
                store[key] = {"rec": rec.model_dump() if rec else None, "err": err}
                if cache is not None:
                    cache.write_text(json.dumps(store))
            hit = store[key]
            raw = hit["rec"]
            rec2 = ScanRecord.model_validate(raw) if raw else None
            if rec2 is not None:
                rec2 = rec2.model_copy(update={"strategy": s.id})
            recs[s.id] = rec2
            errs[s.id] = hit["err"]  # type: ignore[assignment]
        table.append((recs, errs))
    return table


def best_reward(recs: Outcome) -> float:
    return max((reward(r) for r in recs.values() if r is not None), default=0.0)


def replay(recs: Outcome, scans: int = 8) -> list[tuple[str, float]]:
    """Scan the same scene `scans` times with the real policy; returns (strategy, reward)."""
    ref = next((r for r in recs.values() if r is not None), None)
    if ref is None:
        return []
    history: list[ScanRecord] = []
    out: list[tuple[str, float]] = []
    for _ in range(scans):
        s = choose(history, ref.camera_id, ref.bucket)
        rec = recs.get(s.id)
        if rec is None:  # pre-check abort never reaches the policy
            break
        history.append(rec)
        out.append((s.id, reward(rec)))
    return out


def report(scenes: list[EvalScene], scans: int = 8, cache: Path | None = None) -> dict[str, float]:
    table = outcome_table(scenes, cache)
    summary: dict[str, float] = {}
    for label, held in (("tuning", False), ("held-out", True)):
        rows = [(es, t) for es, t in zip(scenes, table, strict=True) if es.held_out == held]
        if not rows:
            continue
        print(f"\n== {label} ({len(rows)} scenes) ==")
        print("scene  blur noise  amb  gain | " + " ".join(f"{s.id[:10]:>14}" for s in STRATEGIES))
        regret_total, first_ok, final_gap = 0.0, 0, 0.0
        for es, (recs, errs) in rows:
            cells = []
            for s in STRATEGIES:
                r, e = recs[s.id], errs[s.id]
                cells.append("FAIL" if r is None or not r.ok else f"{r.coverage:.2f}/{e:.1f}px")
            sc = es.scene
            print(
                f"{es.seed:5d} {sc.blur_sigma:4.1f} {sc.noise_sigma:4.0f} {sc.ambient:5.2f} "
                f"{sc.gain:4.2f} | " + " ".join(f"{c:>14}" for c in cells)
            )
            seq = replay(recs, scans)
            best = best_reward(recs)
            regret_total += sum(best - r for _, r in seq)
            first_ok += 1 if seq and seq[0][1] > 0 else 0
            final_gap += best - (seq[-1][1] if seq else 0.0)
        n = len(rows)
        print(
            f"mean regret/{scans} scans {regret_total / n:.3f}  first scan ok {first_ok}/{n}  "
            f"final-scan gap {final_gap / n:.3f}"
        )
        summary[f"{label}_regret"] = regret_total / n
        summary[f"{label}_first_ok"] = first_ok / n
        summary[f"{label}_final_gap"] = final_gap / n
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--scans", type=int, default=8)
    ap.add_argument("--cache", type=Path, default=None)
    args = ap.parse_args()
    report(make_scenes(args.n), args.scans, args.cache)


if __name__ == "__main__":
    main()
