"""Stub onnxruntime sessions that follow each adapter's tensor contract (no real weights).

A fake model file is a few bytes: ``FAKEONNX <kind>``. ``fake_session(path)`` reads the kind
and returns a session whose ``get_inputs``/``get_outputs`` match the real export (names,
ranks and dtypes from docs/16) and whose ``run`` checks every input strictly.

The fake encoders pass the image through as the "embedding"; the fake decoders flood-fill that
image from the prompt (first include click, else the box centre), restricted to the box. So
the whole path (pre-processing, prompt scaling, mask resizing, post-processing) is exercised
with masks that follow the picture, without any model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAGIC = b"FAKEONNX "

KINDS = {
    "sam1": ("sam1_encoder_hwc", "sam1_decoder"),
    "sam1_nchw": ("sam1_encoder_nchw", "sam1_decoder"),
    "efficientsam": ("efficientsam_encoder", "efficientsam_decoder"),
    "sam2_hf": ("sam2_encoder", "sam2_decoder"),
}


@dataclass
class Node:
    name: str
    shape: list[Any]
    type: str = "tensor(float)"


@dataclass
class FakeSession:
    kind: str
    inputs: list[Node]
    outputs: list[Node]
    calls: list[dict[str, np.ndarray]] = field(default_factory=list)

    def get_inputs(self) -> list[Node]:
        return self.inputs

    def get_outputs(self) -> list[Node]:
        return self.outputs

    def run(self, names: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        assert names is None
        assert set(feeds) == {i.name for i in self.inputs}, (self.kind, sorted(feeds))
        for node in self.inputs:
            want = {"tensor(float)": np.float32, "tensor(int64)": np.int64}[node.type]
            got = feeds[node.name]
            assert got.dtype == want, (self.kind, node.name, got.dtype)
            assert got.ndim == len(node.shape), (self.kind, node.name, got.shape)
            for dim, size in zip(node.shape, got.shape, strict=True):
                assert not isinstance(dim, int) or dim == size, (self.kind, node.name, got.shape)
        self.calls.append(feeds)
        return RUNNERS[self.kind](feeds)


def write_fake_model(path: Path, kind: str) -> bytes:
    data = MAGIC + kind.encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


SESSIONS: list[FakeSession] = []


def fake_session(path: Path) -> FakeSession:
    raw = Path(path).read_bytes()
    assert raw.startswith(MAGIC), f"not a fake model: {path}"
    kind = raw[len(MAGIC) :].decode().strip()
    session = FakeSession(kind, *SIGNATURES[kind]())
    SESSIONS.append(session)
    return session


# --- the decoder "model": flood fill on the passed-through image --------------------------


def _flood(img: np.ndarray, seed: tuple[float, float], box: tuple[float, ...] | None) -> np.ndarray:
    """img (h, w, 3) any scale; returns uint8 (h, w) mask of the similar-colour region."""
    h, w = img.shape[:2]
    work = np.ascontiguousarray(np.clip(img, 0, 255).astype(np.uint8))
    sx = int(min(max(round(seed[0]), 0), w - 1))
    sy = int(min(max(round(seed[1]), 0), h - 1))
    ff = np.zeros((h + 2, w + 2), np.uint8)
    flags = 8 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (1 << 8)
    cv2.floodFill(work, ff, (sx, sy), (0, 0, 0), (12, 12, 12), (12, 12, 12), flags)
    mask = ff[1:-1, 1:-1].copy()
    if box is not None:
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        keep = np.zeros_like(mask)
        keep[max(0, y0) : y1 + 1, max(0, x0) : x1 + 1] = 1
        mask &= keep
    return mask


def _prompt(coords: np.ndarray, labels: np.ndarray) -> tuple[tuple[float, float], Any]:
    pts = coords.reshape(-1, 2)
    lab = labels.reshape(-1)
    box = None
    if (lab == 2).any() and (lab == 3).any():
        a, b = pts[int(np.argmax(lab == 2))], pts[int(np.argmax(lab == 3))]
        box = (float(a[0]), float(a[1]), float(b[0]), float(b[1]))
    inc = pts[lab == 1]
    if len(inc):
        seed = (float(inc[0][0]), float(inc[0][1]))
    else:
        assert box is not None, "a prompt needs an include point or a box"
        seed = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    return seed, box


def _logits(mask: np.ndarray) -> np.ndarray:
    return np.where(mask > 0, 10.0, -10.0).astype(np.float32)


def _stack(best: np.ndarray, count: int, best_index: int) -> tuple[np.ndarray, np.ndarray]:
    """``count`` candidate masks: the flood at ``best_index``; whole-picture decoys elsewhere."""
    decoy = np.full_like(best, 10.0)
    masks = np.stack([best if i == best_index else decoy for i in range(count)])
    scores = np.array([0.9 if i == best_index else 0.4 for i in range(count)], np.float32)
    return masks, scores


# --- signatures and runners ----------------------------------------------------------------

F, I64 = "tensor(float)", "tensor(int64)"


def _sam1_encoder_hwc() -> tuple[list[Node], list[Node]]:
    return [Node("input_image", ["h", "w", 3])], [Node("image_embeddings", [1, "c", "h", "w"])]


def _sam1_encoder_nchw() -> tuple[list[Node], list[Node]]:
    return [Node("input_image", [1, 3, 1024, 1024])], [Node("image_embeddings", [1, 3, 1024, 1024])]


def _sam1_decoder() -> tuple[list[Node], list[Node]]:
    ins = [
        Node("image_embeddings", [1, "c", "h", "w"]),
        Node("point_coords", [1, "n", 2]),
        Node("point_labels", [1, "n"]),
        Node("mask_input", [1, 1, 256, 256]),
        Node("has_mask_input", [1]),
        Node("orig_im_size", [2]),
    ]
    outs = [Node("masks", []), Node("iou_predictions", []), Node("low_res_masks", [])]
    return ins, outs


def _run_sam1_encoder(feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    x = feeds["input_image"]
    if x.ndim == 3:  # (h, w, 3) RGB 0..255
        return [x.transpose(2, 0, 1)[None].copy()]
    # (1, 3, 1024, 1024) normalised: undo normalisation so the decoder floods colours
    mean = np.array([123.675, 116.28, 103.53], np.float32)[:, None, None]
    std = np.array([58.395, 57.12, 57.375], np.float32)[:, None, None]
    return [(x[0] * std + mean)[None]]


def _run_sam1_decoder(feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    labels = feeds["point_labels"][0]
    if not ((labels == 2).any() and (labels == 3).any()):
        assert labels[-1] == -1, "points-only prompts end with the padding point"
    img = feeds["image_embeddings"][0].transpose(1, 2, 0)  # 1024 frame (maybe padded)
    seed, box = _prompt(feeds["point_coords"][0], labels)
    small = _flood(img, seed, box)
    h, w = (int(v) for v in feeds["orig_im_size"])
    scale = 1024 / max(h, w)
    small = small[: max(1, round(h * scale)), : max(1, round(w * scale))]
    full = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    # SAM: mask 0 is the single-mask output meant for several prompts or a box; 1..3 are the
    # ambiguity-aware masks for one click. The fake puts the right mask where SAM would.
    many = box is not None or int((labels >= 0).sum()) > 1
    masks, scores = _stack(_logits(full), 4, 0 if many else 2)
    return [masks[None], scores[None], np.zeros((1, 4, 256, 256), np.float32)]


def _efficientsam_encoder() -> tuple[list[Node], list[Node]]:
    return [Node("batched_images", ["b", 3, "h", "w"])], [Node("image_embeddings", [])]


def _efficientsam_decoder() -> tuple[list[Node], list[Node]]:
    ins = [
        Node("image_embeddings", ["b", "c", "h", "w"]),
        Node("batched_point_coords", [1, 1, "n", 2]),
        Node("batched_point_labels", [1, 1, "n"]),
        Node("orig_im_size", [2], I64),
    ]
    return ins, [Node("output_masks", []), Node("iou_predictions", []), Node("low_res", [])]


def _run_efficientsam_encoder(feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    x = feeds["batched_images"]
    assert 0.0 <= float(x.min()) and float(x.max()) <= 1.0, "EfficientSAM takes RGB 0..1"
    return [x * 255.0]


def _run_efficientsam_decoder(feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    labels = feeds["batched_point_labels"][0, 0]
    assert len(labels) <= 6
    img = feeds["image_embeddings"][0].transpose(1, 2, 0)
    h, w = (int(v) for v in feeds["orig_im_size"])
    assert img.shape[:2] == (h, w)
    seed, box = _prompt(feeds["batched_point_coords"][0, 0], labels)
    masks, scores = _stack(_logits(_flood(img, seed, box)), 3, 1)
    return [masks[None, None], scores[None, None], np.zeros((1, 3, 256, 256), np.float32)]


def _sam2_encoder() -> tuple[list[Node], list[Node]]:
    outs = [
        Node("image_embeddings.0", [1, 32, 256, 256]),
        Node("image_embeddings.1", [1, 64, 128, 128]),
        Node("image_embeddings.2", [1, 256, 64, 64]),
    ]
    return [Node("pixel_values", [1, 3, 1024, 1024])], outs


def _sam2_decoder() -> tuple[list[Node], list[Node]]:
    ins = [
        Node("input_points", [1, 1, "n", 2]),
        Node("input_labels", [1, 1, "n"], I64),
        Node("input_boxes", [1, "b", 4]),
        Node("image_embeddings.0", [1, "c", "h", "w"]),
        Node("image_embeddings.1", [1, 64, 128, 128]),
        Node("image_embeddings.2", [1, 256, 64, 64]),
    ]
    outs = [Node("iou_scores", []), Node("pred_masks", []), Node("object_score_logits", [])]
    return ins, outs


def _run_sam2_encoder(feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    x = feeds["pixel_values"][0]
    mean = np.array([0.485, 0.456, 0.406], np.float32)[:, None, None]
    std = np.array([0.229, 0.224, 0.225], np.float32)[:, None, None]
    rgb = (x * std + mean) * 255.0  # (3, 1024, 1024)
    return [
        rgb[None],  # the fake keeps the picture in the high-res slot
        np.zeros((1, 64, 128, 128), np.float32),
        np.zeros((1, 256, 64, 64), np.float32),
    ]


def _run_sam2_decoder(feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    img = feeds["image_embeddings.0"][0].transpose(1, 2, 0)
    pts = feeds["input_points"].reshape(-1, 2)
    labels = feeds["input_labels"].reshape(-1).astype(np.float32)
    boxes = feeds["input_boxes"].reshape(-1, 4)
    if len(boxes):
        b = boxes[0]
        pts = np.concatenate([pts, [[b[0], b[1]], [b[2], b[3]]]])
        labels = np.concatenate([labels, [2.0, 3.0]])
    seed, box = _prompt(pts, labels)
    big = _flood(img, seed, box)
    low = cv2.resize(big, (256, 256), interpolation=cv2.INTER_NEAREST)
    masks, scores = _stack(_logits(low), 3, 0)
    return [scores[None, None], masks[None, None], np.ones((1, 1, 1), np.float32)]


SIGNATURES = {
    "sam1_encoder_hwc": _sam1_encoder_hwc,
    "sam1_encoder_nchw": _sam1_encoder_nchw,
    "sam1_decoder": _sam1_decoder,
    "efficientsam_encoder": _efficientsam_encoder,
    "efficientsam_decoder": _efficientsam_decoder,
    "sam2_encoder": _sam2_encoder,
    "sam2_decoder": _sam2_decoder,
    "data": lambda: ([], []),
}
RUNNERS: dict[str, Any] = {
    "sam1_encoder_hwc": _run_sam1_encoder,
    "sam1_encoder_nchw": _run_sam1_encoder,
    "sam1_decoder": _run_sam1_decoder,
    "efficientsam_encoder": _run_efficientsam_encoder,
    "efficientsam_decoder": _run_efficientsam_decoder,
    "sam2_encoder": _run_sam2_encoder,
    "sam2_decoder": _run_sam2_decoder,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fake_registry(
    src: Path,
    engines: dict[str, str],
    *,
    url_base: str | None = None,
    corrupt: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Write fake model files to ``src`` and return a registry for them.

    ``engines`` maps an engine id to an adapter family key of ``KINDS``. URLs are ``file://``
    unless ``url_base`` (e.g. a local http server) is given. Names in ``corrupt`` get a
    registry checksum that does not match their bytes.
    """
    entries: dict[str, dict[str, Any]] = {}
    for engine, family in engines.items():
        adapter = "sam1" if family.startswith("sam1") else family
        enc_kind, dec_kind = KINDS[family]
        roles = [("encoder", enc_kind), ("decoder", dec_kind)]
        if adapter == "sam2_hf":
            roles += [("encoder_data", "data"), ("decoder_data", "data")]
        for role, kind in roles:
            name = f"{engine}_{role}"
            filename = f"{name}.onnx"
            data = write_fake_model(src / filename, kind)
            digest = sha(b"tampered" + data) if corrupt and name in corrupt else sha(data)
            url = f"{url_base}/{filename}" if url_base else (src / filename).as_uri()
            entries[name] = {
                "filename": filename,
                "dir": engine,
                "url": url,
                "sha256": digest,
                "size_bytes": len(data),
                "engine": engine,
                "role": role,
                "adapter": adapter,
                "label": f"Fake {engine}",
                "licence": "test-only",
                "source": "tests/fake_onnx.py",
            }
    return entries


def use_registry(path: Path, entries: dict[str, dict[str, Any]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path
