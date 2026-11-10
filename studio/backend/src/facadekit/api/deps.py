from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastapi import Request

from facadekit.api.ws import EventHub, ProjectorHub
from facadekit.jobs.runner import JobRunner
from facadekit.store.project_store import ProjectStore


@dataclass
class AppState:
    store: ProjectStore
    jobs: JobRunner
    events: EventHub
    projector: ProjectorHub
    sim: bool = False
    # later tickets add: cameras


def get_state(request: Request) -> AppState:
    return cast(AppState, request.app.state.fk)
