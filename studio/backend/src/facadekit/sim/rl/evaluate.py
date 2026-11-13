"""Tune candidates on the train split only, then score everything once on the other splits.

Run: ``python -m facadekit.sim.rl evaluate --cache <dir> --out results.json``
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from facadekit.sim.rl.env import (
    Episode,
    Result,
    Table,
    load_table,
    run_episodes,
    season_episodes,
    single_episodes,
    with_cameras,
)
from facadekit.sim.rl.outcomes import PRODUCT_ARMS, default_workers, specs_for
from facadekit.sim.rl.policies import (
    BetaPolicy,
    FittedBeta,
    Fixed,
    Oracle,
    ScenePosterior,
    fitted_priors,
    floor_skip,
    train_q,
)
from facadekit.sim.rl.probes import blur_floor

SCANS = 8  # scans per single-site episode (as in sim/learn_eval.py)
SITES, SITE_SCANS = 3, 4
N_SCENES = {"train": 160, "heldout": 100, "shifted": 100}  # before the camera-size filter


def tables(cache: Path) -> dict[str, Table]:
    out = {}
    for split, n in N_SCENES.items():
        specs = specs_for(split, n)
        cams = {s.id: f"{s.params['cam'][0]}x{s.params['cam'][1]}" for s in specs}
        floors: dict[str, int] = {}
        probe_file = cache / f"probes-{split}.json"
        if probe_file.exists():
            raw = json.loads(probe_file.read_text())
            floors = {k: blur_floor({int(b): m for b, m in v.items()}) for k, v in raw.items()}
        table = load_table(cache, split, PRODUCT_ARMS, "product", only=set(cams))
        out[split] = with_cameras(table, cams, floors)
    return out


@dataclass
class Suite:
    single: list[Episode]
    season: list[Episode]


def suite(table: Table, seed: int, repeats: int, seasons: int) -> Suite:
    return Suite(
        single_episodes(table, SCANS, repeats, seed),
        season_episodes(table, SITES, SITE_SCANS, seasons, seed + 1),
    )


def per_scan_regret(table: Table, policy: Any, s: Suite) -> tuple[float, float]:
    """(product-reward regret, true-utility regret) per scan, mean of both episode shapes."""
    a = run_episodes(table, policy, s.single)
    b = run_episodes(table, policy, s.season)
    n = SITES * SITE_SCANS
    return (
        float((a.regret.mean() / SCANS + b.regret.mean() / n) / 2),
        float((a.u_regret.mean() / SCANS + b.u_regret.mean() / n) / 2),
    )


# --- tuning (train split only) ---------------------------------------------------------------

_G: dict[str, Any] = {}


def _init(cache: str) -> None:
    try:
        os.nice(10)
    except OSError:
        pass
    train = tables(Path(cache))["train"]
    _G["train"] = train
    _G["suite"] = suite(train, seed=11, repeats=3, seasons=1500)


def _score_beta(kwargs: dict[str, Any]) -> tuple[tuple[float, float], dict[str, Any]]:
    kw = dict(kwargs)
    mult = kw.pop("prior_mult")
    base = np.array([a.prior for a in PRODUCT_ARMS]) * mult
    pol = BetaPolicy(PRODUCT_ARMS, priors=base, **kw)
    return per_scan_regret(_G["train"], pol, _G["suite"]), kwargs


def _score_posterior(kwargs: dict[str, Any]) -> tuple[tuple[float, float], dict[str, Any]]:
    pol = ScenePosterior(_G["train"], **kwargs)  # leave-one-out is built into the policy
    return per_scan_regret(_G["train"], pol, _G["suite"]), kwargs


def _random_beta(rng: np.random.Generator) -> dict[str, Any]:
    select = str(rng.choice(["greedy", "greedy", "ucb", "thompson"]))
    return {
        "prior_mult": float(rng.choice([0.5, 1.0, 2.0])),
        "mistake_weight": float(rng.choice([1.0, 1.5, 2.0, 3.0, 4.0])),
        "other_bucket": float(rng.choice([0.0, 0.25, 0.5])),
        "different_scale": float(rng.choice([0.25, 0.5, 1.0])),
        "scale_sigma": float(rng.choice([0.0, 0.0, 0.15, 0.3, 0.6])),
        "sibling_share": float(rng.choice([0.0, 0.25, 0.5, 1.0])),
        "discount": float(rng.choice([1.0, 1.0, 0.95, 0.85, 0.7])),
        "select": select,
        "ucb_c": float(rng.choice([0.5, 1.0, 2.0])) if select == "ucb" else 0.0,
        "floor": bool(rng.choice([False, True])),
        "floor_slack": int(rng.choice([0, 1])),
        "skip_cost": float(rng.choice([0.0, 0.0, 0.02, 0.05, 0.1])),
    }


def tune(cache: Path, n_beta: int = 320, seed: int = 5) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    beta_grid = [_random_beta(rng) for _ in range(n_beta)]
    post_grid = [
        {"sigma_s": a, "sigma_c": b, "sigma_snr": b, "sigma_r": c}
        for a in (0.12, 0.25, 0.5)
        for b in (0.4, 0.8, 1.6)
        for c in (0.1, 0.2, 0.4)
    ]
    ctx = mp.get_context("spawn")
    with ctx.Pool(default_workers(), initializer=_init, initargs=(str(cache),)) as pool:
        beta = list(pool.imap_unordered(_score_beta, beta_grid, chunksize=4))
        post = list(pool.imap_unordered(_score_posterior, post_grid))
    # Two winners per family: tuned for the reward the product optimises, and tuned for the
    # true utility (which only a simulator can measure). Ties: the first found.
    by_r = sorted(beta, key=lambda x: x[0][0])
    by_u = sorted(beta, key=lambda x: x[0][1])
    post_r = sorted(post, key=lambda x: x[0][0])
    return {
        "beta": by_r[0][1], "beta_train_score": by_r[0][0], "beta_top5": by_r[:5],
        "beta_utility": by_u[0][1], "beta_utility_train_score": by_u[0][0],
        "beta_utility_top5": by_u[:5],
        "posterior": post_r[0][1], "posterior_train_score": post_r[0][0],
    }  # fmt: skip


# --- final scoring ----------------------------------------------------------------------------


def _ci(
    x: np.ndarray, groups: np.ndarray | None, rng: np.random.Generator, reps: int = 2000
) -> tuple[float, float]:
    """95 % bootstrap interval of the mean; resamples whole groups (scenes) when given."""
    if groups is None:
        idx = rng.integers(0, len(x), (reps, len(x)))
        means = x[idx].mean(axis=1)
    else:
        ids = np.unique(groups)
        sums = np.array([x[groups == g].sum() for g in ids])
        cnts = np.array([(groups == g).sum() for g in ids])
        pick = rng.integers(0, len(ids), (reps, len(ids)))
        means = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def _from_tuned(kwargs: dict[str, Any]) -> BetaPolicy:
    kw = dict(kwargs)
    mult = kw.pop("prior_mult")
    base = np.array([a.prior for a in PRODUCT_ARMS])
    return BetaPolicy(PRODUCT_ARMS, priors=base * mult, **kw)


def build_policies(tabs: dict[str, Table], tuned: dict[str, Any]) -> dict[str, Any]:
    train = tabs["train"]
    arms = PRODUCT_ARMS
    best_fixed = int(np.argmax(train.mean_reward.mean(axis=0)))
    kw = dict(tuned["beta"])
    mult = kw.pop("prior_mult")
    base = np.array([a.prior for a in arms])
    pol: dict[str, Any] = {
        "shipped": BetaPolicy(arms),
        "always-standard": Fixed(0),
        f"best-fixed ({arms[best_fixed].id})": Fixed(best_fixed),
        "shipped+thompson": BetaPolicy(arms, select="thompson"),
        "shipped+ucb(1)": BetaPolicy(arms, select="ucb", ucb_c=1.0),
        "shipped+discount(0.85)": BetaPolicy(arms, discount=0.85),
        "shipped+scale-kernel(0.3)": BetaPolicy(arms, scale_sigma=0.3),
        "shipped+floor": BetaPolicy(arms, floor=True),
        "shipped+floor(slack 1)": BetaPolicy(arms, floor=True, floor_slack=1),
        "shipped+floor+probe": BetaPolicy(arms, floor=True, probe=True),
        "shipped+floor+probe(slack 1)": BetaPolicy(arms, floor=True, probe=True, floor_slack=1),
        "shipped+skip-cost(0.05)": BetaPolicy(arms, skip_cost=0.05),
        "shipped+floor+skip-cost(0.05)": BetaPolicy(arms, floor=True, skip_cost=0.05),
        "tuned-beta (for reward)": BetaPolicy(arms, priors=base * mult, **kw),
        "tuned-beta (for utility)": _from_tuned(tuned["beta_utility"]),
        "scene-posterior": ScenePosterior(train, **tuned["posterior"]),
    }
    for by_floor in (False, True):
        for strength in (3.0, 8.0):
            tag = "bucket+scale" if by_floor else "bucket"
            pri = fitted_priors(train, strength, by_floor=by_floor)
            pol[f"fitted-priors[{tag}, n0={strength:g}]"] = FittedBeta(arms, pri, by_floor)
    pol["q-learning"] = train_q(train, BetaPolicy(arms))
    return pol


def score(
    tabs: dict[str, Table], policies: dict[str, Any], repeats: int, seasons: int
) -> dict[str, Any]:
    rng = np.random.default_rng(99)
    out: dict[str, Any] = {}
    for split, table in tabs.items():
        s = suite(table, seed=1000 + len(split), repeats=repeats, seasons=seasons)
        scene_of = np.array([ep.scenes[0] for ep in s.single])
        # Strata by what the picture scale allows: the answer differs a lot between them.
        need = np.array([min(max(floor_skip(table.obs[i].s), 2), 4) for i in scene_of])
        strata = {"fine (skip 2 readable)": need == 2, "needs skip 3": need == 3,
                  "needs skip 4+": need == 4}  # fmt: skip
        todo = dict(policies)
        todo["oracle"] = Oracle(table)
        res: dict[str, tuple[Result, Result]] = {
            name: (run_episodes(table, p, s.single), run_episodes(table, p, s.season))
            for name, p in todo.items()
        }
        base = res["shipped"]
        rows = {}
        for name, (a, b) in res.items():
            da, db = a.regret - base[0].regret, b.regret - base[1].regret
            ua, ub = a.u_regret - base[0].u_regret, b.u_regret - base[1].u_regret
            rows[name] = {
                "single_u_regret": float(a.u_regret.mean()),
                "single_u_diff": float(ua.mean()),
                "single_u_diff_ci": _ci(ua, scene_of, rng),
                "season_u_regret": float(b.u_regret.mean()),
                "season_u_diff": float(ub.mean()),
                "season_u_diff_ci": _ci(ub, None, rng),
                "strata": {
                    k: {
                        "episodes": int(m.sum()),
                        "regret": float(a.regret[m].mean()) if m.any() else None,
                        "first_fail": float(a.first_fail[m].mean()) if m.any() else None,
                        "diff": float(da[m].mean()) if m.any() else None,
                        "diff_ci": _ci(da[m], scene_of[m], rng) if m.any() else None,
                    }
                    for k, m in strata.items()
                },
                "single_regret": float(a.regret.mean()),
                "single_regret_ci": _ci(a.regret, scene_of, rng),
                "single_diff": float(da.mean()),
                "single_diff_ci": _ci(da, scene_of, rng),
                "single_first_fail": float(a.first_fail.mean()),
                "single_usable": float(a.usable.mean()),
                "single_last_ok": float(a.last_ok.mean()),
                "single_last_err": float(np.nanmedian(a.last_err)),
                "single_last_err_mean": float(np.nanmean(a.last_err)),
                "single_last_gap": float(a.last_gap.mean()),
                "season_regret": float(b.regret.mean()),
                "season_regret_ci": _ci(b.regret, None, rng),
                "season_diff": float(db.mean()),
                "season_diff_ci": _ci(db, None, rng),
                "season_first_fail": float(b.first_fail.mean()),
                "season_usable": float(b.usable.mean()),
            }
        out[split] = {"scenes": table.n, "skipped": table.skipped, "policies": rows}
    return out


def markdown(results: dict[str, Any]) -> str:
    def cell(r: dict[str, Any], key: str) -> str:
        lo, hi = r[f"{key}_diff_ci"]
        return f"{r[f'{key}_regret']:.3f} | {r[f'{key}_diff']:+.3f} [{lo:+.3f}, {hi:+.3f}]"

    lines: list[str] = []
    for split, block in results.items():
        lines.append(
            f"\n### {split} ({block['scenes']} scenes, "
            f"{block['skipped']} refused by the pre-check)\n"
        )
        lines.append(
            "| Policy | Reward regret, 8 scans at one site | vs shipped (95 % CI) | "
            "Accuracy-priced regret, same | vs shipped (95 % CI) | First scan fails | "
            "8th scan works | 8th scan map error (median / mean, px) | "
            "Reward regret, season of 12 | vs shipped (95 % CI) | "
            "Accuracy-priced regret, season | vs shipped (95 % CI) |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for name, r in block["policies"].items():
            lines.append(
                f"| {name} | {cell(r, 'single')} | {cell(r, 'single_u')} | "
                f"{100 * r['single_first_fail']:.0f} % | {100 * r['single_last_ok']:.0f} % | "
                f"{r['single_last_err']:.2f} / {r['single_last_err_mean']:.2f} | "
                f"{cell(r, 'season')} | {cell(r, 'season_u')} |"
            )
    for split, block in results.items():
        names = list(block["policies"])
        keys = list(block["policies"][names[0]]["strata"])
        counts = [block["policies"][names[0]]["strata"][k]["episodes"] for k in keys]
        lines.append(f"\n### {split}: one site, by picture scale (regret / first scan fails)\n")
        lines.append(
            "| Policy | "
            + " | ".join(f"{k} ({c} ep.)" for k, c in zip(keys, counts, strict=True))
            + " |"
        )
        lines.append("|---|" + "---|" * len(keys))
        for name in names:
            cells = []
            for k in keys:
                st = block["policies"][name]["strata"][k]
                if st["regret"] is None:
                    cells.append("-")
                else:
                    lo, hi = st["diff_ci"]
                    cells.append(
                        f"{st['regret']:.3f} ({st['diff']:+.3f} [{lo:+.3f}, {hi:+.3f}]) / "
                        f"{100 * st['first_fail']:.0f} %"
                    )
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run(cache: Path, out: Path | None, seasons: int) -> None:
    tabs = tables(cache)
    for split, t in tabs.items():
        print(f"{split}: {t.n} scenes ({t.skipped} refused by the pre-check)", flush=True)
    tuned_path = cache / "tuned.json"
    if tuned_path.exists():
        tuned = json.loads(tuned_path.read_text())
    else:
        tuned = tune(cache)
        tuned_path.write_text(json.dumps(tuned, indent=1))
    keys = ("beta", "beta_utility", "posterior")
    print("tuned on train:", json.dumps({k: tuned[k] for k in keys}), flush=True)
    results = score(tabs, build_policies(tabs, tuned), repeats=10, seasons=seasons)
    text = markdown(results)
    print(text)
    if out is not None:
        out.write_text(json.dumps({"tuned": tuned, "results": results}, indent=1))
        out.with_suffix(".md").write_text(text + "\n")
