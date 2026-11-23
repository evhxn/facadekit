from __future__ import annotations

import cv2
import numpy as np

from facadekit.devices.base import Camera


class ShiftedCamera:
    """Moves every picture of another camera by (dx, dy) camera px: a camera that was "bumped"
    in software. Used only by the verification self-test (docs/13 "Self-test")."""

    def __init__(self, camera: Camera, shift: tuple[float, float]) -> None:
        self._camera = camera
        self._m = np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]]])

    @property
    def resolution(self) -> tuple[int, int]:
        return self._camera.resolution

    def open(self) -> None:
        self._camera.open()

    def close(self) -> None:
        self._camera.close()

    def read(self) -> np.ndarray:
        frame = self._camera.read()
        h, w = frame.shape[:2]
        out = cv2.warpAffine(
            frame, self._m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        return np.asarray(out)

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
