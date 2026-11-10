from __future__ import annotations

import time
import wave
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("librosa")

from facadekit.ai.beats import analyze_beats  # noqa: E402
from facadekit.errors import FkError  # noqa: E402

API = "/api/v1"


@pytest.fixture(scope="module")
def click_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    sr = 22050
    y = np.zeros(sr * 10, dtype=np.float32)
    burst = int(0.005 * sr)
    rng = np.random.default_rng(0)
    for k in range(20):
        start = int(k * 0.5 * sr)
        y[start : start + burst] = rng.uniform(-1, 1, burst).astype(np.float32)
    pcm = (y * 32767).astype("<i2")
    path = tmp_path_factory.mktemp("beats") / "clicks.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _upload(client: TestClient, pid: str, path: Path) -> str:
    with path.open("rb") as handle:
        resp = client.post(f"{API}/projects/{pid}/assets", files={"file": (path.name, handle)})
    assert resp.status_code == 201, resp.text
    aid: str = resp.json()["asset"]["id"]
    return aid


def test_analyze_beats_click_track(click_wav: Path) -> None:
    markers = analyze_beats(click_wav)
    beats = [m.t_ms for m in markers if m.kind == "beat"]
    assert 17 <= len(beats) <= 22
    intervals = [b - a for a, b in zip(beats, beats[1:], strict=False)]
    assert abs(median(intervals) - 500) <= 15
    assert [m.t_ms for m in markers] == sorted(m.t_ms for m in markers)
    assert {m.kind for m in markers} <= {"beat", "onset"}


def test_route_replaces_non_user_markers(client: TestClient, click_wav: Path) -> None:
    pid = client.post(f"{API}/projects", json={"name": "Show"}).json()["id"]
    aid = _upload(client, pid, click_wav)
    project = client.get(f"{API}/projects/{pid}").json()
    project["markers"] = [
        {"t_ms": 1234, "kind": "user"},
        {"t_ms": 99999, "kind": "beat"},
    ]
    assert client.put(f"{API}/projects/{pid}", json=project).status_code == 200

    resp = client.post(f"{API}/projects/{pid}/assets/{aid}/analyze")
    assert resp.status_code == 202
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    assert job["result"]["markers"] > 0

    markers = client.get(f"{API}/projects/{pid}").json()["markers"]
    times = [m["t_ms"] for m in markers]
    assert times == sorted(times)
    assert {"t_ms": 1234, "kind": "user"} in markers
    assert 99999 not in times
    assert len(markers) == job["result"]["markers"] + 1


def test_route_rejects_image(client: TestClient, sample_image: Path) -> None:
    pid = client.post(f"{API}/projects", json={"name": "Show"}).json()["id"]
    aid = _upload(client, pid, sample_image)
    resp = client.post(f"{API}/projects/{pid}/assets/{aid}/analyze")
    assert resp.status_code == 422


def test_librosa_missing(
    client: TestClient, click_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "librosa" or name.startswith("librosa."):
            raise ImportError("no librosa")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(FkError) as info:
        analyze_beats(click_wav)
    assert info.value.code == "AI_UNAVAILABLE"

    pid = client.post(f"{API}/projects", json={"name": "Show"}).json()["id"]
    aid = _upload(client, pid, click_wav)
    resp = client.post(f"{API}/projects/{pid}/assets/{aid}/analyze")
    assert resp.status_code == 202
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] == "AI_UNAVAILABLE"
