"""Evaluation harness for Magic select engines (spike S-6, docs/16).

Given a folder of pictures with ground-truth masks (``NAME.png`` + ``NAME_mask.png``, mask
white = object), simulate a user: click the middle of the object, then keep clicking the
middle of the biggest error (include on a missed part, exclude on a wrong part), as in the
usual interactive-segmentation protocol (NoC). Also try a box prompt (the object's bounding
box, padded a little) with and without one click. IoU is measured on the outline the user
would get (the returned polygon), not on the raw mask.

    python -m facadekit.ai.segment_eval generate DIR [--count 24] [--seed 0] [--sim]
    python -m facadekit.ai.segment_eval run DIR [--engines grabcut,efficientsam_ti]
                                                [--clicks 5] [--target 0.85] [--json OUT]

``generate`` writes synthetic shelves: small light objects (the "small white camera") on
cluttered backgrounds, touching similar-coloured clutter; ``--sim`` adds the simulator
scene's features. Engines other than ``grabcut`` must be installed (nothing is downloaded).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from facadekit.ai.segment import Segmenter, get_segmenter
from facadekit.errors import FkError

PromptPoint = tuple[float, float, int]
BOX_PAD_PX = 4


@dataclass
class Sample:
    name: str
    image: np.ndarray  # BGR uint8
    mask: np.ndarray  # bool


@dataclass
class SampleResult:
    name: str
    ious: list[float]  # IoU after click 1..N
    noc: int  # clicks to reach the target (max_clicks + 1 when never reached)
    box_iou: float
    box_click_iou: float
    encode_ms: float
    decode_ms: float


@dataclass
class EngineReport:
    engine: str
    samples: list[SampleResult] = field(default_factory=list)
    target: float = 0.85
    max_clicks: int = 5

    def summary(self) -> dict[str, float | int | str]:
        n = len(self.samples)
        if n == 0:
            return {"engine": self.engine, "samples": 0}

        def mean(values: Sequence[float]) -> float:
            return float(np.mean(values)) if values else 0.0

        def at(k: int) -> float:
            return mean([s.ious[min(k, len(s.ious)) - 1] for s in self.samples])

        reached = [s for s in self.samples if s.noc <= self.max_clicks]
        row: dict[str, float | int | str] = {"engine": self.engine, "samples": n}
        for k in sorted({1, 3, self.max_clicks}):
            if k <= self.max_clicks:
                row[f"iou_{k}"] = round(at(k), 4)
        return {
            **row,
            f"noc_{round(self.target * 100)}": round(mean([s.noc for s in self.samples]), 3),
            "reached_pct": round(100.0 * len(reached) / n, 1),
            "box_iou": round(mean([s.box_iou for s in self.samples]), 4),
            "box_click_iou": round(mean([s.box_click_iou for s in self.samples]), 4),
            "encode_ms": round(mean([s.encode_ms for s in self.samples]), 1),
            "decode_ms": round(mean([s.decode_ms for s in self.samples]), 1),
        }


# --- data ------------------------------------------------------------------------------------


def load_set(folder: Path) -> list[Sample]:
    samples: list[Sample] = []
    for mask_path in sorted(Path(folder).glob("*_mask.png")):
        stem = mask_path.name[: -len("_mask.png")]
        image_path = next(
            (
                p
                for ext in (".png", ".jpg", ".jpeg")
                if (p := mask_path.with_name(stem + ext)).is_file()
            ),
            None,
        )
        if image_path is None:
            continue
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if image is None or mask is None or mask.shape != image.shape[:2] or not (mask > 127).any():
            continue
        samples.append(Sample(stem, image, mask > 127))
    return samples


def _rounded_rect(
    img: np.ndarray, x0: int, y0: int, x1: int, y1: int, r: int, colour: tuple[int, ...]
) -> None:
    r = max(0, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
    cv2.rectangle(img, (x0 + r, y0), (x1 - r, y1), colour, -1)
    cv2.rectangle(img, (x0, y0 + r), (x1, y1 - r), colour, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(img, (cx, cy), r, colour, -1)


def synthetic_sample(rng: np.random.Generator, size: tuple[int, int] = (960, 540)) -> Sample:
    """One cluttered shelf with a small light object touching similar-coloured clutter."""
    w, h = size
    base = rng.integers(40, 120, 3).astype(np.float32)
    grad = np.linspace(0.8, 1.2, h, dtype=np.float32)[:, None, None]
    img = np.clip(base[None, None, :] * grad * np.ones((h, w, 1), np.float32), 0, 255)
    img = img.astype(np.uint8)
    for _ in range(int(rng.integers(25, 60))):  # background clutter
        x, y = int(rng.integers(0, w - 20)), int(rng.integers(0, h - 20))
        cw, ch = int(rng.integers(12, 120)), int(rng.integers(12, 90))
        colour = tuple(int(c) for c in rng.integers(15, 235, 3))
        if rng.random() < 0.3:
            cv2.circle(img, (x, y), max(6, cw // 3), colour, -1)
        else:
            _rounded_rect(
                img, x, y, min(w - 1, x + cw), min(h - 1, y + ch), int(rng.integers(0, 10)), colour
            )
    shelf_y = int(rng.integers(h // 3, 2 * h // 3))
    cv2.rectangle(
        img, (0, shelf_y), (w - 1, shelf_y + int(rng.integers(12, 30))), (190, 185, 175), -1
    )

    ow, oh = int(rng.integers(14, 48)), int(rng.integers(20, 70))
    ox = int(rng.integers(ow + 60, w - ow - 60))
    oy1 = shelf_y
    oy0 = oy1 - oh
    light = int(rng.integers(225, 252))
    colour = (light, light, min(255, light + int(rng.integers(-6, 6))))
    # similar-coloured neighbours touching the object (the case colour-only methods merge)
    for side in (-1, 1):
        if rng.random() < 0.8:
            nw, nh = int(rng.integers(20, 90)), int(rng.integers(25, 100))
            nx0 = ox - nw if side < 0 else ox + ow
            g = int(rng.integers(205, 240))
            _rounded_rect(img, nx0, shelf_y - nh, nx0 + nw, shelf_y, 4, (g, g, g))
    gt = np.zeros((h, w), np.uint8)
    _rounded_rect(gt, ox, oy0, ox + ow, oy1, max(2, ow // 5), (1,))
    img[gt > 0] = colour
    if rng.random() < 0.7:  # a dark lens or logo inside the object
        cv2.circle(img, (ox + ow // 2, oy0 + oh // 3), max(2, ow // 5), (30, 30, 35), -1)
    noise = rng.normal(0, 3.0, img.shape)
    img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return Sample("", img, gt > 0)


def sim_samples() -> list[Sample]:
    """The simulator's projector view, one sample per scene feature."""
    from facadekit.sim.scene import default_scene

    scene = default_scene()
    w, h = scene.proj_size
    img = cv2.warpPerspective(scene.albedo(), np.linalg.inv(scene.h_p2w), (w, h))
    out: list[Sample] = []
    for name in scene.features_world:
        mask = np.zeros((h, w), np.uint8)
        quad = np.round(np.array(scene.feature_quad_proj(name))).astype(np.int32)
        cv2.fillPoly(mask, [quad], 1)
        out.append(Sample(f"sim_{name}", img, mask > 0))
    return out


def generate(
    folder: Path,
    count: int = 24,
    seed: int = 0,
    sim: bool = False,
    size: tuple[int, int] = (960, 540),
) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    samples = []
    for i in range(count):
        sample = synthetic_sample(rng, size)
        sample.name = f"shelf_{i:03d}"
        samples.append(sample)
    if sim:
        samples += sim_samples()
    written: list[Path] = []
    for sample in samples:
        path = folder / f"{sample.name}.png"
        cv2.imwrite(str(path), sample.image)
        cv2.imwrite(str(folder / f"{sample.name}_mask.png"), sample.mask.astype(np.uint8) * 255)
        written.append(path)
    return written


# --- the simulated user ------------------------------------------------------------------------


def interior_point(mask: np.ndarray) -> tuple[float, float]:
    """The point deepest inside ``mask`` (largest distance to its border)."""
    padded = np.pad(mask.astype(np.uint8), 1)
    dist = cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1]
    y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return float(x), float(y)


def next_click(pred: np.ndarray, gt: np.ndarray) -> PromptPoint:
    """Click the middle of the largest error: include if missed, exclude if wrongly taken."""
    fn = gt & ~pred
    fp = pred & ~gt

    def depth(err: np.ndarray) -> tuple[float, tuple[float, float]]:
        if not err.any():
            return 0.0, (0.0, 0.0)
        padded = np.pad(err.astype(np.uint8), 1)
        dist = cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1]
        y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
        return float(dist[y, x]), (float(x), float(y))

    d_fn, p_fn = depth(fn)
    d_fp, p_fp = depth(fp)
    if d_fn >= d_fp:
        return (p_fn[0], p_fn[1], 1)
    return (p_fp[0], p_fp[1], 0)


def polygon_mask(outer: Sequence[tuple[float, float]], shape: tuple[int, ...]) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    if len(outer) >= 3:
        cv2.fillPoly(mask, [np.round(np.array(outer)).astype(np.int32)], 1)
    return mask > 0


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int((a | b).sum())
    return float((a & b).sum() / union) if union else 0.0


def gt_box(mask: np.ndarray, pad: int = BOX_PAD_PX) -> tuple[float, float, float, float]:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    return (
        float(max(0, xs.min() - pad)),
        float(max(0, ys.min() - pad)),
        float(min(w - 1, xs.max() + pad)),
        float(min(h - 1, ys.max() + pad)),
    )


def _try(
    seg: Segmenter,
    points: list[PromptPoint],
    box: tuple[float, float, float, float] | None,
    shape: tuple[int, ...],
) -> tuple[np.ndarray, float]:
    start = time.perf_counter()
    try:
        result = seg.segment(points, box) if box is not None else seg.segment(points)
        pred = polygon_mask(result.outer, shape)
    except FkError:
        pred = np.zeros(shape[:2], bool)  # "Nothing was selected" counts as IoU 0
    return pred, (time.perf_counter() - start) * 1000.0


def evaluate_sample(
    seg: Segmenter, sample: Sample, *, max_clicks: int = 5, target: float = 0.85
) -> SampleResult:
    start = time.perf_counter()
    seg.set_image(sample.image)
    encode_ms = (time.perf_counter() - start) * 1000.0
    gt = sample.mask
    cx, cy = interior_point(gt)
    points: list[PromptPoint] = [(cx, cy, 1)]
    ious: list[float] = []
    decode: list[float] = []
    noc = max_clicks + 1
    for k in range(1, max_clicks + 1):
        pred, ms = _try(seg, points, None, sample.image.shape)
        decode.append(ms)
        score = iou(pred, gt)
        ious.append(score)
        if score >= target and noc > max_clicks:
            noc = k
        if k < max_clicks:
            points.append(next_click(pred, gt))
    box = gt_box(gt)
    box_pred, ms_box = _try(seg, [], box, sample.image.shape)
    both_pred, ms_both = _try(seg, [(cx, cy, 1)], box, sample.image.shape)
    decode += [ms_box, ms_both]
    return SampleResult(
        name=sample.name,
        ious=[round(v, 4) for v in ious],
        noc=noc,
        box_iou=round(iou(box_pred, gt), 4),
        box_click_iou=round(iou(both_pred, gt), 4),
        encode_ms=round(encode_ms, 1),
        decode_ms=round(float(np.mean(decode)), 1),
    )


def evaluate(
    engine: str,
    samples: Sequence[Sample],
    *,
    max_clicks: int = 5,
    target: float = 0.85,
    factory: Callable[[str], Segmenter] = get_segmenter,
) -> EngineReport:
    report = EngineReport(engine=engine, target=target, max_clicks=max_clicks)
    seg = factory(engine)
    actual = str(getattr(seg, "engine", "grabcut"))
    if actual != engine:
        raise FkError("AI_UNAVAILABLE", f"{engine} is not available (got {actual})")
    for sample in samples:
        report.samples.append(evaluate_sample(seg, sample, max_clicks=max_clicks, target=target))
    return report


def format_table(reports: Sequence[EngineReport]) -> str:
    rows = [r.summary() for r in reports]
    if not rows:
        return "(no engines)"
    keys = list(rows[0])
    widths = {k: max(len(k), *(len(str(r.get(k, ""))) for r in rows)) for k in keys}
    line = "  ".join(k.ljust(widths[k]) for k in keys)
    body = ["  ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys) for r in rows]
    return "\n".join([line, "-" * len(line), *body])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m facadekit.ai.segment_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate", help="write a synthetic set (small objects, clutter)")
    gen.add_argument("folder", type=Path)
    gen.add_argument("--count", type=int, default=24)
    gen.add_argument("--seed", type=int, default=0)
    gen.add_argument(
        "--size", default="960x540", help="picture size WxH; 1920x1080 is a projector view"
    )
    gen.add_argument("--sim", action="store_true", help="also add the simulator scene features")
    run = sub.add_parser("run", help="report IoU and clicks per engine")
    run.add_argument("folder", type=Path)
    run.add_argument("--engines", default="grabcut")
    run.add_argument("--clicks", type=int, default=5)
    run.add_argument("--target", type=float, default=0.85)
    run.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        w, h = (int(v) for v in args.size.lower().split("x"))
        written = generate(args.folder, count=args.count, seed=args.seed, sim=args.sim, size=(w, h))
        print(f"Wrote {len(written)} pictures with masks to {args.folder}")
        return 0

    samples = load_set(args.folder)
    if not samples:
        print(f"No NAME.png + NAME_mask.png pairs in {args.folder}", file=sys.stderr)
        return 2
    reports: list[EngineReport] = []
    for engine in [e.strip() for e in args.engines.split(",") if e.strip()]:
        try:
            reports.append(evaluate(engine, samples, max_clicks=args.clicks, target=args.target))
        except FkError as exc:
            print(f"{engine}: {exc.message}", file=sys.stderr)
            return 2
    print(f"{len(samples)} pictures, target IoU {args.target:.2f}, up to {args.clicks} clicks")
    print(format_table(reports))
    if args.json is not None:
        data = {
            "target": args.target,
            "max_clicks": args.clicks,
            "summary": [r.summary() for r in reports],
            "samples": {r.engine: [asdict(s) for s in r.samples] for r in reports},
        }
        args.json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EngineReport",
    "Sample",
    "SampleResult",
    "evaluate",
    "evaluate_sample",
    "format_table",
    "generate",
    "gt_box",
    "interior_point",
    "iou",
    "load_set",
    "main",
    "next_click",
    "sim_samples",
    "synthetic_sample",
]
