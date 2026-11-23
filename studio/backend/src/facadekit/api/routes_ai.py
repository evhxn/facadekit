from __future__ import annotations

import importlib.util
import threading
from typing import Annotated, Any, Literal

import cv2
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from facadekit.ai import engines
from facadekit.ai.beats import analyze_beats
from facadekit.ai.models import ensure_model
from facadekit.ai.segment import engine_of, get_segmenter, normalise_box
from facadekit.api.deps import AppState, get_state
from facadekit.errors import (
    AI_UNAVAILABLE,
    BAD_REQUEST,
    NOT_FOUND,
    REVISION_CONFLICT,
    FkError,
)

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


def _importable(module: str) -> bool:
    # find_spec only locates the module; it does not import heavy dependencies.
    return importlib.util.find_spec(module) is not None


@router.get("/ai/capabilities")
def ai_capabilities() -> dict[str, Any]:
    # Model engines are listed when published and onnxruntime is importable, whether or not
    # their files are downloaded yet; /ai/segment/engines tells which are installed.
    usable = engines.usable_engines()
    return {"segment_engines": [*usable, engines.GRABCUT], "beats": _importable("librosa")}


@router.get("/ai/segment/engines")
def segment_engines() -> dict[str, Any]:
    """Which selector Magic select uses now, and what each model needs to download."""
    return {
        "active": engines.auto_engine(),
        "onnxruntime": engines.onnxruntime_available(),
        "engines": [engines.engine_status(e) for e in engines.list_engines()],
    }


class DownloadRequest(BaseModel):
    # The user saw the size and the source and said yes; the UI sends both back so a stale
    # confirmation (the registry changed in between) is refused instead of fetching more.
    confirm: bool
    expected_bytes: int = Field(ge=0)


_download_lock = threading.Lock()


@router.post("/ai/models/{engine_id}/download", status_code=202)
def download_engine(engine_id: str, body: DownloadRequest, state: State) -> dict[str, str]:
    if not body.confirm:
        raise FkError(BAD_REQUEST, "The download was not confirmed")
    info = engines.get_engine(engine_id)
    if info is None:
        raise FkError(NOT_FOUND, f"Unknown segmentation engine: {engine_id}")
    if not info.published:
        raise FkError(AI_UNAVAILABLE, f"{info.label} is not published yet.")
    if not engines.onnxruntime_available():
        raise FkError(AI_UNAVAILABLE, "Smart select needs onnxruntime (pip install facadekit[ai]).")
    if body.expected_bytes != info.download_bytes:
        raise FkError(BAD_REQUEST, "The download size changed. Look at it again and confirm.")
    names = [f.name for f in info.files if not f.installed]
    sizes = {f.name: max(f.size_bytes, 1) for f in info.files}
    total = sum(sizes[n] for n in names) or 1

    def run(progress: Any, cancel: Any) -> dict[str, Any]:
        done = 0
        with _download_lock:
            for name in names:
                if cancel():
                    break
                share = sizes[name] / total

                def report(frac: float, _msg: str, base: int = done, share: float = share) -> None:
                    progress(min(1.0, base / total + frac * share), f"Downloading {info.label}")

                ensure_model(name, progress=report)
                done += sizes[name]
        with _segment_lock:
            state.segmenters.clear()  # "auto" may pick the new model now
        return {"engine": info.id, "files": len(names)}

    job = state.jobs.submit("download", run)
    return {"job_id": job.id}


@router.post("/projects/{pid}/assets/{aid}/analyze", status_code=202)
def analyze_asset(pid: str, aid: str, state: State) -> dict[str, str]:
    project_dir = state.store.project_dir(pid)
    project = state.store.load(pid)
    asset = next((a for a in project.assets if a.id == aid), None)
    if asset is None:
        raise FkError(NOT_FOUND, f"asset {aid!r} not found")
    if not (asset.kind == "audio" or (asset.kind == "video" and asset.has_audio)):
        raise FkError(BAD_REQUEST, "asset has no audio to analyse")
    source = project_dir / asset.filename

    def run(progress: Any, cancel: Any) -> dict[str, Any]:
        found = analyze_beats(source, progress)
        for attempt in range(2):
            current = state.store.load(pid)
            kept = [m for m in current.markers if m.kind == "user"]
            current.markers = sorted([*kept, *found], key=lambda m: m.t_ms)
            try:
                saved = state.store.save(current)
            except FkError as exc:
                if exc.code == REVISION_CONFLICT and attempt == 0:
                    continue
                raise
            state.events.publish({"type": "project", "project_id": pid, "revision": saved.revision})
            break
        return {"markers": len(found)}

    job = state.jobs.submit("analyze", run)
    return {"job_id": job.id}


class SegmentPoint(BaseModel):
    x: float
    y: float
    label: Literal[0, 1]


class SegmentBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class SegmentRequest(BaseModel):
    points: list[SegmentPoint] = Field(default_factory=list)
    # "auto", "grabcut", or a model engine id from /ai/segment/engines (e.g. "mobilesam").
    prefer: str = Field(default="auto", pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    # Optional box prompt (projector px). Models take it as a box; GrabCut as its init mask.
    box: SegmentBox | None = None


_segment_lock = threading.Lock()
# Each cached segmenter holds a full projector-view image and, for MobileSAM, its embedding.
MAX_CACHED_SEGMENTERS = 2


@router.post("/projects/{pid}/segment")
def segment(pid: str, body: SegmentRequest, state: State) -> dict[str, Any]:
    project = state.store.load(pid)
    if project.calibration_id is None:
        raise FkError(BAD_REQUEST, "The project has no calibration")
    if body.box is None and not any(p.label == 1 for p in body.points):
        raise FkError(BAD_REQUEST, "At least one include point is required")
    cid = project.calibration_id
    image_path = state.store.project_dir(pid) / "calibration" / cid / "proj_view.png"
    key = (pid, cid, body.prefer)
    points: list[tuple[float, float, int]] = [(p.x, p.y, p.label) for p in body.points]
    raw_box = None if body.box is None else (body.box.x0, body.box.y0, body.box.x1, body.box.y1)
    with _segment_lock:
        segmenter = state.segmenters.get(key)
        if segmenter is None:
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image is None:
                raise FkError(NOT_FOUND, "proj_view.png not found for the calibration")
            segmenter = get_segmenter(body.prefer)
            segmenter.set_image(image)
            state.segmenters[key] = segmenter
            while len(state.segmenters) > MAX_CACHED_SEGMENTERS:  # oldest first
                del state.segmenters[next(iter(state.segmenters))]
        box = normalise_box(raw_box, getattr(segmenter, "image_size", None))
        result = segmenter.segment(points, box) if box is not None else segmenter.segment(points)
    return {
        "outer": result.outer,
        "quad": result.quad,
        "score": result.score,
        "engine": engine_of(segmenter),
        "engine_label": str(getattr(segmenter, "label", "Simple selector (GrabCut)")),
    }
