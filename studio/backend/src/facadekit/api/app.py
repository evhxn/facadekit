from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from facadekit import __version__
from facadekit.api import (
    remote_auth,
    routes_ai,
    routes_assets,
    routes_calibration,
    routes_devices,
    routes_fpp,
    routes_jobs,
    routes_projector,
    routes_projects,
    routes_render,
)
from facadekit.api import ws as ws_module
from facadekit.api.deps import AppState
from facadekit.api.routes_devices import CameraManager
from facadekit.config import projects_dir
from facadekit.errors import BAD_REQUEST, NOT_FOUND, FkError, http_status
from facadekit.jobs.runner import JobRunner
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import default_scene
from facadekit.store.project_store import ProjectStore

WEBDIST = Path(__file__).resolve().parent.parent / "webdist"
NO_UI_MESSAGE = (
    "FacadeKit backend is running. The web UI has not been built (run `make build-web`)."
)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(
    *,
    data_dir: Path | None = None,
    sim: bool = False,
    lan: bool = False,
    pairing_code: str | None = None,
    client_addr_override: Callable[[], str] | None = None,
) -> FastAPI:
    root = data_dir / "projects" if data_dir is not None else projects_dir()
    events = ws_module.EventHub()
    projector = ws_module.ProjectorHub(events, sim_size=(1920, 1080) if sim else None)
    jobs = JobRunner(
        on_update=lambda job: events.publish({"type": "job", "job": job.model_dump(mode="json")})
    )
    sim_scene = default_scene() if sim else None
    sim_display = SimDisplay(sim_scene) if sim_scene is not None else None
    sim_camera_factory = (
        (lambda: SimCamera(sim_scene, sim_display))
        if sim_scene is not None and sim_display is not None
        else None
    )
    state = AppState(
        store=ProjectStore(root),
        jobs=jobs,
        events=events,
        projector=projector,
        cameras=CameraManager(sim_camera_factory=sim_camera_factory),
        sim=sim,
        sim_scene=sim_scene,
        sim_display=sim_display,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        events.bind_loop(loop)
        projector.bind_loop(loop)
        try:
            yield
        finally:
            state.jobs.shutdown()
            state.cameras.close()

    app = FastAPI(title="FacadeKit Studio", lifespan=lifespan)
    app.state.fk = state
    app.state.remote = remote_auth.RemoteAuth(
        lan=lan,
        code=pairing_code,
        client_addr_override=client_addr_override,
        on_pair=lambda host: print(f"Device paired: {host}", flush=True),
    )
    app.add_middleware(remote_auth.RemoteAccessMiddleware, auth=app.state.remote)

    @app.exception_handler(FkError)
    async def _fk_error(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, FkError)
        return _error(http_status(exc.code), exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        errors = exc.errors()
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first["loc"])
            message = f"{location}: {first['msg']}"
        else:
            message = "invalid request"
        return _error(422, BAD_REQUEST, message)

    api = APIRouter(prefix="/api/v1")

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "sim": state.sim}

    api.include_router(routes_projects.router)
    api.include_router(routes_assets.router)
    api.include_router(routes_devices.router)
    api.include_router(routes_calibration.router)
    api.include_router(routes_jobs.router)
    api.include_router(routes_render.router)
    api.include_router(routes_fpp.router)
    api.include_router(routes_ai.router)
    api.include_router(routes_projector.router)
    api.include_router(remote_auth.router)
    app.include_router(api)
    app.include_router(ws_module.router)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:
        if full_path in ("api", "ws") or full_path.startswith(("api/", "ws/")):
            return _error(404, NOT_FOUND, f"no such endpoint: /{full_path}")
        index = WEBDIST / "index.html"
        if not index.is_file():
            return PlainTextResponse(NO_UI_MESSAGE)
        if full_path:
            root_dir = WEBDIST.resolve()
            candidate = (root_dir / full_path).resolve()
            if candidate.is_relative_to(root_dir) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)

    return app
