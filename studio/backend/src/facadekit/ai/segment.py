from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from facadekit.ai.adapters import (
    ADAPTERS,
    SAM_MEAN,
    SAM_SIZE,
    SAM_STD,
    Box,
    SessionFactory,
    ort_session,
)
from facadekit.errors import AI_UNAVAILABLE, BAD_REQUEST, INTERNAL, NOT_FOUND, FkError

__all__ = [
    "SAM_MEAN",
    "SAM_SIZE",
    "SAM_STD",
    "Box",
    "GrabCutSegmenter",
    "MobileSamSegmenter",
    "ModelSegmenter",
    "SegmentResult",
    "Segmenter",
    "engine_of",
    "get_segmenter",
    "mask_to_polygon_and_quad",
    "normalise_box",
    "postprocess_mask",
]

log = logging.getLogger(__name__)

Point = tuple[float, float]
PromptPoint = tuple[float, float, int]

MAX_POLYGON_POINTS = 64
MIN_POLYGON_EPS_PX = 0.75  # small objects: do not keep every pixel step of the outline
GRABCUT_LONG_SIDE = 800
MIN_BOX_PX = 2.0


@dataclass
class SegmentResult:
    outer: list[Point]
    quad: list[Point]
    score: float


class Segmenter(Protocol):
    def set_image(self, bgr: np.ndarray) -> None: ...

    def segment(
        self, points: list[tuple[float, float, int]], box: Box | None = None
    ) -> SegmentResult: ...


def normalise_box(box: Sequence[float] | None, size: tuple[int, int] | None = None) -> Box | None:
    """Order a box as (x0, y0, x1, y1), clip it to ``size`` (w, h) and reject tiny ones."""
    if box is None:
        return None
    if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
        raise FkError(BAD_REQUEST, "A box needs four numbers")
    x0, x1 = sorted((float(box[0]), float(box[2])))
    y0, y1 = sorted((float(box[1]), float(box[3])))
    if size is not None:
        w, h = size
        x0, x1 = min(max(x0, 0.0), w - 1.0), min(max(x1, 0.0), w - 1.0)
        y0, y1 = min(max(y0, 0.0), h - 1.0), min(max(y1, 0.0), h - 1.0)
    if x1 - x0 < MIN_BOX_PX or y1 - y0 < MIN_BOX_PX:
        raise FkError(BAD_REQUEST, "The box is too small")
    return (x0, y0, x1, y1)


def _check_prompt(points: Sequence[PromptPoint], box: Box | None) -> None:
    if box is None and not any(p[2] == 1 for p in points):
        raise FkError(BAD_REQUEST, "At least one include point is required")


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill every hole inside the mask's outer outlines."""
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(binary)
    if contours:
        cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return filled.astype(bool)


def postprocess_mask(
    mask: np.ndarray, points: Sequence[PromptPoint], box: Box | None = None
) -> np.ndarray:
    """Clean a model or GrabCut mask for one object.

    1. With a box: drop everything outside the box (plus a small margin).
    2. Keep only the connected component(s) under the include clicks. A click that misses
       the mask keeps the component nearest to it. With a box and no include click, keep the
       largest component.
    3. Fill holes.

    Returns a bool mask of the same shape (may be empty).
    """
    binary = np.asarray(mask) > 0
    if binary.ndim != 2:
        raise FkError(BAD_REQUEST, "Nothing was selected")
    h, w = binary.shape
    if box is not None:
        margin_x = 0.04 * (box[2] - box[0]) + 2.0
        margin_y = 0.04 * (box[3] - box[1]) + 2.0
        x0, x1 = max(0, math.floor(box[0] - margin_x)), min(w, math.ceil(box[2] + margin_x) + 1)
        y0, y1 = max(0, math.floor(box[1] - margin_y)), min(h, math.ceil(box[3] + margin_y) + 1)
        clipped = np.zeros_like(binary)
        clipped[y0:y1, x0:x1] = binary[y0:y1, x0:x1]
        binary = clipped
    if not binary.any():
        return binary
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary.astype(np.uint8), connectivity=8
    )
    keep: set[int] = set()
    includes = [p for p in points if p[2] == 1]
    for p in includes:
        x = min(max(round(p[0]), 0), w - 1)
        y = min(max(round(p[1]), 0), h - 1)
        if labels[y, x] > 0:
            keep.add(int(labels[y, x]))
    if not keep:
        if includes:
            ys, xs = np.nonzero(labels)
            px, py = includes[0][0], includes[0][1]
            nearest = int(np.argmin((xs - px) ** 2 + (ys - py) ** 2))
            keep.add(int(labels[ys[nearest], xs[nearest]]))
        else:
            keep.add(1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))
    kept = np.isin(labels, sorted(keep))
    return fill_holes(kept)


def _order_quad(pts: np.ndarray) -> list[Point]:
    pts = pts.reshape(-1, 2).astype(np.float64)
    centre = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0])
    ordered = pts[np.argsort(angles)]
    first = int(np.argmin(ordered[:, 0] + ordered[:, 1]))
    ordered = np.roll(ordered, -first, axis=0)
    return [(float(x), float(y)) for x, y in ordered]


def mask_to_polygon_and_quad(mask: np.ndarray) -> tuple[list[Point], list[Point]]:
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    if binary.ndim != 2 or not binary.any():
        raise FkError(BAD_REQUEST, "Nothing was selected")
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    component = (labels == largest).astype(np.uint8)
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    perimeter = float(cv2.arcLength(contour, True))

    eps = max(0.004 * perimeter, MIN_POLYGON_EPS_PX)
    poly = cv2.approxPolyDP(contour, eps, True)
    while len(poly) > MAX_POLYGON_POINTS:
        eps *= 1.25
        poly = cv2.approxPolyDP(contour, eps, True)
    outer = [(float(p[0][0]), float(p[0][1])) for p in poly]

    quad_pts: np.ndarray | None = None
    for percent in np.arange(1.0, 8.0001, 0.25):
        approx = cv2.approxPolyDP(contour, float(percent) / 100.0 * perimeter, True)
        if len(approx) == 4:
            quad_pts = approx
            break
    if quad_pts is None:
        quad_pts = cv2.boxPoints(cv2.minAreaRect(contour))
    return outer, _order_quad(quad_pts)


def _result(mask: np.ndarray, score: float) -> SegmentResult:
    outer, quad = mask_to_polygon_and_quad(mask)
    return SegmentResult(outer=outer, quad=quad, score=score)


class GrabCutSegmenter:
    engine = "grabcut"
    label = "Simple selector (GrabCut)"

    def __init__(self) -> None:
        self._image: np.ndarray | None = None
        self._full: np.ndarray | None = None
        self._scale = 1.0
        self._full_size = (0, 0)

    @property
    def image_size(self) -> tuple[int, int] | None:
        return self._full_size if self._full is not None else None

    def set_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        self._full_size = (w, h)
        self._full = np.ascontiguousarray(bgr)
        self._scale = min(1.0, GRABCUT_LONG_SIDE / max(w, h))
        if self._scale < 1.0:
            size = (max(1, round(w * self._scale)), max(1, round(h * self._scale)))
            bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
        self._image = np.ascontiguousarray(bgr)

    def segment(
        self, points: list[tuple[float, float, int]], box: Box | None = None
    ) -> SegmentResult:
        if self._image is None:
            raise FkError(BAD_REQUEST, "No image has been set")
        _check_prompt(points, box)
        if box is not None:
            mask = self._segment_box(points, box)
        else:
            mask = self._segment_points(points)
        mask = postprocess_mask(mask, points, box)
        return _result(mask, 0.5)

    def _segment_box(self, points: list[PromptPoint], box: Box) -> np.ndarray:
        """GrabCut initialised with a mask: outside the box is background, inside is probably
        the object, clicks are definite. Runs on a crop around the box so small objects keep
        their detail (the point path works on the whole picture at 800 px)."""
        assert self._full is not None
        full_w, full_h = self._full_size
        bw, bh = box[2] - box[0], box[3] - box[1]
        pad = max(8.0, 0.25 * max(bw, bh))
        cx0, cy0 = max(0, math.floor(box[0] - pad)), max(0, math.floor(box[1] - pad))
        cx1 = min(full_w, math.ceil(box[2] + pad) + 1)
        cy1 = min(full_h, math.ceil(box[3] + pad) + 1)
        crop = self._full[cy0:cy1, cx0:cx1]
        ch, cw = crop.shape[:2]
        scale = min(1.0, GRABCUT_LONG_SIDE / max(cw, ch))
        if scale < 1.0:
            size = (max(1, round(cw * scale)), max(1, round(ch * scale)))
            crop = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
        h, w = crop.shape[:2]

        def to_crop(x: float, y: float) -> tuple[int, int]:
            return (
                min(max(round((x - cx0) * scale), 0), w - 1),
                min(max(round((y - cy0) * scale), 0), h - 1),
            )

        gc = np.full((h, w), cv2.GC_BGD, dtype=np.uint8)
        (bx0, by0), (bx1, by1) = to_crop(box[0], box[1]), to_crop(box[2], box[3])
        gc[by0 : by1 + 1, bx0 : bx1 + 1] = cv2.GC_PR_FGD
        if not (gc != cv2.GC_PR_FGD).any():
            # The box covers the whole picture: let its border be probable background.
            ring = max(1, round(0.02 * max(w, h)))
            gc[:ring, :] = gc[-ring:, :] = cv2.GC_PR_BGD
            gc[:, :ring] = gc[:, -ring:] = cv2.GC_PR_BGD
        radius = max(2, round(0.02 * math.hypot(w, h)))
        for p in points:
            if p[2] == 0:
                cv2.circle(gc, to_crop(p[0], p[1]), radius, int(cv2.GC_BGD), -1)
        for p in points:
            if p[2] == 1:
                cv2.circle(gc, to_crop(p[0], p[1]), radius, int(cv2.GC_FGD), -1)
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        cv2.grabCut(crop, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)  # type: ignore[call-overload]
        small = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)
        if scale < 1.0:
            small = cv2.resize(small, (cx1 - cx0, cy1 - cy0), interpolation=cv2.INTER_NEAREST)
        mask = np.zeros((full_h, full_w), np.uint8)
        mask[cy0:cy1, cx0:cx1] = small
        return mask

    def _segment_points(self, points: list[PromptPoint]) -> np.ndarray:
        assert self._image is not None
        includes = [p for p in points if p[2] == 1]
        h, w = self._image.shape[:2]
        diag = math.hypot(w, h)
        small = max(1, round(0.02 * diag))
        large = max(1, round(0.12 * diag))
        gc = np.full((h, w), cv2.GC_PR_BGD, dtype=np.uint8)

        def disc(p: tuple[float, float, int], radius: int, value: int) -> None:
            centre = (round(p[0] * self._scale), round(p[1] * self._scale))
            cv2.circle(gc, centre, radius, int(value), -1)

        disc(includes[0], large, cv2.GC_PR_FGD)
        for p in points:
            if p[2] == 0:
                disc(p, small, cv2.GC_BGD)
        for p in includes:
            disc(p, small, cv2.GC_FGD)
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        cv2.grabCut(self._image, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)  # type: ignore[call-overload]
        mask = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)
        if self._scale < 1.0:
            mask = cv2.resize(mask, self._full_size, interpolation=cv2.INTER_NEAREST)
        return np.asarray(mask, dtype=np.uint8)


class ModelSegmenter:
    """A promptable segmentation model from the registry, run with onnxruntime on the CPU.

    Loads only files already on disk with the registry checksum: it never downloads. A model
    that is not installed raises ``AI_UNAVAILABLE``; the user installs it through the
    confirmed download flow (``POST /ai/models/{engine}/download``).
    """

    def __init__(self, engine_id: str, *, session_factory: SessionFactory | None = None) -> None:
        from facadekit.ai import engines, models

        info = engines.get_engine(engine_id)
        if info is None:
            raise FkError(NOT_FOUND, f"Unknown segmentation engine: {engine_id}")
        if not info.published:
            raise FkError(AI_UNAVAILABLE, f"{info.label} is not published yet.")
        if not info.installed:
            raise FkError(
                AI_UNAVAILABLE,
                f"{info.label} is not installed. Download it from Magic select first.",
            )
        adapter_cls = ADAPTERS.get(info.adapter)
        if adapter_cls is None:
            raise FkError(INTERNAL, f"Unknown segmentation adapter: {info.adapter}")
        missing = set(adapter_cls.roles) - info.roles()
        if missing:
            raise FkError(INTERNAL, f"{info.label} lacks files for: {', '.join(sorted(missing))}")
        paths = {f.role: models.model_path(f.name) for f in info.files}
        factory = session_factory or _session_factory or ort_session
        self.engine = info.id
        self.label = info.label
        self._adapter = adapter_cls(paths, factory)
        self._size: tuple[int, int] | None = None

    @property
    def image_size(self) -> tuple[int, int] | None:
        return self._size

    def set_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        self._size = (w, h)
        self._adapter.set_image(bgr)

    def segment(
        self, points: list[tuple[float, float, int]], box: Box | None = None
    ) -> SegmentResult:
        if self._size is None:
            raise FkError(BAD_REQUEST, "No image has been set")
        _check_prompt(points, box)
        mask, score = self._adapter.predict(points, box)
        mask = postprocess_mask(mask, points, box)
        return _result(mask, score)


class MobileSamSegmenter(ModelSegmenter):
    """The original T-106 engine, kept by name (``prefer="mobilesam"``)."""

    def __init__(self, *, session_factory: SessionFactory | None = None) -> None:
        super().__init__("mobilesam", session_factory=session_factory)


# Tests (and the evaluation harness) may install a factory that builds stub sessions.
_session_factory: SessionFactory | None = None


def set_session_factory(factory: SessionFactory | None) -> None:
    global _session_factory
    _session_factory = factory


def engine_of(segmenter: object) -> str:
    return str(getattr(segmenter, "engine", "grabcut"))


def get_segmenter(prefer: str = "auto") -> Segmenter:
    """``"grabcut"``, ``"auto"`` (the first installed model, else GrabCut) or an engine id.

    Never downloads. An explicitly requested engine that is not installed raises
    ``AI_UNAVAILABLE``; ``"auto"`` falls back to GrabCut instead.
    """
    from facadekit.ai import engines

    if prefer == "grabcut":
        return GrabCutSegmenter()
    if prefer != "auto":
        return ModelSegmenter(prefer)
    chosen = engines.auto_engine()
    if chosen == engines.GRABCUT:
        return GrabCutSegmenter()
    try:
        return ModelSegmenter(chosen)
    except (ImportError, OSError, FkError) as exc:
        log.warning("Smart select %s could not load, using GrabCut: %s", chosen, exc)
        return GrabCutSegmenter()
