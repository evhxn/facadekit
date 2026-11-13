from __future__ import annotations

from facadekit.calib.learn import STRATEGIES, ScanRecord, scale_class
from facadekit.sim.learn_eval import (
    MOVE_SCENES,
    Outcome,
    best_reward,
    far_scenes,
    make_scenes,
    move,
    replay,
)
from facadekit.sim.scene import true_px_per_proj_px


def _rec(strategy: str, ok: bool, scale: str | None = None) -> ScanRecord:
    return ScanRecord.model_validate(
        {
            "id": "scn_00000000", "at": "2026-11-11T00:00:00Z", "camera_id": "sim",
            "bucket": "high", "median_contrast": 120.0, "lit_fraction": 0.6,
            "strategy": strategy, "ok": ok, "coverage": 0.99 if ok else None, "scale": scale,
        }
    )  # fmt: skip


def _near() -> Outcome:
    """Close camera: every strategy works."""
    return {s.id: _rec(s.id, True, "fine") for s in STRATEGIES}


def _far() -> Outcome:
    """Far camera: the 4 px stripes (skip_lsb 2) are unreadable."""
    return {s.id: _rec(s.id, s.skip_lsb > 2, "coarse") for s in STRATEGIES}


def test_scenes_are_deterministic_and_split_held_out() -> None:
    a, b = make_scenes(12), make_scenes(12)
    assert [s.scene for s in a] == [s.scene for s in b]
    assert {s.held_out for s in a} == {True, False}
    assert len({s.scene.blur_sigma for s in a}) > 2  # the spread actually varies


def test_replay_settles_on_the_working_strategy() -> None:
    recs = {
        "standard": _rec("standard", False),
        "low_light": _rec("low_light", False),
        "soft_focus": _rec("soft_focus", True),
        "low_light_soft": _rec("low_light_soft", True),
        "very_soft": _rec("very_soft", True),
    }
    seq = replay(recs, 5)
    assert [s for s, _ in seq] == ["standard"] + ["soft_focus"] * 4
    assert seq[0][1] == 0.0 and best_reward(recs) > 0.9


def test_far_scenes_cover_both_scale_classes() -> None:
    scenes = far_scenes()
    assert [es.name for es in scenes] == ["near", "far_marginal", "far_too_coarse", "phone"]
    assert set(MOVE_SCENES) <= {es.name for es in scenes}
    assert len({es.seed for es in scenes}) == 4
    assert not {es.seed for es in scenes} & {es.seed for es in make_scenes(18)}
    scales = {es.name: scale_class(true_px_per_proj_px(es.scene)) for es in scenes}
    assert scales == {
        "near": "fine",
        "far_marginal": "coarse",
        "far_too_coarse": "coarse",
        "phone": "fine",
    }


def test_scale_context_changes_nothing_within_one_scene() -> None:
    # All scans of one scene share its scale class, so the factor never applies.
    for recs in (_near(), _far()):
        assert replay(recs, 8, use_scale=True) == replay(recs, 8)


def test_replay_without_scale_ignores_record_scale() -> None:
    # The published 0.490 / 1.143 numbers come from replays without scale context.
    prior = [_rec("standard", True, "fine")] * 8
    unscaled = {k: v.model_copy(update={"scale": None}) if v else None for k, v in _far().items()}
    prior_unscaled = [r.model_copy(update={"scale": None}) for r in prior]
    assert replay(_far(), 4, prior=prior) == replay(unscaled, 4, prior=prior_unscaled)


def test_moving_far_away_unlearns_faster_with_scale() -> None:
    # 8 good "standard" scans up close, then the camera is moved far away. Without scale the
    # old successes take 3 failures to outvote; with scale they count half, so 2 failures do.
    without = move(_near(), _far(), 8, 8, use_scale=False)
    with_scale = move(_near(), _far(), 8, 8, use_scale=True)
    assert [s for s, _ in without[:4]] == ["standard"] * 3 + ["soft_focus"]
    assert [s for s, _ in with_scale[:3]] == ["standard"] * 2 + ["soft_focus"]
    best = best_reward(_far())
    regret = {k: sum(best - r for _, r in v) for k, v in (("no", without), ("yes", with_scale))}
    assert regret["yes"] < regret["no"]
