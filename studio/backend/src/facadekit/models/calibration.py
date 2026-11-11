from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CalibSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip_lsb: int = 2
    settle_ms: int = 300
    discard_frames: int = 5
    average_frames: int = 3
    min_conf: float = Field(default=0.10, gt=0.0, lt=1.0)  # decode confidence threshold


class CalibQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage: float
    planarity: float
    median_contrast: float
    lit_fraction: float


class Calibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str
    mode: Literal["scan", "manual"]
    projector_width: int
    projector_height: int
    camera_id: str | None
    camera_width: int | None
    camera_height: int | None
    settings: CalibSettings | None
    quality: CalibQuality | None
    h_c2p: list[list[float]] | None
    warnings: list[str] = Field(default_factory=list)
    strategy: str | None = None  # scan strategy chosen by scan memory (calib/learn.py)
