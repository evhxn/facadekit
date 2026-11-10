from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from facadekit.fpp.fake import create_fake_fpp


@dataclass
class FakeFpp:
    host: str
    state_dir: Path
    app: FastAPI


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def fake_fpp(tmp_path: Path) -> Iterator[FakeFpp]:
    """Runs the fake FPP on 127.0.0.1:<free port> in a background thread.

    Yields an object with .host ("127.0.0.1:<port>"), .state_dir (Path), .app (FastAPI).
    """
    state_dir = tmp_path / "fpp"
    app = create_fake_fpp(state_dir)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("fake FPP server did not start")
        time.sleep(0.01)
    try:
        yield FakeFpp(host=f"127.0.0.1:{port}", state_dir=state_dir, app=app)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """2 s, 320x180, 30 fps H.264 + AAC test clip generated with ffmpeg."""
    from facadekit.media.ffmpeg import run_ffmpeg

    out = tmp_path_factory.mktemp("media_video") / "sample.mp4"
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(out),
        ]
    )  # fmt: skip
    return out


@pytest.fixture(scope="session")
def sample_image(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """64x48 PNG with an alpha channel."""
    import cv2
    import numpy as np

    out = tmp_path_factory.mktemp("media_image") / "sample.png"
    img = np.zeros((48, 64, 4), dtype=np.uint8)
    img[:, :, 2] = 200
    img[:, :, 3] = 128
    cv2.imwrite(str(out), img)
    return out


@pytest.fixture(scope="session")
def sample_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """3 s sine WAV generated with ffmpeg."""
    from facadekit.media.ffmpeg import run_ffmpeg

    out = tmp_path_factory.mktemp("media_audio") / "sample.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(out)])
    return out
