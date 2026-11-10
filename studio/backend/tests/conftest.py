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
