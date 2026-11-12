from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CameraRoi(BaseModel):
    """A crop of the camera picture, in full-frame camera pixels (docs/08 section 2)."""

    model_config = ConfigDict(extra="ignore")

    x: int
    y: int
    w: int
    h: int  # covers [x, x+w) x [y, y+h) (pixel-corner coordinates)
    frame_width: int
    frame_height: int  # camera frame size the crop was made for
    bin: Literal[1, 2] = 1  # average bin x bin pixels after cropping (phone videos only)

    @model_validator(mode="after")
    def _check(self) -> CameraRoi:
        if self.x < 0 or self.y < 0:
            raise ValueError("The crop must start inside the picture.")
        if self.w < 64 or self.h < 64:
            raise ValueError("The crop must be at least 64 x 64 pixels.")
        if self.x + self.w > self.frame_width or self.y + self.h > self.frame_height:
            raise ValueError("The crop must lie inside the camera picture.")
        if self.w % self.bin or self.h % self.bin:
            raise ValueError("The crop size must be a multiple of the bin factor.")
        return self


class CalibSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skip_lsb: int = 2
    settle_ms: int = 300
    discard_frames: int = 5
    average_frames: int = 3
    min_conf: float = Field(default=0.10, gt=0.0, lt=1.0)  # decode confidence threshold
    roi: CameraRoi | None = None  # crop of the camera picture; None = the whole picture


class StrictCameraRoi(CameraRoi):
    """CameraRoi for API request bodies: unknown fields are rejected (files on disk ignore them)."""

    model_config = ConfigDict(extra="forbid")


class StrictCalibSettings(CalibSettings):
    """CalibSettings for API request bodies: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    roi: StrictCameraRoi | None = None


class CalibQuality(BaseModel):
    model_config = ConfigDict(extra="ignore")

    coverage: float
    planarity: float
    median_contrast: float
    lit_fraction: float
    px_per_proj_px: float | None = None
    snr: float | None = None
    verdict: Literal["good", "marginal", "too_coarse"] | None = None


class Calibration(BaseModel):
    model_config = ConfigDict(extra="ignore")

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

    def to_file_json(self) -> str:
        """JSON for calibration.json: new optional fields that are None are left out, so the
        previous release (which does not know them) reads the file unchanged."""
        exclude: dict[str, Any] = {}
        if self.strategy is None:
            exclude["strategy"] = True
        if self.settings is not None and self.settings.roi is None:
            exclude["settings"] = {"roi"}
        if self.quality is not None:
            omit = {
                name
                for name in ("px_per_proj_px", "snr", "verdict")
                if getattr(self.quality, name) is None
            }
            if omit:
                exclude["quality"] = omit
        return self.model_dump_json(indent=2, exclude=exclude or None)
