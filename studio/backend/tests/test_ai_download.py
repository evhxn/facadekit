"""Confirmed model download (against a local HTTP server), engine status, and the segment
route with box prompts and a stub model, end to end (spike S-6)."""

from __future__ import annotations

import importlib.util
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.ai import models, segment
from facadekit.ai.models import ensure_model
from facadekit.api.app import create_app
from facadekit.errors import MODEL_DOWNLOAD_FAILED, FkError
from facadekit.sim.scene import default_scene
from tests import fake_onnx
from tests.fake_onnx import fake_registry, fake_session, use_registry

API = "/api/v1"
_real_find_spec = importlib.util.find_spec


class FakeModelHost:
    """A local HTTP server for the fake model files; counts every request."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.requests: list[str] = []
        host = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                host.requests.append(self.path)
                path = host.root / self.path.lstrip("/")
                if not path.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def host(tmp_path: Path) -> Iterator[FakeModelHost]:
    server = FakeModelHost(tmp_path / "src")
    yield server
    server.close()


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACADEKIT_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("FACADEKIT_MODEL_REGISTRY", raising=False)
    monkeypatch.setattr(segment, "_session_factory", fake_session)

    def find_spec(name: str, package: str | None = None) -> Any:
        if name == "onnxruntime":
            return object()
        return _real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    fake_onnx.SESSIONS.clear()


def _served_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: FakeModelHost,
    corrupt: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    entries = fake_registry(
        tmp_path / "src", {"smart": "efficientsam"}, url_base=host.url, corrupt=corrupt
    )
    monkeypatch.setenv(
        "FACADEKIT_MODEL_REGISTRY", str(use_registry(tmp_path / "registry.json", entries))
    )
    return entries


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while True:
        job: dict[str, Any] = client.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.fixture
def sim_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(data_dir=tmp_path / "data", sim=True)) as c:
        yield c


# --- ensure_model over plain http: loopback test hosts only -----------------------------------


def test_loopback_http_needs_a_registry_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: FakeModelHost
) -> None:
    entries = _served_registry(tmp_path, monkeypatch, host)
    path = ensure_model("smart_encoder")
    assert path.read_bytes() == (tmp_path / "src" / "smart_encoder.onnx").read_bytes()
    assert path.parent.name == "smart"  # the registry "dir" keeps an engine's files together

    # The same URL from the bundled registry (no override) is refused without a request.
    monkeypatch.delenv("FACADEKIT_MODEL_REGISTRY")
    monkeypatch.setattr(models, "registry", lambda: entries)
    before = len(host.requests)
    with pytest.raises(FkError) as info:
        ensure_model("smart_decoder")
    assert info.value.code == MODEL_DOWNLOAD_FAILED
    assert len(host.requests) == before


# --- engine status and the confirmed download ------------------------------------------------


def test_status_before_and_after_a_confirmed_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: FakeModelHost, sim_client: TestClient
) -> None:
    entries = _served_registry(tmp_path, monkeypatch, host)
    total = sum(e["size_bytes"] for e in entries.values())

    status = sim_client.get(f"{API}/ai/segment/engines").json()
    assert status["active"] == "grabcut"
    assert status["onnxruntime"] is True
    (engine,) = status["engines"]
    assert engine["id"] == "smart" and engine["installed"] is False
    assert engine["download_bytes"] == total
    assert {f["host"] for f in engine["files"]} == {"127.0.0.1"}
    assert sim_client.get(f"{API}/ai/capabilities").json()["segment_engines"] == [
        "smart",
        "grabcut",
    ]
    assert host.requests == []  # status never fetches

    resp = sim_client.post(
        f"{API}/ai/models/smart/download", json={"confirm": True, "expected_bytes": total}
    )
    assert resp.status_code == 202, resp.text
    job = _wait(sim_client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    assert job["kind"] == "download"
    assert sorted(host.requests) == ["/smart_decoder.onnx", "/smart_encoder.onnx"]

    status = sim_client.get(f"{API}/ai/segment/engines").json()
    assert status["active"] == "smart"
    assert status["engines"][0]["installed"] is True
    assert status["engines"][0]["download_bytes"] == 0
    assert not list((models.models_dir() / "smart").glob("*.part"))


def test_download_needs_confirmation_and_the_size_that_was_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: FakeModelHost, sim_client: TestClient
) -> None:
    entries = _served_registry(tmp_path, monkeypatch, host)
    total = sum(e["size_bytes"] for e in entries.values())
    url = f"{API}/ai/models/smart/download"
    assert sim_client.post(url, json={"confirm": False, "expected_bytes": total}).status_code == 422
    assert sim_client.post(url, json={"confirm": True, "expected_bytes": 1}).status_code == 422
    assert sim_client.post(url, json={}).status_code == 422
    assert (
        sim_client.post(
            f"{API}/ai/models/nope/download", json={"confirm": True, "expected_bytes": 0}
        ).status_code
        == 404
    )
    assert host.requests == []


def test_checksum_mismatch_refuses_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: FakeModelHost, sim_client: TestClient
) -> None:
    entries = _served_registry(tmp_path, monkeypatch, host, corrupt={"smart_decoder"})
    total = sum(e["size_bytes"] for e in entries.values())
    resp = sim_client.post(
        f"{API}/ai/models/smart/download", json={"confirm": True, "expected_bytes": total}
    )
    job = _wait(sim_client, resp.json()["job_id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] == MODEL_DOWNLOAD_FAILED
    assert "Checksum mismatch" in job["error"]["message"]
    folder = models.models_dir() / "smart"
    assert not (folder / "smart_decoder.onnx").exists()
    assert not list(folder.glob("*.part"))
    status = sim_client.get(f"{API}/ai/segment/engines").json()
    assert status["active"] == "grabcut" and status["engines"][0]["installed"] is False


def test_placeholder_engine_cannot_be_downloaded(sim_client: TestClient) -> None:
    resp = sim_client.post(
        f"{API}/ai/models/efficientsam_ti/download",
        json={"confirm": True, "expected_bytes": 24799761 + 16565728},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "AI_UNAVAILABLE"


# --- segment route --------------------------------------------------------------------------


def _calibrated(client: TestClient) -> str:
    pid: str = client.post(f"{API}/projects", json={"name": "demo"}).json()["id"]
    resp = client.post(
        f"{API}/projects/{pid}/calibrations",
        json={
            "mode": "scan",
            "camera_id": "sim",
            "settings": {"discard_frames": 0, "average_frames": 1},
        },
    )
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    return pid


def test_segment_never_downloads_then_uses_the_model_once_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: FakeModelHost, sim_client: TestClient
) -> None:
    entries = _served_registry(tmp_path, monkeypatch, host)
    total = sum(e["size_bytes"] for e in entries.values())
    pid = _calibrated(sim_client)
    door = default_scene().feature_quad_proj("garage_door")
    cx, cy = (float(v) for v in np.mean(door, axis=0))
    click = {"points": [{"x": cx, "y": cy, "label": 1}]}

    first = sim_client.post(f"{API}/projects/{pid}/segment", json=click).json()
    assert first["engine"] == "grabcut"
    assert first["engine_label"] == "Simple selector (GrabCut)"
    explicit = sim_client.post(f"{API}/projects/{pid}/segment", json={**click, "prefer": "smart"})
    assert explicit.json()["error"]["code"] == "AI_UNAVAILABLE"
    assert host.requests == []

    resp = sim_client.post(
        f"{API}/ai/models/smart/download", json={"confirm": True, "expected_bytes": total}
    )
    assert _wait(sim_client, resp.json()["job_id"])["state"] == "succeeded"

    after = sim_client.post(f"{API}/projects/{pid}/segment", json=click)
    assert after.status_code == 200, after.text
    body = after.json()
    assert body["engine"] == "smart" and body["engine_label"] == "Fake smart"
    xs = [p[0] for p in body["quad"]]
    door_xs = [p[0] for p in door]
    assert min(door_xs) - 15 < min(xs) and max(xs) < max(door_xs) + 15


def test_segment_box_prompt_and_old_request_shape(sim_client: TestClient) -> None:
    pid = _calibrated(sim_client)
    door = default_scene().feature_quad_proj("garage_door")
    xs, ys = [p[0] for p in door], [p[1] for p in door]
    box = {"x0": min(xs) - 6, "y0": min(ys) - 6, "x1": max(xs) + 6, "y1": max(ys) + 6}
    url = f"{API}/projects/{pid}/segment"

    boxed = sim_client.post(url, json={"points": [], "box": box, "prefer": "grabcut"})
    assert boxed.status_code == 200, boxed.text
    assert len(boxed.json()["quad"]) == 4
    # box alone, without "points" at all
    assert sim_client.post(url, json={"box": box}).status_code == 200
    # the T-106 request shape is unchanged
    centre = {"x": float(np.mean(xs)), "y": float(np.mean(ys)), "label": 1}
    old = sim_client.post(url, json={"points": [centre], "prefer": "grabcut"})
    assert old.status_code == 200 and old.json()["engine"] == "grabcut"
    # still refused: no include point and no box; a degenerate box; an odd engine name
    assert sim_client.post(url, json={"points": [{**centre, "label": 0}]}).status_code == 422
    tiny = {"x0": 10, "y0": 10, "x1": 11, "y1": 40}
    assert sim_client.post(url, json={"box": tiny}).status_code == 422
    assert sim_client.post(url, json={"points": [centre], "prefer": "../x"}).status_code == 422
