from __future__ import annotations

import cv2
import numpy as np

from facadekit.devices.base import Camera
from facadekit.errors import BAD_REQUEST, FkError
from facadekit.models.calibration import CameraRoi


class CroppedCamera:
    """Shows only the crop of another camera; implements `Camera` (docs/08 section 2)."""

    def __init__(self, camera: Camera, roi: CameraRoi) -> None:
        self._camera = camera
        self._roi = roi

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._roi.w // self._roi.bin, self._roi.h // self._roi.bin)

    def open(self) -> None:
        self._camera.open()
        roi = self._roi
        if tuple(self._camera.resolution) != (roi.frame_width, roi.frame_height):
            self._camera.close()
            raise FkError(
                BAD_REQUEST,
                "The crop was drawn for a different camera picture size. Check the camera again.",
            )

    def close(self) -> None:
        self._camera.close()

    def read(self) -> np.ndarray:
        roi = self._roi
        frame = self._camera.read()[roi.y : roi.y + roi.h, roi.x : roi.x + roi.w]
        if roi.bin == 2:
            frame = cv2.resize(frame, (roi.w // 2, roi.h // 2), interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(frame)

    def set_controls(
        self,
        *,
        auto_exposure: bool | None = None,
        exposure: float | None = None,
        auto_wb: bool | None = None,
        autofocus: bool | None = None,
    ) -> dict[str, bool]:
        return self._camera.set_controls(
            auto_exposure=auto_exposure, exposure=exposure, auto_wb=auto_wb, autofocus=autofocus
        )
