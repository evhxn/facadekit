"""The Magic select evaluation harness (spike S-6): synthetic set, simulated clicks, report."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from facadekit.ai import segment, segment_eval
from facadekit.ai.models import ensure_model
from facadekit.ai.segment_eval import (
    evaluate,
    generate,
    interior_point,
    load_set,
    main,
    next_click,
)
from facadekit.errors import FkError
from tests.fake_onnx import fake_registry, fake_session, use_registry


def test_generated_set_has_small_objects_and_loads(tmp_path: Path) -> None:
    written = generate(tmp_path, count=3, seed=7)
    assert len(written) == 3
    samples = load_set(tmp_path)
    assert [s.name for s in samples] == ["shelf_000", "shelf_001", "shelf_002"]
    for s in samples:
        frac = s.mask.mean()
        assert 0.0003 < frac < 0.01  # small objects
        assert s.image.shape == (540, 960, 3)
    # the same seed gives the same pictures
    again = tmp_path / "again"
    generate(again, count=3, seed=7)
    assert np.array_equal(load_set(again)[1].image, samples[1].image)


def test_sim_features_are_added(tmp_path: Path) -> None:
    generate(tmp_path, count=0, sim=True)
    names = [s.name for s in load_set(tmp_path)]
    assert "sim_garage_door" in names


def test_simulated_user_clicks() -> None:
    gt = np.zeros((50, 50), bool)
    gt[10:30, 10:30] = True
    assert interior_point(gt) in {(19.0, 19.0), (20.0, 20.0), (19.0, 20.0), (20.0, 19.0)}
    pred = np.zeros_like(gt)
    pred[10:30, 10:18] = True  # missed the right part -> include click there
    x, y, label = next_click(pred, gt)
    assert label == 1 and x > 18
    pred = gt.copy()
    pred[35:48, 35:48] = True  # took something else -> exclude click on it
    x, y, label = next_click(pred, gt)
    assert label == 0 and x > 34 and y > 34


def test_grabcut_report(tmp_path: Path) -> None:
    generate(tmp_path, count=2, seed=1)
    report = evaluate("grabcut", load_set(tmp_path), max_clicks=2)
    summary = report.summary()
    assert summary["engine"] == "grabcut" and summary["samples"] == 2
    for key in ("iou_1", "iou_2", "box_iou", "box_click_iou"):
        assert 0.0 <= float(summary[key]) <= 1.0
    assert 1 <= float(summary["noc_85"]) <= 3
    assert all(len(s.ious) == 2 for s in report.samples)


def test_model_engine_through_the_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACADEKIT_DATA", str(tmp_path / "data"))
    real = importlib.util.find_spec

    def find_spec(name: str, package: str | None = None) -> Any:
        return object() if name == "onnxruntime" else real(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(segment, "_session_factory", fake_session)
    entries = fake_registry(tmp_path / "src", {"stub": "efficientsam"})
    monkeypatch.setenv("FACADEKIT_MODEL_REGISTRY", str(use_registry(tmp_path / "r.json", entries)))
    for name in entries:
        ensure_model(name)
    generate(tmp_path / "set", count=2, seed=4)
    out = tmp_path / "report.json"

    code = main(
        ["run", str(tmp_path / "set"), "--engines", "stub", "--clicks", "2", "--json", str(out)]
    )

    assert code == 0
    data = json.loads(out.read_text())
    (summary,) = data["summary"]
    assert summary["engine"] == "stub"
    assert set(summary) >= {"iou_1", "iou_2", "noc_85", "box_iou", "encode_ms"}
    assert "iou_3" not in summary
    assert summary["reached_pct"] > 0  # the flood-fill stub follows the picture
    assert len(data["samples"]["stub"]) == 2


def test_engine_that_is_not_installed_is_refused(tmp_path: Path) -> None:
    generate(tmp_path, count=1)
    with pytest.raises(FkError):
        evaluate("efficientsam_ti", load_set(tmp_path))
    assert main(["run", str(tmp_path), "--engines", "efficientsam_ti"]) == 2
    assert main(["run", str(tmp_path / "empty")]) == 2


def test_cli_generate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate", str(tmp_path), "--count", "1"]) == 0
    assert "Wrote 1 pictures" in capsys.readouterr().out
    assert segment_eval.load_set(tmp_path)


def test_cli_generate_projector_size(tmp_path: Path) -> None:
    assert main(["generate", str(tmp_path), "--count", "1", "--size", "1920x1080"]) == 0
    (sample,) = load_set(tmp_path)
    assert sample.image.shape == (1080, 1920, 3)
    assert sample.mask.mean() < 0.003  # a small object in a projector-size picture
