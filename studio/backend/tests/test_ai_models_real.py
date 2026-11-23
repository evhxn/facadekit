"""The real Magic select models (spike S-6 phase 2). Opt-in: FK_TEST_MODELS=1, with the models
installed in FACADEKIT_DATA (through the app's Download button). Nothing here downloads."""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

from facadekit.ai import engines, models, segment
from facadekit.ai.adapters import ort_session
from facadekit.ai.segment import ModelSegmenter, get_segmenter
from facadekit.ai.segment_eval import (
    Sample,
    evaluate,
    gt_box,
    iou,
    polygon_mask,
    synthetic_sample,
)
from facadekit.sim.scene import default_scene
from tests import fake_onnx

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(os.environ.get("FK_TEST_MODELS") != "1", reason="set FK_TEST_MODELS=1"),
]

ENGINES = ["sam2_1_tiny", "efficientsam_ti", "mobilesam"]
FAKE_KINDS = {
    ("sam2_hf", "encoder"): "sam2_encoder",
    ("sam2_hf", "decoder"): "sam2_decoder",
    ("efficientsam", "encoder"): "efficientsam_encoder",
    ("efficientsam", "decoder"): "efficientsam_decoder",
    ("sam1", "encoder"): "sam1_encoder_hwc",
    ("sam1", "decoder"): "sam1_decoder",
}


@pytest.fixture(autouse=True)
def bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACADEKIT_MODEL_REGISTRY", raising=False)
    monkeypatch.setattr(segment, "_session_factory", None)


def _installed(engine_id: str) -> engines.EngineInfo:
    info = engines.get_engine(engine_id)
    assert info is not None
    assert info.installed, f"{engine_id} is not installed in {models.models_dir()}"
    return info


@pytest.mark.parametrize("engine_id", ENGINES)
def test_real_signatures_match_the_stub_models(engine_id: str) -> None:
    """The stubs that test every adapter without weights must mirror the real graphs."""
    info = _installed(engine_id)
    for f in info.files:
        kind = FAKE_KINDS.get((info.adapter, f.role))
        if kind is None:
            continue  # .onnx_data weights
        real = ort_session(models.model_path(f.name))
        fake_in, _ = fake_onnx.SIGNATURES[kind]()
        assert [(i.name, i.type) for i in real.get_inputs()] == [
            (i.name, i.type) for i in fake_in
        ], f.name
        for r, s in zip(real.get_inputs(), fake_in, strict=True):
            assert len(r.shape) == len(s.shape), f.name


def _proj_view() -> tuple[np.ndarray, np.ndarray]:
    scene = default_scene()
    w, h = scene.proj_size
    img = cv2.warpPerspective(scene.albedo(), np.linalg.inv(scene.h_p2w), (w, h))
    mask = np.zeros((h, w), np.uint8)
    quad = np.round(np.array(scene.feature_quad_proj("garage_door"))).astype(np.int32)
    cv2.fillPoly(mask, [quad], 1)
    return img, mask > 0


@pytest.mark.parametrize("engine_id", ENGINES)
def test_garage_door_click_and_box(engine_id: str) -> None:
    _installed(engine_id)
    img, gt = _proj_view()
    seg = get_segmenter(engine_id)
    assert isinstance(seg, ModelSegmenter)
    seg.set_image(img)
    ys, xs = np.nonzero(gt)
    click = seg.segment([(float(xs.mean()), float(ys.mean()), 1)])
    assert iou(polygon_mask(click.outer, img.shape), gt) > 0.8
    boxed = seg.segment([], gt_box(gt))
    assert iou(polygon_mask(boxed.outer, img.shape), gt) > 0.8


def _ring_set() -> list[Sample]:
    rng = np.random.default_rng(11)
    out = []
    for i in range(4):
        sample = synthetic_sample(rng, (1920, 1080))
        sample.name = f"ring_{i}"
        out.append(sample)
    return out


@pytest.mark.parametrize("engine_id", ENGINES)
def test_small_white_object_on_a_cluttered_shelf_with_a_box(engine_id: str) -> None:
    _installed(engine_id)
    report = evaluate(engine_id, _ring_set(), max_clicks=3)
    assert float(report.summary()["box_iou"]) > 0.8


def test_default_engine_needs_few_clicks_on_small_objects() -> None:
    _installed("sam2_1_tiny")
    assert engines.auto_engine() == "sam2_1_tiny"
    report = evaluate("sam2_1_tiny", _ring_set(), max_clicks=5)
    summary = report.summary()
    assert float(summary["noc_85"]) <= 2.0
    assert float(summary["reached_pct"]) == 100.0
