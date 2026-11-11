from __future__ import annotations

from facadekit.calib.learn import ScanRecord
from facadekit.sim.learn_eval import best_reward, make_scenes, replay


def _rec(strategy: str, ok: bool) -> ScanRecord:
    return ScanRecord.model_validate(
        {
            "id": "scn_00000000", "at": "2026-11-11T00:00:00Z", "camera_id": "sim",
            "bucket": "high", "median_contrast": 120.0, "lit_fraction": 0.6,
            "strategy": strategy, "ok": ok, "coverage": 0.99 if ok else None,
        }
    )  # fmt: skip


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
