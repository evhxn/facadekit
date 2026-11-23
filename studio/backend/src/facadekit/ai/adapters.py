"""ONNX adapters for promptable segmentation models (spike S-6, docs/16).

Each adapter turns an image plus prompts (points and an optional box, in image pixels) into a
boolean mask of the full image and a score. Adapters only know their model's tensor contract;
loading files, post-processing and polygon fitting live in ``segment.py``.

Tensor contracts and their evidence are recorded in ``docs/16-magic-select-model.md``. Names
that a session does not declare are not fed, and every feed is cast to the dtype the session
declares, so small export differences (int64 vs float labels) do not break inference.

``onnxruntime`` is imported only when a real session is created; tests pass a
``session_factory`` returning stub sessions with the same contract.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from facadekit.errors import INTERNAL, FkError

log = logging.getLogger(__name__)

PromptPoint = tuple[float, float, int]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in image pixels
SessionFactory = Callable[[Path], Any]

SAM_SIZE = 1024
SAM_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
SAM_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
EFFICIENTSAM_MAX_POINTS = 6  # decoder_max_num_input_points in the EfficientSAM builder

_DTYPES: dict[str, Any] = {
    "tensor(float)": np.float32,
    "tensor(float16)": np.float16,
    "tensor(double)": np.float64,
    "tensor(int64)": np.int64,
    "tensor(int32)": np.int32,
    "tensor(bool)": np.bool_,
}


def ort_session(path: Path) -> Any:
    """A CPU onnxruntime session (the default ``SessionFactory``)."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


class Adapter(Protocol):
    def set_image(self, bgr: np.ndarray) -> None: ...

    def predict(self, points: Sequence[PromptPoint], box: Box | None) -> tuple[np.ndarray, float]:
        """Return (bool mask of the full image, score)."""
        ...


def _input_meta(session: Any) -> dict[str, Any]:
    return {i.name: i for i in session.get_inputs()}


def _feed(session: Any, values: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Keep only the inputs the session declares, cast to the declared dtype."""
    meta = _input_meta(session)
    feeds: dict[str, np.ndarray] = {}
    for name, spec in meta.items():
        if name not in values:
            raise FkError(INTERNAL, f"Segmentation model needs an input this adapter lacks: {name}")
        dtype = _DTYPES.get(str(getattr(spec, "type", "tensor(float)")), np.float32)
        feeds[name] = np.ascontiguousarray(np.asarray(values[name]).astype(dtype, copy=False))
    return feeds


def _run(session: Any, values: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    names = [o.name for o in session.get_outputs()]
    outputs = session.run(None, _feed(session, values))
    return {name: np.asarray(out) for name, out in zip(names, outputs, strict=False)}


def _output(outputs: dict[str, np.ndarray], *names: str, index: int) -> np.ndarray:
    for name in names:
        if name in outputs:
            return outputs[name]
    values = list(outputs.values())
    if index < len(values):
        return values[index]
    raise FkError(INTERNAL, f"Segmentation model returned no {names[0]}")


def _pick(masks: np.ndarray, scores: np.ndarray, *, many_prompts: bool) -> int:
    """Choose one of the candidate masks.

    SAM-style decoders exported with every mask token return 4 masks; index 0 is the
    single-mask output SAM recommends for prompts with several points or a box, 1..3 are the
    ambiguity-aware masks meant for a single click. Decoders that return 3 masks only carry
    the ambiguity-aware ones.
    """
    count = masks.shape[0]
    if count <= 1:
        return 0
    if count == 4:
        return 0 if many_prompts else 1 + int(np.argmax(scores[1:4]))
    return int(np.argmax(scores[:count]))


def _work_size(w: int, h: int, long_side: int) -> tuple[float, tuple[int, int]]:
    scale = min(1.0, long_side / max(w, h))
    return scale, (max(1, round(w * scale)), max(1, round(h * scale)))


def _to_full(logits: np.ndarray, full: tuple[int, int]) -> np.ndarray:
    """Resize a (h, w) logit map to the full (w, h) image and threshold at 0."""
    w, h = full
    lg = np.asarray(logits, dtype=np.float32)
    if lg.shape != (h, w):
        lg = cv2.resize(lg, (w, h), interpolation=cv2.INTER_LINEAR)
    return lg > 0.0


class Sam1Adapter:
    """SAM-1 family split into an image encoder and the standard SAM ONNX decoder.

    Used for MobileSAM. Encoder: either ``(1, 3, 1024, 1024)`` normalised and padded, or (when
    exported with preprocessing inside, as the Acly MobileSAM export is) ``(h, w, 3)`` RGB
    0..255 with the long side resized to 1024. Decoder inputs: ``image_embeddings``,
    ``point_coords``, ``point_labels``, ``mask_input``, ``has_mask_input``, ``orig_im_size``;
    outputs ``masks`` (logits at ``orig_im_size``), ``iou_predictions``, ``low_res_masks``.
    """

    roles = ("encoder", "decoder")

    def __init__(self, paths: dict[str, Path], factory: SessionFactory) -> None:
        self._encoder = factory(paths["encoder"])
        self._decoder = factory(paths["decoder"])
        self._embedding: np.ndarray | None = None
        self._scale = 1.0
        self._full = (0, 0)
        self._work = (0, 0)

    def set_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        self._full = (w, h)
        self._scale = SAM_SIZE / max(h, w)
        size = (max(1, round(w * self._scale)), max(1, round(h * self._scale)))
        self._work = size
        resized = cv2.resize(bgr, size, interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
        spec = self._encoder.get_inputs()[0]
        if len(getattr(spec, "shape", [])) == 3:
            tensor = rgb  # preprocessing (normalise, pad, NCHW) is inside the graph
        else:
            norm = (rgb - SAM_MEAN) / SAM_STD
            padded = np.zeros((SAM_SIZE, SAM_SIZE, 3), dtype=np.float32)
            padded[: norm.shape[0], : norm.shape[1]] = norm
            tensor = padded.transpose(2, 0, 1)[None]
        out = _run(self._encoder, {spec.name: tensor})
        self._embedding = _output(out, "image_embeddings", index=0)

    def predict(self, points: Sequence[PromptPoint], box: Box | None) -> tuple[np.ndarray, float]:
        if self._embedding is None:
            raise FkError(INTERNAL, "No image has been set")
        s = self._scale
        coords = [[p[0] * s, p[1] * s] for p in points]
        labels = [float(p[2]) for p in points]
        if box is not None:
            coords += [[box[0] * s, box[1] * s], [box[2] * s, box[3] * s]]
            labels += [2.0, 3.0]
        else:
            coords.append([0.0, 0.0])  # padding point, as in SAM's ONNX example
            labels.append(-1.0)
        w, h = self._full
        out = _run(
            self._decoder,
            {
                "image_embeddings": self._embedding,
                "point_coords": np.array(coords, np.float32)[None],
                "point_labels": np.array(labels, np.float32)[None],
                "mask_input": np.zeros((1, 1, 256, 256), np.float32),
                "has_mask_input": np.zeros(1, np.float32),
                "orig_im_size": np.array([h, w], np.float32),
            },
        )
        masks = _output(out, "masks", index=0)[0]
        scores = _output(out, "iou_predictions", "scores", index=1).reshape(-1)
        best = _pick(masks, scores, many_prompts=len(points) > 1 or box is not None)
        return _to_full(masks[best], self._full), float(scores[best])


class EfficientSamAdapter:
    """EfficientSAM (yformer/EfficientSAM ONNX export, encoder + decoder).

    Encoder ``batched_images`` ``(1, 3, h, w)`` RGB 0..1 (resizing and ImageNet normalisation
    are inside the graph) -> ``image_embeddings`` ``(1, 256, 64, 64)``. Decoder inputs
    ``image_embeddings``, ``batched_point_coords`` ``(1, 1, n, 2)`` in input pixels,
    ``batched_point_labels`` ``(1, 1, n)`` (1 include, 0 exclude, 2/3 box corners),
    ``orig_im_size`` int64 ``[h, w]``; outputs ``output_masks`` ``(1, 1, 3, h, w)`` logits and
    ``iou_predictions`` ``(1, 1, 3)``. At most 6 prompt points are used by the model, and it
    has no embedding for exclude clicks (label 0), so those are not sent.
    """

    roles = ("encoder", "decoder")
    long_side = SAM_SIZE

    def __init__(self, paths: dict[str, Path], factory: SessionFactory) -> None:
        self._encoder = factory(paths["encoder"])
        self._decoder = factory(paths["decoder"])
        self._embedding: np.ndarray | None = None
        self._scale = 1.0
        self._full = (0, 0)
        self._work = (0, 0)

    def set_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        self._full = (w, h)
        self._scale, self._work = _work_size(w, h, self.long_side)
        img = bgr
        if self._scale < 1.0:
            img = cv2.resize(bgr, self._work, interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        name = self._encoder.get_inputs()[0].name
        out = _run(self._encoder, {name: rgb.transpose(2, 0, 1)[None]})
        self._embedding = _output(out, "image_embeddings", index=0)

    def predict(self, points: Sequence[PromptPoint], box: Box | None) -> tuple[np.ndarray, float]:
        if self._embedding is None:
            raise FkError(INTERNAL, "No image has been set")
        s = self._scale
        prompt: list[tuple[float, float, float]] = []
        if box is not None:
            prompt += [(box[0] * s, box[1] * s, 2.0), (box[2] * s, box[3] * s, 3.0)]
        # EfficientSAM's prompt encoder has learned embeddings only for labels 1 (object),
        # 2/3 (box corners) and -1 (padding); a 0 ("not this") would be an untyped point.
        # Exclude clicks are therefore not sent; post-processing still uses them.
        prompt += [(p[0] * s, p[1] * s, 1.0) for p in points if p[2] == 1]
        if len(prompt) > EFFICIENTSAM_MAX_POINTS:
            # Keep the box, the first include click and the most recent clicks.
            log.info("EfficientSAM uses at most %d prompt points", EFFICIENTSAM_MAX_POINTS)
            head = 3 if box is not None else 1
            prompt = prompt[:head] + prompt[-(EFFICIENTSAM_MAX_POINTS - head) :]
        coords = np.array([[q[0], q[1]] for q in prompt], np.float32)[None, None]
        labels = np.array([q[2] for q in prompt], np.float32)[None, None]
        w, h = self._work
        out = _run(
            self._decoder,
            {
                "image_embeddings": self._embedding,
                "batched_point_coords": coords,
                "batched_point_labels": labels,
                "orig_im_size": np.array([h, w], np.int64),
            },
        )
        masks = _output(out, "output_masks", index=0)[0, 0]
        scores = _output(out, "iou_predictions", index=1).reshape(-1)
        best = _pick(masks, scores, many_prompts=len(prompt) > 1)
        return _to_full(masks[best], self._full), float(scores[best])


class Sam2HfAdapter:
    """SAM 2.1 as exported for Transformers.js (onnx-community/sam2.1-hiera-*-ONNX).

    ``vision_encoder``: ``pixel_values`` ``(1, 3, 1024, 1024)`` (image resized to 1024 x 1024
    without keeping the aspect ratio, RGB / 255, ImageNet mean/std) -> ``image_embeddings.0``
    ``(1, 32, 256, 256)``, ``.1`` ``(1, 64, 128, 128)``, ``.2`` ``(1, 256, 64, 64)``.
    ``prompt_encoder_mask_decoder``: ``input_points`` ``(1, 1, n, 2)`` in the 1024 frame,
    ``input_labels`` ``(1, 1, n)`` int64, ``input_boxes`` ``(1, b, 4)`` and the three embeddings
    -> ``iou_scores`` ``(1, 1, 3)``, ``pred_masks`` ``(1, 1, 3, 256, 256)`` logits,
    ``object_score_logits``. Both graphs keep their weights in ``.onnx_data`` files next to them.
    """

    roles = ("encoder", "encoder_data", "decoder", "decoder_data")

    def __init__(self, paths: dict[str, Path], factory: SessionFactory) -> None:
        self._encoder = factory(paths["encoder"])
        self._decoder = factory(paths["decoder"])
        self._embeddings: dict[str, np.ndarray] = {}
        self._full = (0, 0)

    def set_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        self._full = (w, h)
        img = cv2.resize(bgr, (SAM_SIZE, SAM_SIZE), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        norm = (rgb - IMAGENET_MEAN) / IMAGENET_STD
        name = self._encoder.get_inputs()[0].name
        out = _run(self._encoder, {name: norm.transpose(2, 0, 1)[None]})
        self._embeddings = {k: v for k, v in out.items() if k.startswith("image_embeddings")}
        if not self._embeddings:
            raise FkError(INTERNAL, "SAM 2 encoder returned no image embeddings")

    def predict(self, points: Sequence[PromptPoint], box: Box | None) -> tuple[np.ndarray, float]:
        if not self._embeddings:
            raise FkError(INTERNAL, "No image has been set")
        w, h = self._full
        sx, sy = SAM_SIZE / w, SAM_SIZE / h
        pts = np.array([[p[0] * sx, p[1] * sy] for p in points], np.float32).reshape(1, 1, -1, 2)
        labels = np.array([p[2] for p in points], np.int64).reshape(1, 1, -1)
        if box is None:
            boxes = np.zeros((1, 0, 4), np.float32)
        else:
            boxes = np.array([[[box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]]], np.float32)
        out = _run(
            self._decoder,
            {**self._embeddings, "input_points": pts, "input_labels": labels, "input_boxes": boxes},
        )
        masks = _output(out, "pred_masks", index=1)[0, 0]
        scores = _output(out, "iou_scores", index=0).reshape(-1)
        best = _pick(masks, scores, many_prompts=len(points) > 1 or box is not None)
        return _to_full(masks[best], self._full), float(scores[best])


ADAPTERS: dict[str, Any] = {
    "sam1": Sam1Adapter,
    "efficientsam": EfficientSamAdapter,
    "sam2_hf": Sam2HfAdapter,
}
