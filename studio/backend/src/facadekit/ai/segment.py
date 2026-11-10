from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import cv2
import numpy as np

from facadekit.errors import BAD_REQUEST, FkError

Point = tuple[float, float]

MAX_POLYGON_POINTS = 64
GRABCUT_LONG_SIDE = 800
SAM_SIZE = 1024
SAM_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
SAM_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)


@dataclass
class SegmentResult:
    outer: list[Point]
    quad: list[Point]
    score: float


class Segmenter(Protocol):
    def set_image(self, bgr: np.ndarray) -> None: ...

    def segment(self, points: list[tuple[float, float, int]]) -> SegmentResult: ...


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

    eps = 0.004 * perimeter
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
    def __init__(self) -> None:
        self._image: np.ndarray | None = None
        self._scale = 1.0
        self._full_size = (0, 0)

    def set_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        self._full_size = (w, h)
        self._scale = min(1.0, GRABCUT_LONG_SIDE / max(w, h))
        if self._scale < 1.0:
            size = (max(1, round(w * self._scale)), max(1, round(h * self._scale)))
            bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
        self._image = np.ascontiguousarray(bgr)

    def segment(self, points: list[tuple[float, float, int]]) -> SegmentResult:
        if self._image is None:
            raise FkError(BAD_REQUEST, "No image has been set")
        includes = [p for p in points if p[2] == 1]
        if not includes:
            raise FkError(BAD_REQUEST, "At least one include point is required")
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
        return _result(mask, 0.5)


class MobileSamSegmenter:
    def __init__(self) -> None:
        import onnxruntime as ort

        from facadekit.ai.models import ensure_model

        encoder_path = ensure_model("mobilesam_encoder")
        decoder_path = ensure_model("mobilesam_decoder")
        providers = ["CPUExecutionProvider"]
        self._encoder: Any = ort.InferenceSession(str(encoder_path), providers=providers)
        self._decoder: Any = ort.InferenceSession(str(decoder_path), providers=providers)
        self._embedding: np.ndarray | None = None
        self._scale = 1.0
        self._orig_size = (0, 0)

    def set_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        self._orig_size = (h, w)
        self._scale = SAM_SIZE / max(h, w)
        size = (max(1, round(w * self._scale)), max(1, round(h * self._scale)))
        rgb = cv2.cvtColor(cv2.resize(bgr, size, interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        norm = (rgb.astype(np.float32) - SAM_MEAN) / SAM_STD
        padded = np.zeros((SAM_SIZE, SAM_SIZE, 3), dtype=np.float32)
        padded[: norm.shape[0], : norm.shape[1]] = norm
        tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None])
        name = self._encoder.get_inputs()[0].name
        self._embedding = np.asarray(self._encoder.run(None, {name: tensor})[0])

    def segment(self, points: list[tuple[float, float, int]]) -> SegmentResult:
        if self._embedding is None:
            raise FkError(BAD_REQUEST, "No image has been set")
        if not any(p[2] == 1 for p in points):
            raise FkError(BAD_REQUEST, "At least one include point is required")
        coords = np.array([[p[0] * self._scale, p[1] * self._scale] for p in points], np.float32)
        labels = np.array([p[2] for p in points], dtype=np.float32)
        coords = np.concatenate([coords, np.zeros((1, 2), np.float32)])[None]
        labels = np.concatenate([labels, np.array([-1.0], np.float32)])[None]
        outputs = self._decoder.run(
            None,
            {
                "image_embeddings": self._embedding,
                "point_coords": coords,
                "point_labels": labels,
                "mask_input": np.zeros((1, 1, 256, 256), np.float32),
                "has_mask_input": np.zeros(1, np.float32),
                "orig_im_size": np.array(self._orig_size, dtype=np.float32),
            },
        )
        masks = np.asarray(outputs[0])
        scores = np.asarray(outputs[1]).reshape(-1)
        best = int(np.argmax(scores)) if masks.shape[1] > 1 else 0
        mask = masks[0, best] > 0
        return _result(mask, float(scores[best]))


def get_segmenter(prefer: Literal["auto", "mobilesam", "grabcut"] = "auto") -> Segmenter:
    if prefer == "grabcut":
        return GrabCutSegmenter()
    if prefer == "mobilesam":
        return MobileSamSegmenter()
    try:
        return MobileSamSegmenter()
    except (ImportError, FkError):
        return GrabCutSegmenter()
