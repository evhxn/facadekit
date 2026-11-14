from __future__ import annotations

import platform
import sys
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from facadekit import __version__, logs
from facadekit.api.deps import AppState, get_state

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]

LOG_TAIL_LINES = 200


def system_info() -> dict[str, Any]:
    """Versions and platform; no device is opened and nothing personal is read."""
    import cv2
    import numpy as np

    return {
        "facadekit": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "os": platform.platform(),
        "machine": platform.machine(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
    }


@router.get("/diagnostics")
def diagnostics(state: State) -> dict[str, Any]:
    """What a bug report needs: versions, device state, recent jobs and the end of the log.

    Loopback only (it is not in the paired-device route list). The home directory is
    replaced with `~`; project names and file names can still appear in log lines.
    """
    log_file = state.log_file
    return {
        "system": system_info(),
        "sim": state.sim,
        "projector": state.projector.status().model_dump(),
        "jobs": [
            {
                "id": job.id,
                "kind": job.kind,
                "state": job.state,
                "message": job.message,
                "error": job.error.model_dump() if job.error else None,
                "created_at": job.created_at,
            }
            for job in state.jobs.recent()
        ],
        "log_file": logs.redact(str(log_file)) if log_file is not None else None,
        "log_tail": logs.tail(log_file, LOG_TAIL_LINES) if log_file is not None else [],
    }
