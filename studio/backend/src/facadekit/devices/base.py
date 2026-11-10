from __future__ import annotations

from typing import Protocol

import numpy as np


class Camera(Protocol):
    @property
    def resolution(self) -> tuple[int, int]:
        """(width, height); valid after open()."""
        ...

    def open(self) -> None:
        """Raises FkError CAMERA_OPEN_FAILED."""
        ...

    def close(self) -> None: ...

    def read(self) -> np.ndarray:
        """Newest frame, BGR uint8. Raises FkError CAMERA_READ_FAILED."""
        ...

    def set_controls(
        self,
        *,
        auto_exposure: bool | None = None,
        exposure: float | None = None,
        auto_wb: bool | None = None,
        autofocus: bool | None = None,
    ) -> dict[str, bool]:
        """Return {name: applied?} for the arguments that were not None. exposure is 0..1."""
        ...


class ProjectorDisplay(Protocol):
    @property
    def size(self) -> tuple[int, int]:
        """(width, height) physical pixels."""
        ...

    def show(self, image: np.ndarray) -> None:
        """Show a (H, W) gray or (H, W, 3) BGR uint8 image of exactly `size`.

        Returns only after the image is on screen. Raises FkError BAD_IMAGE_SIZE.
        """
        ...
