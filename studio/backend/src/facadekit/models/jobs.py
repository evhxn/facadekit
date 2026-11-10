from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

JobKind = Literal["calibration", "render", "publish", "analyze"]
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: JobKind
    state: JobState
    progress: float = 0.0
    message: str = ""
    error: JobError | None = None
    result: dict[str, Any] | None = None
    created_at: str
