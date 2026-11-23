"""Model-agnostic smart select: registry engines, adapters (with stub ONNX sessions),
auto selection that never downloads, and the segment route end to end (spike S-6)."""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from facadekit.ai import adapters, engines, models, segment
from facadekit.ai.models import ensure_model, registry
from facadekit.ai.segment import GrabCutSegmenter, ModelSegmenter, get_segmenter
from facadekit.errors import AI_UNAVAILABLE, NOT_FOUND, FkError
from tests import fake_onnx
from tests.fake_onnx import fake_registry, fake_session, use_registry

_real_find_spec = importlib.util.find_spec


def _onnxruntime(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    def find_spec(name: str, package: str | None = None) -> Any:
        if name == "onnxruntime":
            return object() if present else None
        return _real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("FACADEKIT_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("FACADEKIT_MODEL_REGISTRY", raising=False)
    monkeypatch.setattr(segment, "_session_factory", fake_session)
    _onnxruntime(monkeypatch, True)
    fake_onnx.SESSIONS.clear()
    yield


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def stream(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no network request expected")

    monkeypatch.setattr(models.httpx, "stream", stream)


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, families: dict[str, str], install: bool
) -> dict[str, dict[str, Any]]:
    entries = fake_registry(tmp_path / "src", families)
    monkeypatch.setenv(
        "FACADEKIT_MODEL_REGISTRY", str(use_registry(tmp_path / "registry.json", entries))
    )
    if install:
        for name in entries:
            ensure_model(name)
    return entries


def _scene() -> tuple[np.ndarray, np.ndarray]:
    """A small white box on a cluttered shelf next to other light objects (the Ring case)."""
    rng = np.random.default_rng(3)
    img = np.full((540, 960, 3), (70, 90, 110), np.uint8)
    for _ in range(60):
        x, y = int(rng.integers(0, 900)), int(rng.integers(0, 500))
        colour = tuple(int(c) for c in rng.integers(30, 200, 3))
        cv2.rectangle(
            img, (x, y), (x + int(rng.integers(20, 90)), y + int(rng.integers(20, 60))), colour, -1
        )
    cv2.rectangle(img, (380, 300), (600, 330), (200, 200, 200), -1)  # shelf
    cv2.rectangle(img, (430, 250), (470, 300), (235, 235, 235), -1)  # a white neighbour
    gt = np.zeros(img.shape[:2], np.uint8)
    cv2.rectangle(gt, (472, 262), (500, 300), 1, -1)  # the small white camera
    img[gt > 0] = (250, 250, 250)
    return img, gt.astype(bool)


def _iou(a: list[tuple[float, float]], gt: np.ndarray) -> float:
    mask = np.zeros(gt.shape, np.uint8)
    cv2.fillPoly(mask, [np.round(np.array(a)).astype(np.int32)], 1)
    m = mask > 0
    return float((m & gt).sum() / (m | gt).sum())


# --- bundled registry --------------------------------------------------------------------


def test_bundled_registry_points_at_pinned_upstream_files() -> None:
    entries = registry()
    pinned = re.compile(r"^https://huggingface\.co/[\w.-]+/[\w.-]+/resolve/[0-9a-f]{40}/[\w./-]+$")
    for name, entry in entries.items():
        assert pinned.match(entry["url"]), name
        assert "evhxn" not in entry["url"]
        assert entry["sha256"].startswith("TO_BE_FILLED"), name  # filled in after S-6 phase 2
        assert re.fullmatch(r"[0-9a-f]{64}", entry["upstream_sha256"]), name
        assert entry["size_bytes"] > 0
        assert entry["licence"] == "Apache-2.0"
    found = {e.id: e for e in engines.list_engines(check_files=False)}
    assert list(found) == ["sam2_1_tiny", "efficientsam_ti", "mobilesam"]
    for info in found.values():
        assert info.adapter in adapters.ADAPTERS
        assert info.roles() == set(adapters.ADAPTERS[info.adapter].roles)
        assert not info.published
    assert found["efficientsam_ti"].download_bytes == 24799761 + 16565728


def test_bundled_registry_auto_is_grabcut_and_downloads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_network(monkeypatch)
    assert engines.auto_engine() == "grabcut"
    assert isinstance(get_segmenter("auto"), GrabCutSegmenter)


def test_old_registry_without_engine_fields_still_groups_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = _setup(tmp_path, monkeypatch, {"mobilesam": "sam1"}, install=False)
    for entry in entries.values():
        for key in ("engine", "role", "adapter", "label", "licence", "source", "dir"):
            entry.pop(key)
    use_registry(tmp_path / "registry.json", entries)
    (info,) = engines.list_engines()
    assert (info.id, info.adapter, info.roles()) == ("mobilesam", "sam1", {"encoder", "decoder"})


# --- auto selection ------------------------------------------------------------------------


def test_published_but_not_installed_is_never_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = _setup(tmp_path, monkeypatch, {"fast": "efficientsam"}, install=False)
    _forbid_network(monkeypatch)
    for entry in entries.values():
        Path(entry["url"].removeprefix("file://")).unlink()  # a copy would fail loudly

    assert engines.usable_engines() == ["fast"]
    assert engines.auto_engine() == "grabcut"
    assert isinstance(get_segmenter("auto"), GrabCutSegmenter)
    with pytest.raises(FkError) as info:
        get_segmenter("fast")
    assert info.value.code == AI_UNAVAILABLE
    assert "not installed" in info.value.message
    assert not any((models.models_dir() / "fast").glob("*"))


def test_auto_uses_the_first_installed_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, {"best": "sam2_hf", "next": "efficientsam"}, install=False)
    for name in ("next_encoder", "next_decoder"):
        ensure_model(name)
    assert engines.auto_engine() == "next"
    for name in ("best_encoder", "best_encoder_data", "best_decoder", "best_decoder_data"):
        ensure_model(name)
    assert engines.auto_engine() == "best"
    seg = get_segmenter("auto")
    assert isinstance(seg, ModelSegmenter) and seg.engine == "best"


def test_tampered_local_file_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, {"fast": "efficientsam"}, install=True)
    assert engines.auto_engine() == "fast"
    models.model_path("fast_decoder").write_bytes(b"something else")
    assert engines.auto_engine() == "grabcut"


def test_no_onnxruntime_means_grabcut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, {"fast": "efficientsam"}, install=True)
    _onnxruntime(monkeypatch, False)
    assert engines.usable_engines() == []
    assert engines.auto_engine() == "grabcut"


def test_unknown_engine_is_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, {"fast": "efficientsam"}, install=True)
    with pytest.raises(FkError) as info:
        get_segmenter("nope")
    assert info.value.code == NOT_FOUND


# --- adapters end to end with stub sessions ---------------------------------------------------


@pytest.mark.parametrize("family", ["sam1", "sam1_nchw", "efficientsam", "sam2_hf"])
def test_adapter_click_box_and_exclude(
    family: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, {"m": family}, install=True)
    img, gt = _scene()
    seg = get_segmenter("auto")
    assert isinstance(seg, ModelSegmenter) and seg.engine == "m"
    seg.set_image(img)

    click = seg.segment([(486.0, 281.0, 1)])
    assert _iou(click.outer, gt) > 0.85, family
    assert click.score == pytest.approx(0.9)
    xs = [p[0] for p in click.quad]
    assert 465 < min(xs) and max(xs) < 507  # in full-image pixels, not the model frame

    boxed = seg.segment([], (468.0, 258.0, 504.0, 304.0))
    assert _iou(boxed.outer, gt) > 0.85, family

    both = seg.segment([(486.0, 281.0, 1), (450.0, 270.0, 0)], (468.0, 258.0, 504.0, 304.0))
    assert _iou(both.outer, gt) > 0.85, family


def test_sam1_prompt_tensors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, {"m": "sam1"}, install=True)
    img, _ = _scene()
    seg = get_segmenter("m")
    seg.set_image(img)
    seg.segment([(486.0, 281.0, 1)])
    seg.segment([(486.0, 281.0, 1)], (468.0, 258.0, 504.0, 304.0))
    decoder = fake_onnx.SESSIONS[1]
    points_only, with_box = decoder.calls
    scale = 1024 / 960
    np.testing.assert_allclose(
        points_only["point_coords"][0], [[486 * scale, 281 * scale], [0, 0]], rtol=1e-5
    )
    assert points_only["point_labels"][0].tolist() == [1, -1]
    assert with_box["point_labels"][0].tolist() == [1, 2, 3]
    assert with_box["orig_im_size"].tolist() == [540, 960]
    encoder = fake_onnx.SESSIONS[0]
    assert encoder.calls[0]["input_image"].shape == (576, 1024, 3)


def test_efficientsam_caps_prompt_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, {"m": "efficientsam"}, install=True)
    img, _ = _scene()
    seg = get_segmenter("m")
    seg.set_image(img)
    clicks = [(486.0, 281.0, 1)] + [(480.0 + i, 280.0, 1) for i in range(8)] + [(9.0, 9.0, 0)]
    seg.segment(clicks, (468.0, 258.0, 504.0, 304.0))
    call = fake_onnx.SESSIONS[1].calls[0]
    assert call["batched_point_labels"][0, 0].tolist() == [2, 3, 1, 1, 1, 1]
    # the box, the first click and the most recent include clicks; exclude clicks are not sent
    assert call["batched_point_coords"][0, 0, 2].tolist() == [486.0, 281.0]
    assert call["batched_point_coords"][0, 0, 5].tolist() == [487.0, 280.0]


def test_sam2_box_only_sends_empty_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(tmp_path, monkeypatch, {"m": "sam2_hf"}, install=True)
    img, _ = _scene()
    seg = get_segmenter("m")
    seg.set_image(img)
    seg.segment([], (468.0, 258.0, 504.0, 304.0))
    call = fake_onnx.SESSIONS[1].calls[0]
    assert call["input_points"].shape == (1, 1, 0, 2)
    assert call["input_labels"].shape == (1, 1, 0)
    np.testing.assert_allclose(
        call["input_boxes"][0, 0],
        [468 * 1024 / 960, 258 * 1024 / 540, 504 * 1024 / 960, 304 * 1024 / 540],
        rtol=1e-5,
    )


def test_model_missing_adapter_input_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, {"m": "efficientsam"}, install=True)
    seg = get_segmenter("m")
    session = fake_onnx.SESSIONS[0]
    session.inputs.append(fake_onnx.Node("extra_input", [1]))
    with pytest.raises(FkError) as info:
        seg.set_image(_scene()[0])
    assert "extra_input" in info.value.message
