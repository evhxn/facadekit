from __future__ import annotations

import importlib.util
import threading
from typing import Annotated, Any, Literal

import cv2
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from facadekit.ai.beats import analyze_beats
from facadekit.ai.models import is_published
from facadekit.ai.segment import MobileSamSegmenter, get_segmenter
from facadekit.api.deps import AppState, get_state
from facadekit.errors import BAD_REQUEST, NOT_FOUND, REVISION_CONFLICT, FkError

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


def _importable(module: str) -> bool:
    # find_spec only locates the module; it does not import heavy dependencies.
    return importlib.util.find_spec(module) is not None


@router.get("/ai/capabilities")
def ai_capabilities() -> dict[str, Any]:
    mobilesam_ready = (
        is_published("mobilesam_encoder")
        and is_published("mobilesam_decoder")
        and _importable("onnxruntime")
    )
    engines = ["mobilesam", "grabcut"] if mobilesam_ready else ["grabcut"]
    return {"segment_engines": engines, "beats": _importable("librosa")}


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


class SegmentRequest(BaseModel):
    points: list[SegmentPoint]
    prefer: Literal["auto", "mobilesam", "grabcut"] = "auto"


_segment_lock = threading.Lock()


@router.post("/projects/{pid}/segment")
def segment(pid: str, body: SegmentRequest, state: State) -> dict[str, Any]:
    project = state.store.load(pid)
    if project.calibration_id is None:
        raise FkError(BAD_REQUEST, "The project has no calibration")
    if not any(p.label == 1 for p in body.points):
        raise FkError(BAD_REQUEST, "At least one include point is required")
    cid = project.calibration_id
    image_path = state.store.project_dir(pid) / "calibration" / cid / "proj_view.png"
    key = (pid, cid, body.prefer)
    points: list[tuple[float, float, int]] = [(p.x, p.y, p.label) for p in body.points]
    with _segment_lock:
        segmenter = state.segmenters.get(key)
        if segmenter is None:
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image is None:
                raise FkError(NOT_FOUND, "proj_view.png not found for the calibration")
            segmenter = get_segmenter(body.prefer)
            segmenter.set_image(image)
            state.segmenters[key] = segmenter
        result = segmenter.segment(points)
    engine = "mobilesam" if isinstance(segmenter, MobileSamSegmenter) else "grabcut"
    return {"outer": result.outer, "quad": result.quad, "score": result.score, "engine": engine}
