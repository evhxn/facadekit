from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Request

from facadekit.api.ws import EventHub, ProjectorHub
from facadekit.jobs.runner import JobRunner
from facadekit.store.project_store import ProjectStore

if TYPE_CHECKING:
    from facadekit.api.routes_devices import CameraManager
    from facadekit.sim.devices import SimDisplay
    from facadekit.sim.scene import SimScene


@dataclass
class AppState:
    store: ProjectStore
    jobs: JobRunner
    events: EventHub
    projector: ProjectorHub
    cameras: CameraManager
    sim: bool = False
    sim_scene: SimScene | None = None
    sim_display: SimDisplay | None = None


def get_state(request: Request) -> AppState:
    return cast(AppState, request.app.state.fk)
