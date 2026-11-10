from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from facadekit.api.deps import AppState, get_state
from facadekit.models.jobs import Job

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]


@router.get("/jobs/{jid}")
def get_job(jid: str, state: State) -> Job:
    return state.jobs.get(jid)


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str, state: State) -> Job:
    return state.jobs.cancel(jid)
