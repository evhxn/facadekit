from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from facadekit.calib.capture import run_capture
from facadekit.calib.decode import DecodeResult, decode
from facadekit.calib.learn import (
    STRATEGIES,
    ScanAdvisor,
    ScanMemory,
    ScanRecord,
    choose,
    contrast_bucket,
    learning_enabled,
    reward,
    summarize,
)
from facadekit.calib.maps import build_maps
from facadekit.calib.patterns import PatternSpec
from facadekit.calib.pipeline import run_calibration
from facadekit.errors import FkError
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene


def _rec(
    strategy: str, *, ok: bool = True, coverage: float | None = 0.97, **kw: object
) -> ScanRecord:
    base: dict[str, object] = {
        "id": "scn_00000000",
        "at": "2026-11-10T00:00:00Z",
        "camera_id": "cam",
        "bucket": "high",
        "median_contrast": 120.0,
        "lit_fraction": 0.6,
        "strategy": strategy,
        "ok": ok,
        "coverage": coverage if ok else None,
        "error_code": None if ok else "SCAN_FAILED",
    }
    base.update(kw)
    return ScanRecord.model_validate(base)


def test_buckets() -> None:
    assert [contrast_bucket(v) for v in (20, 35, 79.9, 80, 200)] == [
        "low",
        "medium",
        "medium",
        "high",
        "high",
    ]


def test_reward() -> None:
    assert reward(_rec("standard", ok=False)) == 0.0
    assert reward(_rec("standard", coverage=0.5)) == 0.0
    assert reward(_rec("standard", coverage=0.95)) == pytest.approx(0.98)  # 2 extra frames
    assert reward(_rec("low_light", coverage=0.95)) == pytest.approx(0.95)  # 5 extra frames
    assert reward(_rec("standard", coverage=0.95, verdict="bad")) == 0.0
    assert reward(_rec("standard", coverage=0.725, verdict="good")) == pytest.approx(0.58)


def test_empty_history_keeps_the_defaults() -> None:
    s = choose([], "cam", "high")
    assert s.id == "standard"
    assert s.apply(CalibSettings()) == CalibSettings()  # nothing changes until something fails


def test_one_failure_switches_and_each_further_failure_moves_on() -> None:
    history: list[ScanRecord] = []
    tried = []
    for _ in range(len(STRATEGIES)):
        s = choose(history, "cam", "low")
        tried.append(s.id)
        history.append(_rec(s.id, ok=False, bucket="low"))
    assert tried == [s.id for s in STRATEGIES]  # every strategy gets its turn
    assert choose(history, "cam", "low").id == "standard"  # all failed once: start over


def test_failure_rules_out_same_or_finer_blocks_unless_lighting_is_dim() -> None:
    """Blurred stripes stay blurred with more frames: skip the same-block siblings."""
    history: list[ScanRecord] = []
    tried = []
    for _ in range(3):
        s = choose(history, "cam", "high")
        tried.append(s.id)
        history.append(_rec(s.id, ok=False))
    assert tried == ["standard", "soft_focus", "very_soft"]
    # in dim light noise is the likelier cause, so low_light is tried right after standard
    assert choose([_rec("standard", ok=False, bucket="low")], "cam", "low").id == "low_light"


def test_coarsest_strategy_rescues_a_very_blurred_camera() -> None:
    history = [
        _rec("standard", ok=False),
        _rec("soft_focus", ok=False),
        _rec("very_soft", coverage=0.99),
    ]
    assert choose(history, "cam", "high").id == "very_soft"


def test_success_is_remembered() -> None:
    history = [_rec("standard", ok=False), _rec("soft_focus", coverage=0.97)]
    assert choose(history, "cam", "high").id == "soft_focus"
    history.append(_rec("soft_focus", coverage=0.97))
    assert choose(history, "cam", "high").id == "soft_focus"


def test_user_verdict_bad_counts_as_a_mistake() -> None:
    history = [_rec("standard", coverage=0.99, verdict="bad")]
    assert choose(history, "cam", "high").id == "low_light"
    history = [_rec("standard", coverage=0.99, verdict="good")]
    assert choose(history, "cam", "high").id == "standard"


def test_context_limits_what_a_mistake_teaches() -> None:
    history = [_rec("standard", ok=False)]
    assert choose(history, "cam", "high").id == "soft_focus"
    # another camera in the same lighting: half weight, not enough to abandon the default
    assert choose(history, "other", "high").id == "standard"
    # same camera in different lighting: quarter weight
    assert choose(history, "cam", "low").id == "standard"
    # another camera and other lighting: ignored
    assert choose(history * 10, "other", "low").id == "standard"
    # but repeated failures elsewhere do add up
    assert choose(history * 2, "other", "high").id == "soft_focus"


def test_memory_roundtrip_verdict_and_damage(tmp_path: Path) -> None:
    mem = ScanMemory(tmp_path / "learning" / "scans.jsonl")
    assert mem.records() == []
    mem.append(_rec("standard", calibration_id="cal_00000001"))
    mem.append(_rec("low_light", ok=False))
    with mem.path.open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    assert [r.strategy for r in mem.records()] == ["standard", "low_light"]
    assert mem.set_verdict("cal_00000001", "bad") is True
    assert mem.set_verdict("cal_ffffffff", "bad") is False
    assert mem.records()[0].verdict == "bad"
    assert len(mem.records()) == 2  # the damaged line is dropped on rewrite
    summary = {s.id: s for s in summarize(mem.records())}
    assert (summary["standard"].tries, summary["standard"].mistakes) == (1, 1)
    assert summary["soft_focus"].mean_reward is None
    mem.clear()
    assert mem.records() == []


def test_learning_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACADEKIT_LEARN", raising=False)
    assert learning_enabled()
    monkeypatch.setenv("FACADEKIT_LEARN", "0")
    assert not learning_enabled()


def _scan(tmp: Path, scene: SimScene, advisor: ScanAdvisor) -> object:
    display = SimDisplay(scene)
    return run_calibration(
        tmp,
        display,
        SimCamera(scene, display),
        "sim",
        CalibSettings(discard_frames=0),
        sleep=lambda _s: None,
        advisor=advisor,
    )


def test_easy_scene_uses_standard_and_logs_it(tmp_path: Path) -> None:
    advisor = ScanAdvisor(ScanMemory(tmp_path / "scans.jsonl"))
    cal = _scan(tmp_path, default_scene(), advisor)
    assert cal.strategy == "standard"  # type: ignore[attr-defined]
    (rec,) = advisor.memory.records()
    assert rec.ok and rec.calibration_id == cal.id  # type: ignore[attr-defined]
    assert rec.bucket == "high" and rec.coverage is not None and rec.coverage > 0.85


def test_out_of_focus_camera_is_learned(tmp_path: Path) -> None:
    """A blurred camera cannot resolve the fine stripes: one failure, then the wizard adapts."""
    scene = SimScene(blur_sigma=4.0)
    advisor = ScanAdvisor(ScanMemory(tmp_path / "scans.jsonl"))
    with pytest.raises(FkError) as exc:
        _scan(tmp_path, scene, advisor)
    assert exc.value.code == "SCAN_FAILED"
    assert advisor.memory.records()[-1].strategy == "standard"
    cal = _scan(tmp_path, scene, advisor)
    assert cal.strategy == "soft_focus"  # type: ignore[attr-defined]
    assert cal.settings.skip_lsb == 3  # type: ignore[attr-defined]
    assert cal.quality.coverage > 0.5  # type: ignore[attr-defined]
    assert [r.ok for r in advisor.memory.records()] == [False, True]
    # A brand new advisor reading the same log does not repeat the mistake.
    again = ScanAdvisor(ScanMemory(tmp_path / "scans.jsonl"))
    assert again.choose("sim", {"median_contrast": 82.0, "lit_fraction": 0.5}).id != "standard"


def test_cancelled_and_precheck_failures_are_not_logged(tmp_path: Path) -> None:
    advisor = ScanAdvisor(ScanMemory(tmp_path / "scans.jsonl"))
    with pytest.raises(FkError) as exc:
        _scan(tmp_path, SimScene(gain=0.15, noise_sigma=8.0), advisor)
    assert exc.value.code == "TOO_BRIGHT"  # fails before a strategy is chosen
    assert advisor.memory.records() == []


def test_coarse_stripes_fill_the_whole_map() -> None:
    """skip_lsb=3 used to leave three grid nodes in four empty (25 % coverage)."""
    scene = default_scene()
    display = SimDisplay(scene)
    cam = SimCamera(scene, display)
    cam.open()
    spec = PatternSpec(*scene.proj_size, skip_lsb=3)
    fast = CalibSettings(discard_frames=0, average_frames=1, skip_lsb=3)
    maps = build_maps(decode(run_capture(display, cam, spec, fast, sleep=lambda _s: None)), spec)
    assert maps.coverage > 0.9
    with pytest.raises(ValueError):
        build_maps(decode_stub(), spec, grid_step=8)  # one block per node is not allowed


def decode_stub() -> DecodeResult:
    z = np.zeros((4, 4), np.float32)
    return DecodeResult(np.zeros((4, 4, 2), np.float32), np.ones((4, 4), bool), z, 1.0, 1.0)
