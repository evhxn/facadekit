"""Gym-style environment over a cached outcome table.

An episode is a camera's scan log: at every step the agent sees what the wizard's pre-check
measures (lighting, picture scale, noise), picks a strategy, and gets the scan's outcome.

Two episode shapes:

- ``single``: one scene scanned ``scans`` times (retries at one site).
- ``season``: ``sites`` scenes in a row, ``scans`` each, same camera: the camera is moved or the
  lighting changes, and what was learned at one site may or may not transfer.

Outcomes are drawn at random from the cached noise realisations of the scene, so a scan near
the pass/fail edge can go either way, as it does on a real wall.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from facadekit.calib.learn import TIME_COST_PER_FRAME, Strategy, contrast_bucket
from facadekit.sim.rl.outcomes import arm_key, load_cache

BUCKETS = ("low", "medium", "high")
ACCURACY_PRICE = 0.03  # reward per skip_lsb step, see Table.mean_utility
USABLE_ERR_PX = 2.0  # 95 % of the measured map within this many projector px = "usable"


class Obs(NamedTuple):
    cam: str
    bucket: int  # index into BUCKETS
    s: float  # camera px per projector px, as the pre-check measures it
    snr: float
    contrast: float
    # Finest skip_lsb the stripe probe says the camera resolves (sim/rl/probes.py); 0 = no
    # probe was taken, which is what the wizard's pre-check does today.
    blur_floor: int = 0


class Rec(NamedTuple):
    cam: str
    bucket: int
    s: float
    arm: int
    reward: float
    ok: bool


def product_reward(coverage: float | None, ok: bool, average_frames: int) -> float:
    """calib.learn.reward without a user verdict."""
    if not ok or coverage is None:
        return 0.0
    quality = min(1.0, max(0.0, (coverage - 0.5) / 0.45))
    return min(1.0, max(0.0, quality - TIME_COST_PER_FRAME * (average_frames - 1)))


@dataclass
class Table:
    """Outcomes of `n` scannable scenes: arrays indexed [scene, arm, realisation]."""

    split: str
    arms: tuple[Strategy, ...]
    scene_ids: list[str]
    reward: np.ndarray
    ok: np.ndarray
    usable: np.ndarray  # ok and map_err_p95 <= USABLE_ERR_PX
    map_err: np.ndarray  # 95th-percentile map error in projector px; NaN where the scan failed
    obs: list[Obs]
    skipped: int  # scenes the pre-check refused (nothing to choose there)

    @property
    def n(self) -> int:
        return len(self.scene_ids)

    @property
    def mean_reward(self) -> np.ndarray:
        return np.asarray(self.reward.mean(axis=2))

    @property
    def best(self) -> np.ndarray:
        return np.asarray(self.mean_reward.max(axis=1))

    @property
    def mean_utility(self) -> np.ndarray:
        """Accuracy-priced reward: the product reward minus ACCURACY_PRICE per skip_lsb step
        above 2, for scans that worked.

        The product reward is coverage minus a time cost, so it cannot tell a fine map from a
        coarse one, and in the simulator a coarser strategy never has lower coverage. Finer
        stripes do give a more accurate map where they work (`map_err`). This measure says:
        between two strategies that both work, the finer one is worth a little more. The price
        is a judgement, not a measurement; the report also shows the map error itself.
        """
        skip = np.array([a.skip_lsb for a in self.arms], dtype=np.float64)
        priced = self.reward - ACCURACY_PRICE * (skip - 2.0)[None, :, None]
        return np.asarray(np.where(self.ok, np.maximum(priced, 0.0), 0.0).mean(axis=2))


def load_table(
    cache_dir: Path,
    split: str,
    arms: tuple[Strategy, ...],
    kind: str,
    only: set[str] | None = None,
) -> Table:
    """The table of every fully scanned scene (of the scene ids in `only`, when given)."""
    raw = load_cache(cache_dir / f"{split}-{kind}.jsonl")
    scenes = sorted({k.split("|")[0] for k in raw}, key=lambda s: int(s.split("-")[1]))
    if only is not None:
        scenes = [s for s in scenes if s in only]
    reals = 1 + max(int(k.split("|")[2]) for k in raw)
    ids: list[str] = []
    rows: list[list[list[dict[str, Any]]]] = []
    obs: list[Obs] = []
    skipped = 0
    for sid in scenes:
        maybe = [[raw.get(f"{sid}|{arm_key(a)}|{r}") for r in range(reals)] for a in arms]
        if any(c is None for row in maybe for c in row):
            continue  # table still being filled
        cells = [[c for c in row if c is not None] for row in maybe]
        pres = [c["pre"] for row in cells for c in row if c["pre"] is not None]
        if len(pres) < len(arms) * reals:
            skipped += 1  # pre-check abort in at least one realisation: not a choice problem
            continue
        pre = pres[0]
        obs.append(
            Obs(
                cam="",  # set by with_cameras
                bucket=BUCKETS.index(contrast_bucket(pre["median_contrast"])),
                s=float(pre.get("px_per_proj_px", 1.0)),
                snr=float(pre.get("snr", 0.0)),
                contrast=float(pre["median_contrast"]),
            )
        )
        ids.append(sid)
        rows.append(cells)
    n, k = len(ids), len(arms)
    reward = np.zeros((n, k, reals))
    ok = np.zeros((n, k, reals), bool)
    err = np.full((n, k, reals), np.nan)
    for i, cells in enumerate(rows):
        for a, row in enumerate(cells):
            for r, c in enumerate(row):
                ok[i, a, r] = bool(c["ok"])
                reward[i, a, r] = product_reward(
                    c["coverage"], bool(c["ok"]), arms[a].average_frames
                )
                if c["map_err_p95"] is not None:
                    err[i, a, r] = float(c["map_err_p95"])
    usable = ok & (np.nan_to_num(err, nan=1e9) <= USABLE_ERR_PX)
    return Table(split, arms, ids, reward, ok, usable, err, obs, skipped)


def with_cameras(
    table: Table, cams: dict[str, str], blur_floors: dict[str, int] | None = None
) -> Table:
    """Set each scene's camera id (resolution class) and, when given, its probe result."""
    floors = blur_floors or {}
    table.obs = [
        o._replace(cam=cams[sid], blur_floor=floors.get(sid, 0))
        for o, sid in zip(table.obs, table.scene_ids, strict=True)
    ]
    return table


@dataclass(frozen=True)
class Episode:
    scenes: tuple[int, ...]  # scene index per step
    draws: tuple[int, ...]  # realisation index per step


def single_episodes(table: Table, scans: int, repeats: int, seed: int) -> list[Episode]:
    rng = np.random.default_rng(seed)
    reals = table.reward.shape[2]
    return [
        Episode((i,) * scans, tuple(int(v) for v in rng.integers(0, reals, scans)))
        for i in range(table.n)
        for _ in range(repeats)
    ]


def season_episodes(table: Table, sites: int, scans: int, count: int, seed: int) -> list[Episode]:
    """`sites` different scenes seen by one camera, `scans` each."""
    rng = np.random.default_rng(seed)
    reals = table.reward.shape[2]
    by_cam: dict[str, list[int]] = {}
    for i, o in enumerate(table.obs):
        by_cam.setdefault(o.cam, []).append(i)
    pools = [v for v in by_cam.values() if len(v) >= sites]
    sizes = np.array([len(v) for v in pools], dtype=np.float64)
    out: list[Episode] = []
    for _ in range(count):
        pool = pools[int(rng.choice(len(pools), p=sizes / sizes.sum()))]
        chosen = rng.choice(pool, sites, replace=False)
        steps = tuple(int(c) for c in chosen for _ in range(scans))
        out.append(Episode(steps, tuple(int(v) for v in rng.integers(0, reals, len(steps)))))
    return out


@dataclass
class Result:
    regret: np.ndarray  # per episode, summed over its steps
    first_fail: np.ndarray  # per episode: did the first scan at a new site fail (mean over sites)
    usable: np.ndarray  # per episode: fraction of scans that gave a usable map
    last_gap: np.ndarray  # per episode: regret of the final scan at each site, mean over sites
    u_regret: np.ndarray  # per episode: regret in accuracy-priced reward (Table.mean_utility)
    last_err: np.ndarray  # per episode: map error (p95, projector px) of the final scan at each
    # site, mean over the sites where that scan worked; NaN when none worked
    last_ok: np.ndarray  # per episode: fraction of sites whose final scan worked


def run_episodes(table: Table, policy: Any, episodes: list[Episode], seed: int = 0) -> Result:
    """Play every episode with a fresh memory. `policy.choose(history, obs, rng) -> arm`."""
    n = len(episodes)
    regret, first_fail = np.zeros(n), np.zeros(n)
    usable, last_gap, u_regret = np.zeros(n), np.zeros(n), np.zeros(n)
    last_err, last_ok = np.full(n, np.nan), np.zeros(n)
    mean_r, best = table.mean_reward, table.best
    mean_u = table.mean_utility
    best_u = mean_u.max(axis=1)
    for e, ep in enumerate(episodes):
        rng = np.random.default_rng((seed, e))
        history: list[Rec] = []
        sites = 0
        fails = 0.0
        gaps = 0.0
        errs: list[float] = []
        steps = len(ep.scenes)
        for t, (i, r) in enumerate(zip(ep.scenes, ep.draws, strict=True)):
            obs = table.obs[i]
            a = int(policy.choose(history, obs, rng))
            rew = float(table.reward[i, a, r])
            history.append(Rec(obs.cam, obs.bucket, obs.s, a, rew, bool(table.ok[i, a, r])))
            regret[e] += best[i] - mean_r[i, a]
            u_regret[e] += best_u[i] - mean_u[i, a]
            usable[e] += float(table.usable[i, a, r])
            if t == 0 or ep.scenes[t - 1] != i:
                sites += 1
                fails += 0.0 if table.ok[i, a, r] else 1.0
            if t == steps - 1 or ep.scenes[t + 1] != i:
                gaps += best[i] - mean_r[i, a]
                if table.ok[i, a, r]:
                    errs.append(float(table.map_err[i, a, r]))
        first_fail[e] = fails / sites
        last_gap[e] = gaps / sites
        last_ok[e] = len(errs) / sites
        if errs:
            last_err[e] = float(np.mean(errs))
        usable[e] /= steps
    return Result(regret, first_fail, usable, last_gap, u_regret, last_err, last_ok)
