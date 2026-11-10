from __future__ import annotations

import cv2
import numpy as np

from facadekit.api.ws import ProjectorHub
from facadekit.errors import BAD_IMAGE_SIZE, PROJECTOR_NOT_CONNECTED, FkError


class WsDisplay:
    """ProjectorDisplay backed by the projector browser page (see ProjectorHub)."""

    def __init__(self, hub: ProjectorHub) -> None:
        self._hub = hub

    @property
    def size(self) -> tuple[int, int]:
        status = self._hub.status()
        if not status.connected or status.width is None or status.height is None:
            raise FkError(PROJECTOR_NOT_CONNECTED, "no projector page is connected")
        return (status.width, status.height)

    def show(self, image: np.ndarray) -> None:
        width, height = self.size
        if image.shape[:2] != (height, width) or image.ndim not in (2, 3):
            raise FkError(
                BAD_IMAGE_SIZE,
                f"image shape {image.shape} does not match projector {width}x{height}",
            )
        ok, buf = cv2.imencode(".png", image)
        if not ok:
            raise FkError(BAD_IMAGE_SIZE, "image could not be encoded as PNG")
        seq = self._hub.put_image(buf.tobytes())
        self._hub.send(
            {"type": "image", "seq": seq, "url": f"/api/v1/projector/image/{seq}.png"},
            wait_ack=True,
        )
        self._hub.set_mode("image")
