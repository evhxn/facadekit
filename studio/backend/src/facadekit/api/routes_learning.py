from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from facadekit.api.deps import AppState, get_state
from facadekit.calib.learn import StrategySummary, Verdict, learning_enabled, summarize
from facadekit.calib.pipeline import load_calibration

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict


class FeedbackResponse(BaseModel):
    recorded: bool  # False when that scan was not chosen by scan memory


@router.post("/projects/{pid}/calibrations/{cid}/feedback")
def calibration_feedback(
    pid: str, cid: str, body: FeedbackRequest, state: State
) -> FeedbackResponse:
    """The user's opinion of a finished scan; it changes which strategy is tried next."""
    load_calibration(state.store.project_dir(pid), cid)  # NOT_FOUND for unknown ids
    if state.scan_memory is None:
        return FeedbackResponse(recorded=False)
    return FeedbackResponse(recorded=state.scan_memory.set_verdict(cid, body.verdict))


class LearningSummary(BaseModel):
    enabled: bool
    scans: int
    strategies: list[StrategySummary]


@router.get("/learning/scans")
def learning_summary(state: State) -> LearningSummary:
    records = state.scan_memory.records() if state.scan_memory is not None else []
    return LearningSummary(
        enabled=state.scan_memory is not None and learning_enabled(),
        scans=len(records),
        strategies=summarize(records),
    )


@router.delete("/learning/scans", status_code=204)
def forget_scans(state: State) -> Response:
    if state.scan_memory is not None:
        state.scan_memory.clear()
    return Response(status_code=204)
