"""Mask post-processing for small objects and GrabCut box prompts (spike S-6)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from facadekit.ai.segment import (
    GrabCutSegmenter,
    fill_holes,
    mask_to_polygon_and_quad,
    normalise_box,
    postprocess_mask,
)
from facadekit.errors import FkError


def _blobs() -> np.ndarray:
    mask = np.zeros((200, 300), np.uint8)
    cv2.rectangle(mask, (20, 20), (150, 180), 1, -1)  # big neighbour
    cv2.rectangle(mask, (200, 80), (230, 110), 1, -1)  # the small object
    return mask


def test_keeps_the_component_under_the_click_not_the_largest() -> None:
    out = postprocess_mask(_blobs(), [(215.0, 95.0, 1)])
    assert out[95, 215] and not out[100, 80]
    assert out.sum() == 31 * 31


def test_two_include_clicks_keep_both_components() -> None:
    out = postprocess_mask(_blobs(), [(215.0, 95.0, 1), (80.0, 100.0, 1)])
    assert out[95, 215] and out[100, 80]


def test_click_that_misses_keeps_the_nearest_component() -> None:
    out = postprocess_mask(_blobs(), [(240.0, 95.0, 1)])  # just right of the small square
    assert out[95, 215] and not out[100, 80]


def test_exclude_clicks_never_choose_a_component() -> None:
    out = postprocess_mask(_blobs(), [(215.0, 95.0, 1), (80.0, 100.0, 0)])
    assert not out[100, 80]


def test_holes_are_filled() -> None:
    ring = np.zeros((100, 100), np.uint8)
    cv2.circle(ring, (50, 50), 30, 1, -1)
    cv2.circle(ring, (50, 50), 10, 0, -1)
    out = postprocess_mask(ring, [(50.0, 25.0, 1)])
    assert out[50, 50]
    assert fill_holes(ring)[50, 50]


def test_box_clips_spill_and_box_only_keeps_largest_inside() -> None:
    mask = _blobs()
    out = postprocess_mask(mask, [], (190.0, 70.0, 240.0, 120.0))
    assert out[95, 215] and not out[100, 80]
    spill = mask.copy()
    cv2.rectangle(spill, (200, 80), (290, 110), 1, -1)  # the model bled to the right
    out = postprocess_mask(spill, [(215.0, 95.0, 1)], (195.0, 75.0, 235.0, 115.0))
    assert not out[95, 280] and out[95, 215]


def test_empty_after_box_is_empty() -> None:
    out = postprocess_mask(_blobs(), [], (250.0, 150.0, 290.0, 190.0))
    assert not out.any()


def test_small_object_polygon_is_simplified_but_faithful() -> None:
    mask = np.zeros((120, 120), np.uint8)
    cv2.circle(mask, (60, 60), 9, 1, -1)  # a 19 px blob: every pixel step is a contour vertex
    outer, quad = mask_to_polygon_and_quad(mask)
    contour = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0][0]
    assert 4 <= len(outer) < len(contour)
    poly = np.zeros_like(mask)
    cv2.fillPoly(poly, [np.round(np.array(outer)).astype(np.int32)], 1)
    iou = (poly & mask).sum() / (poly | mask).sum()
    assert iou > 0.85
    assert len(quad) == 4


def test_normalise_box_orders_clips_and_rejects_tiny() -> None:
    assert normalise_box((50, 40, 10, 5), (40, 30)) == (10.0, 5.0, 39.0, 29.0)
    assert normalise_box(None) is None
    with pytest.raises(FkError):
        normalise_box((5, 5, 6, 30))
    with pytest.raises(FkError):
        normalise_box((0, 0, float("nan"), 3))


def _shelf() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(1)
    img = np.full((540, 960, 3), (60, 80, 100), np.uint8)
    for _ in range(80):
        x, y = int(rng.integers(0, 900)), int(rng.integers(0, 500))
        colour = tuple(int(c) for c in rng.integers(20, 220, 3))
        w, h = int(rng.integers(15, 80)), int(rng.integers(15, 50))
        cv2.rectangle(img, (x, y), (x + w, y + h), colour, -1)
    cv2.rectangle(img, (400, 250), (520, 320), (225, 225, 225), -1)  # white clutter
    gt = np.zeros(img.shape[:2], np.uint8)
    cv2.rectangle(gt, (522, 270), (548, 318), 1, -1)
    img[gt > 0] = (250, 250, 250)
    cv2.rectangle(img, (530, 285), (540, 295), (40, 40, 40), -1)  # the camera lens
    return img, gt.astype(bool)


def test_grabcut_box_selects_small_object_next_to_white_clutter() -> None:
    img, gt = _shelf()
    seg = GrabCutSegmenter()
    seg.set_image(img)
    result = seg.segment([], (519.0, 266.0, 551.0, 322.0))
    poly = np.zeros(gt.shape, np.uint8)
    cv2.fillPoly(poly, [np.round(np.array(result.outer)).astype(np.int32)], 1)
    iou = (poly.astype(bool) & gt).sum() / (poly.astype(bool) | gt).sum()
    assert iou > 0.8
    assert result.score == 0.5


def test_grabcut_box_covering_the_whole_picture_still_runs() -> None:
    img, _ = _shelf()
    seg = GrabCutSegmenter()
    seg.set_image(img)
    result = seg.segment([(535.0, 300.0, 1)], (0.0, 0.0, 959.0, 539.0))
    assert len(result.quad) == 4


def test_box_without_include_point_is_allowed_and_no_prompt_is_not() -> None:
    img, _ = _shelf()
    seg = GrabCutSegmenter()
    seg.set_image(img)
    with pytest.raises(FkError):
        seg.segment([(10.0, 10.0, 0)])
    assert len(seg.segment([(10.0, 10.0, 0)], (519.0, 266.0, 551.0, 322.0)).quad) == 4
