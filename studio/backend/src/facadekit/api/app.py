from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from facadekit import __version__, logs
from facadekit.api import (
    origin_guard,
    remote_auth,
    routes_ai,
    routes_assets,
    routes_calibration,
    routes_devices,
    routes_diagnostics,
    routes_fpp,
    routes_jobs,
    routes_learning,
    routes_projector,
    routes_projects,
    routes_render,
)
from facadekit.api import ws as ws_module
from facadekit.api.deps import AppState
from facadekit.api.routes_devices import CameraManager
from facadekit.calib.learn import ScanMemory
from facadekit.calib.learn_align import AlignMemory
from facadekit.config import projects_dir
from facadekit.errors import BAD_REQUEST, INTERNAL, NOT_FOUND, FkError, http_status
from facadekit.jobs.runner import JobRunner
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import SimScene, default_scene
from facadekit.store.project_store import ProjectStore

log = logging.getLogger(__name__)

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
    testing: bool = False,
    sim_scene: SimScene | None = None,
    log_file: Path | None = None,
) -> FastAPI:
    root = data_dir / "projects" if data_dir is not None else projects_dir()
    events = ws_module.EventHub()
    projector = ws_module.ProjectorHub(events, sim_size=(1920, 1080) if sim else None)
    jobs = JobRunner(
        on_update=lambda job: events.publish({"type": "job", "job": job.model_dump(mode="json")})
    )
    sim_scene = (sim_scene or default_scene()) if sim else None  # test hook: choose the scene
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
        scan_memory=ScanMemory(root.parent / "learning" / "scans.jsonl"),
        align_memory=AlignMemory(root.parent / "learning" / "realign.jsonl"),
        log_file=log_file,
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
    guard_hosts = origin_guard.allowed_hostnames(lan=lan, extra=("testserver",) if testing else ())
    app.add_middleware(origin_guard.OriginGuardMiddleware, hostnames=guard_hosts)

    # Outermost, so the id also covers requests the two guards below refuse.
    app.add_middleware(logs.RequestLogMiddleware)

    @app.exception_handler(FkError)
    async def _fk_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, FkError)
        status = http_status(exc.code)
        # Nothing else explains these in the log: say which error it was, and for the ones
        # that are FacadeKit's own fault (INTERNAL) where it came from.
        log.log(
            logging.ERROR if status >= 500 else logging.WARNING,
            "%s %s refused: %s: %s",
            request.method,
            request.url.path,
            exc.code,
            exc.message,
            exc_info=exc if exc.code == INTERNAL else None,
        )
        return _error(status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        errors = exc.errors()
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first["loc"])
            message = f"{location}: {first['msg']}"
        else:
            message = "invalid request"
        log.warning("%s %s refused: %s: %s", request.method, request.url.path, BAD_REQUEST, message)
        return _error(422, BAD_REQUEST, message)

    api = APIRouter(prefix="/api/v1")

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "sim": state.sim}

    api.include_router(routes_projects.router)
    api.include_router(routes_assets.router)
    api.include_router(routes_devices.router)
    api.include_router(routes_diagnostics.router)
    api.include_router(routes_calibration.router)
    api.include_router(routes_learning.router)
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
