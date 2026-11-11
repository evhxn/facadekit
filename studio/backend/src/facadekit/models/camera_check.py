from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from facadekit.models.calibration import CameraRoi


class CameraCheck(BaseModel):
    """Result of the wizard's camera check (docs/08 sections 3 and 7). All boxes are full-frame."""

    verdict: Literal["good", "marginal", "too_coarse"]
    reasons: list[str]  # Reason values from calib/quality.py, in rule order
    camera_width: int
    camera_height: int  # full frame
    lit_box: CameraRoi | None  # tight box of the projection (no margin); None if not found
    suggested_roi: CameraRoi | None  # quality.suggest_roi(...)
    px_per_proj_px: float
    proj_px_per_cam_px: float  # 1 / px_per_proj_px, rounded to 1 decimal (for the UI sentence)
    stripe_modulation: dict[str, float]  # {"2": .., "3": .., "4": ..}
    snr: float
    median_contrast: float
    controls: dict[str, bool]
    # Owner override of T-204: 1080p stays opt-in. True when the picture is below 1920x1080 and
    # the verdict is not "good": the UI may tell the user to set FACADEKIT_CAMERA_SIZE=1920x1080.
    suggest_1080p: bool = False
