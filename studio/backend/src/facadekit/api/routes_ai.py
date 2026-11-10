from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from facadekit.ai.beats import analyze_beats
from facadekit.api.deps import AppState, get_state
from facadekit.errors import BAD_REQUEST, NOT_FOUND, REVISION_CONFLICT, FkError

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


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
