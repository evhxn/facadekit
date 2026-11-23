"""Results of "Verify surface" (docs/13-surface-verification.md).

Stored per surface in `<project>/verification/<surface_id>.json` (rig-only checks in
`rig.json`), never in project.json, so a check never bumps the project's revision.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from facadekit.models.project import Mask, Point

RigVerdict = Literal["stable", "camera_moved", "projector_moved", "both_moved", "moved", "unknown"]
PartState = Literal["stable", "moved", "unknown"]
SurfaceVerdict = Literal["pass", "warn", "fail"]
Side = Literal["top", "right", "bottom", "left"]


class RigCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: RigVerdict
    camera: PartState
    projector: PartState
    camera_shift_px: float | None = None  # camera px, median over the lit wall
    projector_shift_px: float | None = None  # projector px, median over the dots
    total_shift_px: float | None = None  # projector px, dots against the scan, both moves
    confidence: float = 0.0  # 0..1, see docs/13
    dots_expected: int = 0
    dots_found: int = 0
    texture_matches: int = 0
    texture_method: str | None = None  # "features" | "phase" | None (camera motion unknown)
    drift: list[list[float]] | None = None  # 3x3 old -> new projector px when it moved
    message: str = ""
    recommendation: str = ""


class EdgeReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    side: Side
    spill_px: float | None = None  # projector px; + light past the object's edge, - short of it
    drift_px: float | None = None  # projector px; rig motion at this edge (from the dots), + out
    lit_px: float | None = None  # projector px; measured light edge against the predicted one
    spill_range_px: list[float] | None = None  # 10th and 90th percentile of spill along the side
    samples: int = 0
    advice: str = ""


class SurfaceCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    surface_id: str
    verdict: SurfaceVerdict
    iou_intended: float | None = None  # lit region against the intended region
    iou_object: float | None = None  # lit region against the object
    leakage: float | None = None  # share of the lit area that is not on the object
    uncovered: float | None = None  # share of the object that stays dark
    max_edge_px: float | None = None  # worst |spill| (90th percentile along a side), projector px
    object_confidence: float = 0.0
    parallax_px: float | None = None  # spread of the lit edge's displacement, projector px
    inner_residual_px: float | None = None  # inner dots against the wall-wide motion
    depth_step_px: float | None = None  # how much the scan's map jumps at the outline, camera px
    flatness_warning: bool = False
    outline_measured: float = 1.0  # share of the outline the scan measured
    edges: list[EdgeReport] = Field(default_factory=list)
    suggested_quad: list[Point] | None = None
    suggested_mask: Mask | None = None
    suggestion_note: str = ""
    message: str = ""


class VerificationRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    created_at: str
    calibration_id: str
    camera_id: str
    surface_id: str | None = None
    surface_quad: list[Point] | None = None  # the outline that was checked (to spot edits)
    surface_mask: Mask | None = None
    level: float = 1.0  # projection level used (1.0 = full brightness)
    exposure_locked: bool = False
    shots: int = 0
    duration_s: float = 0.0
    rig: RigCheck
    surface: SurfaceCheck | None = None
    overlay: bool = False  # `<surface_id>.jpg` was written
    injected: dict[str, float] | None = None  # self-test only: the error that was put in
