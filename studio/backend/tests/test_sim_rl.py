from __future__ import annotations

import numpy as np

from facadekit.calib.learn import STRATEGIES, ScanRecord, choose, scale_class, scale_floor
from facadekit.sim.rl.env import (
    BUCKETS,
    Obs,
    Rec,
    Table,
    product_reward,
    run_episodes,
    season_episodes,
    single_episodes,
)
from facadekit.sim.rl.policies import (
    BetaPolicy,
    Fixed,
    Oracle,
    ScenePosterior,
    fitted_priors,
    site_state,
    train_q,
)
from facadekit.sim.rl.scenes import build_scene, make_specs


def _record(rec: Rec) -> ScanRecord:
    arm = STRATEGIES[rec.arm]
    # invert product_reward so calib.learn.reward(record) == rec.reward
    coverage = None
    if rec.ok:
        coverage = 0.5 + 0.45 * min(1.0, rec.reward + 0.01 * (arm.average_frames - 1))
    return ScanRecord(
        id="scn_0", at="t", camera_id=rec.cam, bucket=BUCKETS[rec.bucket],  # type: ignore[arg-type]
        median_contrast=50.0, lit_fraction=0.5, strategy=arm.id, ok=rec.ok,
        coverage=coverage, scale=scale_class(rec.s),
    )  # fmt: skip


def test_default_beta_policy_is_the_shipped_policy() -> None:
    """Every comparison in docs/09 is against this policy, so it must be the real one."""
    rng = np.random.default_rng(0)
    policy = BetaPolicy(STRATEGIES)
    with_floor = BetaPolicy(STRATEGIES, floor=True)
    for _ in range(400):
        history = []
        for _ in range(int(rng.integers(0, 14))):
            arm = int(rng.integers(len(STRATEGIES)))
            ok = bool(rng.random() < 0.6)
            cap = 1.0 - 0.01 * (STRATEGIES[arm].average_frames - 1)
            reward = float(rng.uniform(0, cap)) if ok else 0.0
            history.append(
                Rec(str(rng.choice(["a", "b"])), int(rng.integers(3)),
                    float(rng.choice([0.15, 0.3, 0.45, 0.8, 1.5])), arm, reward, ok)
            )  # fmt: skip
        obs = Obs(str(rng.choice(["a", "b"])), int(rng.integers(3)),
                  float(rng.choice([0.15, 0.3, 0.45, 0.8, 1.5])), 20.0, 50.0)  # fmt: skip
        want = choose(
            [_record(r) for r in history],
            obs.cam,
            BUCKETS[obs.bucket],
            scale_class(obs.s),  # type: ignore[arg-type]
        )
        got = policy.choose(history, obs, rng)
        assert STRATEGIES[got].id == want.id, (history, obs)
        # ... and the simulated floor is the product's floor
        floored = choose(
            [_record(r) for r in history], obs.cam, BUCKETS[obs.bucket], scale_class(obs.s),  # type: ignore[arg-type]
            floor=scale_floor(obs.s),
        )  # fmt: skip
        assert STRATEGIES[with_floor.choose(history, obs, rng)].id == floored.id, (history, obs)


def test_product_reward_matches_calib_learn() -> None:
    from facadekit.calib.learn import reward

    for arm in STRATEGIES:
        for cov in (0.4, 0.5, 0.7, 0.95, 1.0):
            rec = ScanRecord(
                id="s", at="t", camera_id="c", bucket="high", median_contrast=90, lit_fraction=0.5,
                strategy=arm.id, ok=True, coverage=cov,
            )  # fmt: skip
            assert product_reward(cov, True, arm.average_frames) == reward(rec)
    assert product_reward(None, False, 3) == 0.0


def _table(n: int = 12, seed: int = 0) -> Table:
    """Synthetic: scene i needs skip_lsb >= need[i]; coarser than needed loses accuracy."""
    rng = np.random.default_rng(seed)
    k = len(STRATEGIES)
    need = rng.integers(2, 5, n)
    reward = np.zeros((n, k, 2))
    ok = np.zeros((n, k, 2), bool)
    err = np.full((n, k, 2), np.nan)
    obs = []
    for i in range(n):
        for a, arm in enumerate(STRATEGIES):
            if arm.skip_lsb >= need[i]:
                ok[i, a] = True
                reward[i, a] = product_reward(1.0, True, arm.average_frames)
                err[i, a] = 0.5 + 3.0 * (arm.skip_lsb - need[i])
        s = {2: 0.9, 3: 0.25, 4: 0.12}[int(need[i])]  # finest_readable_skip(s) == need
        obs.append(Obs("cam", 2, s, 30.0, 90.0))
    usable = ok & (np.nan_to_num(err, nan=1e9) <= 2.0)
    return Table("train", STRATEGIES, [f"t-{i}" for i in range(n)], reward, ok, usable, err, obs, 0)


def test_env_regret_bounds_and_determinism() -> None:
    table = _table()
    eps = single_episodes(table, 8, 2, seed=1)
    assert len(eps) == table.n * 2
    oracle = run_episodes(table, Oracle(table), eps)
    assert np.allclose(oracle.regret, 0.0) and np.allclose(oracle.first_fail, 0.0)
    shipped = run_episodes(table, BetaPolicy(STRATEGIES), eps)
    again = run_episodes(table, BetaPolicy(STRATEGIES), eps)
    assert np.array_equal(shipped.regret, again.regret)
    worst = run_episodes(table, Fixed(0), eps)  # "standard" fails wherever need > 2
    assert shipped.regret.mean() < worst.regret.mean()
    assert (shipped.regret >= -1e-12).all() and (shipped.u_regret >= -1e-12).all()
    seasons = season_episodes(table, 3, 4, 20, seed=2)
    assert all(len(set(e.scenes)) == 3 and len(e.scenes) == 12 for e in seasons)


def test_floor_skips_strategies_the_picture_cannot_resolve() -> None:
    table = _table()
    eps = single_episodes(table, 8, 1, seed=1)
    plain = run_episodes(table, BetaPolicy(STRATEGIES), eps)
    floor = run_episodes(table, BetaPolicy(STRATEGIES, floor=True), eps)
    assert floor.first_fail.mean() == 0.0 < plain.first_fail.mean()
    assert floor.regret.mean() < plain.regret.mean()


def test_train_fitted_policies_run_and_scene_posterior_never_votes_for_itself() -> None:
    table = _table(30)
    eps = single_episodes(table, 6, 1, seed=3)
    post = ScenePosterior(table)
    res = run_episodes(table, post, eps)
    assert np.isfinite(res.regret).all()
    pri = fitted_priors(table, 5.0, by_floor=True)
    assert all(v.shape == (len(STRATEGIES), 2) and (v > 0).all() for v in pri.values())
    q = train_q(table, BetaPolicy(STRATEGIES), episodes=3000, seed=0)
    assert (
        run_episodes(table, q, eps).regret.mean()
        <= run_episodes(table, Fixed(0), eps).regret.mean()
    )
    state = site_state([Rec("cam", 2, 0.4, 0, 0.0, False)], Obs("cam", 2, 0.4, 30.0, 90.0), 5)
    assert state[2:] == (1, 0, 0, 0, 0)


def test_scene_specs_are_deterministic_and_splits_do_not_overlap() -> None:
    a, b = make_specs("train", 5), make_specs("train", 5)
    assert [s.params for s in a] == [s.params for s in b]
    seeds = {
        split: {s.params["seed"] for s in make_specs(split, 50)}
        for split in ("train", "heldout", "shifted")
    }
    assert not (seeds["train"] & seeds["heldout"]) and not (seeds["train"] & seeds["shifted"])
    scene = build_scene(make_specs("shifted", 3)[2])
    w, h = scene.cam_size
    assert all(
        -0.2 * w <= x <= 1.2 * w and -0.2 * h <= y <= 1.2 * h for x, y in scene.world_corners_cam
    )
    assert build_scene(a[0], 1).seed != build_scene(a[0], 0).seed


def test_committed_tables_load_and_the_probe_floor_is_attached() -> None:
    """studio/backend/rl_data holds the gzipped outcome tables behind docs/09."""
    from pathlib import Path

    from facadekit.sim.rl.evaluate import tables
    from facadekit.sim.rl.probes import blur_floor

    data = Path(__file__).resolve().parents[1] / "rl_data"
    tabs = tables(data)
    assert {k: t.n for k, t in tabs.items()} == {"train": 148, "heldout": 89, "shifted": 85}
    held = tabs["heldout"]
    assert held.reward.shape == (89, len(STRATEGIES), 2)
    assert all(o.cam in ("1280x720", "1920x1080") and 2 <= o.blur_floor <= 5 for o in held.obs)
    assert blur_floor({2: 0.1, 3: 0.29, 4: 0.5}) == 4 and blur_floor({2: 0.0, 3: 0.0, 4: 0.0}) == 5
    # the headline of docs/09, on a small deterministic suite
    eps = single_episodes(held, 8, 1, seed=1)
    shipped = run_episodes(held, BetaPolicy(STRATEGIES), eps)
    floor = run_episodes(held, BetaPolicy(STRATEGIES, floor=True), eps)
    probe = run_episodes(held, BetaPolicy(STRATEGIES, floor=True, probe=True), eps)
    oracle = run_episodes(held, Oracle(held), eps)
    assert probe.regret.mean() < 0.05 < floor.regret.mean() < 0.5 < shipped.regret.mean()
    assert probe.first_fail.mean() == oracle.first_fail.mean() < floor.first_fail.mean()
