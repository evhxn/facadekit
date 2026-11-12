"""Evaluation harness for scan memory (calib/learn.py), simulator only, no extra dependencies.

It builds synthetic scenes from code, scans each scene once with every strategy (the outcome
table), then replays the bandit against that table to measure regret: how much reward is lost
before the policy settles on a good strategy for a given camera.

Run: ``uv run python -m facadekit.sim.learn_eval [--n 12] [--no-far]``
Scenes with ``seed % 3 == 0`` are held out: never look at them while tuning.

A second group (``far_scenes``) covers picture scale: the default scene plus the far and phone
scenes from ``sim/scene.py``. Besides the per-scene replay it measures "moves": the same camera
scans one scene, is then moved to a scene of another distance, and the policy is replayed there
on the combined history, once without and once with the picture-scale context (T-205).
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
from facadekit.sim.scene import (
    SimScene,
    default_scene,
    scene_far_marginal,
    scene_far_too_coarse,
    scene_phone,
)

Outcome = dict[str, ScanRecord | None]  # strategy id -> record (None: scan aborted pre-check)


@dataclass(frozen=True)
class EvalScene:
    seed: int
    scene: SimScene
    name: str = ""  # set for the far/phone group; also keys the outcome cache

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


# Scenes used for the moves: one webcam (1280x720) at three distances. The phone scene has a
# different camera (2560x1440), so moving "to" it would be a camera change, not a move.
MOVE_SCENES = ("near", "far_marginal", "far_too_coarse")


def far_scenes() -> list[EvalScene]:
    """The picture-scale group: default ("near"), far_marginal, far_too_coarse and phone.

    These were not used to tune anything. Seeds 2000+ keep them apart from `make_scenes`."""
    makers = (
        ("near", default_scene),
        ("far_marginal", scene_far_marginal),
        ("far_too_coarse", scene_far_too_coarse),
        ("phone", scene_phone),
    )
    return [EvalScene(2000 + i, make(), name) for i, (name, make) in enumerate(makers)]


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
            key = f"{es.name or es.seed}:{s.skip_lsb}:{s.average_frames}:{s.min_conf}"
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


def replay(
    recs: Outcome,
    scans: int = 8,
    *,
    prior: list[ScanRecord] | None = None,
    use_scale: bool = False,
) -> list[tuple[str, float]]:
    """Scan the same scene `scans` times with the real policy; returns (strategy, reward).

    `prior` is history from earlier scans (another scene, same camera). With `use_scale` the
    policy gets this scene's picture-scale class as context, as the wizard does since T-205;
    without it `choose` behaves as before T-205."""
    ref = next((r for r in recs.values() if r is not None), None)
    if ref is None:
        return []
    scale = ref.scale if use_scale else None
    history: list[ScanRecord] = list(prior or [])
    out: list[tuple[str, float]] = []
    for _ in range(scans):
        s = choose(history, ref.camera_id, ref.bucket, scale)
        rec = recs.get(s.id)
        if rec is None:  # pre-check abort never reaches the policy
            break
        history.append(rec)
        out.append((s.id, reward(rec)))
    return out


def history_of(recs: Outcome, seq: list[tuple[str, float]]) -> list[ScanRecord]:
    """The scan log a replay leaves behind."""
    return [r for s, _ in seq if (r := recs[s]) is not None]


def move(
    before: Outcome, after: Outcome, history: int, scans: int, use_scale: bool
) -> list[tuple[str, float]]:
    """Scan `before` `history` times, move the camera, then scan `after` `scans` times."""
    prior = history_of(before, replay(before, history, use_scale=use_scale))
    return replay(after, scans, prior=prior, use_scale=use_scale)


def _regret(recs: Outcome, seq: list[tuple[str, float]]) -> float:
    best = best_reward(recs)
    return sum(best - r for _, r in seq)


def far_report(
    scenes: list[EvalScene], scans: int = 8, cache: Path | None = None
) -> dict[str, float]:
    """Per-scene replay and moves for the picture-scale group (see `far_scenes`)."""
    table = outcome_table(scenes, cache)
    by_name = {es.name: recs for es, (recs, _) in zip(scenes, table, strict=True)}
    summary: dict[str, float] = {}
    print(f"\n== far/phone ({len(scenes)} scenes, not used for tuning) ==")
    print("scene           scale | " + " ".join(f"{s.id[:10]:>14}" for s in STRATEGIES))
    regret = {False: 0.0, True: 0.0}
    first_ok, final_gap = 0, 0.0
    for es, (recs, errs) in zip(scenes, table, strict=True):
        cells = []
        for s in STRATEGIES:
            r, e = recs[s.id], errs[s.id]
            cells.append("FAIL" if r is None or not r.ok else f"{r.coverage:.2f}/{e:.1f}px")
        ref = next((r for r in recs.values() if r is not None), None)
        scale = (ref.scale if ref else None) or "-"
        print(f"{es.name:15s} {scale:>6s} | " + " ".join(f"{c:>14}" for c in cells))
        for use in (False, True):
            regret[use] += _regret(recs, replay(recs, scans, use_scale=use))
        seq = replay(recs, scans)
        first_ok += 1 if seq and seq[0][1] > 0 else 0
        final_gap += best_reward(recs) - (seq[-1][1] if seq else 0.0)
    n = len(scenes)
    print(
        f"mean regret/{scans} scans {regret[False] / n:.3f} (with scale {regret[True] / n:.3f})  "
        f"first scan ok {first_ok}/{n}  final-scan gap {final_gap / n:.3f}"
    )
    summary["far_regret"] = regret[False] / n
    summary["far_regret_scale"] = regret[True] / n
    summary["far_first_ok"] = first_ok / n
    summary["far_final_gap"] = final_gap / n

    pairs = [(a, b) for a in MOVE_SCENES for b in MOVE_SCENES if a != b and a in by_name]
    pairs = [(a, b) for a, b in pairs if b in by_name]
    if not pairs:
        return summary
    for hist in sorted({2, scans}):
        print(f"\n-- moves: {hist} scans before, {scans} after; regret after the move --")
        print(f"{'from':>15} -> {'to':15} scales        no scale  with scale  first pick")
        totals = {False: 0.0, True: 0.0}
        for a, b in pairs:
            ra, rb = by_name[a], by_name[b]
            sa = next((r.scale for r in ra.values() if r is not None), None)
            sb = next((r.scale for r in rb.values() if r is not None), None)
            seqs = {use: move(ra, rb, hist, scans, use) for use in (False, True)}
            regs = {use: _regret(rb, seqs[use]) for use in (False, True)}
            for use in (False, True):
                totals[use] += regs[use]
            picks = "/".join(seqs[use][0][0] if seqs[use] else "-" for use in (False, True))
            print(f"{a:>15} -> {b:15} {sa}->{sb:7} {regs[False]:8.3f}  {regs[True]:10.3f}  {picks}")
        m = len(pairs)
        print(f"mean regret after a move: {totals[False] / m:.3f} -> {totals[True] / m:.3f}")
        summary[f"move{hist}_regret"] = totals[False] / m
        summary[f"move{hist}_regret_scale"] = totals[True] / m
    return summary


def report(
    scenes: list[EvalScene],
    scans: int = 8,
    cache: Path | None = None,
    far: list[EvalScene] | None = None,
) -> dict[str, float]:
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
    if far:
        summary.update(far_report(far, scans, cache))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--scans", type=int, default=8)
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--no-far", action="store_true", help="skip the far/phone group")
    args = ap.parse_args()
    report(make_scenes(args.n), args.scans, args.cache, None if args.no_far else far_scenes())


if __name__ == "__main__":
    main()
