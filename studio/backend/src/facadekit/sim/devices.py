from __future__ import annotations

import cv2
import numpy as np

from facadekit import errors
from facadekit.errors import FkError
from facadekit.sim.scene import SimScene


class SimDisplay:
    def __init__(self, scene: SimScene) -> None:
        self._scene = scene
        self.current: np.ndarray | None = None

    @property
    def size(self) -> tuple[int, int]:
        return self._scene.proj_size

    def show(self, image: np.ndarray) -> None:
        w, h = self.size
        ok = image.dtype == np.uint8 and (image.shape == (h, w) or image.shape == (h, w, 3))
        if not ok:
            raise FkError(
                errors.BAD_IMAGE_SIZE,
                f"expected uint8 ({h}, {w}) or ({h}, {w}, 3), got {image.dtype} {image.shape}",
            )
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        self.current = np.array(image, copy=True)


class SimCamera:
    def __init__(self, scene: SimScene, display: SimDisplay) -> None:
        self._scene = scene
        self._display = display
        self._rng = np.random.default_rng(scene.seed)
        self._open = False

    @property
    def resolution(self) -> tuple[int, int]:
        return self._scene.cam_size

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def read(self) -> np.ndarray:
        if not self._open:
            raise FkError(errors.CAMERA_READ_FAILED, "camera is not open")
        return self._scene.render_camera(self._display.current, self._rng)

    def set_controls(
        self,
        *,
        auto_exposure: bool | None = None,
        exposure: float | None = None,
        auto_wb: bool | None = None,
        autofocus: bool | None = None,
    ) -> dict[str, bool]:
        given = {
            "auto_exposure": auto_exposure,
            "exposure": exposure,
            "auto_wb": auto_wb,
            "autofocus": autofocus,
        }
        return {k: True for k, v in given.items() if v is not None}
