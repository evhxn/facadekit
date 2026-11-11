from __future__ import annotations

from pathlib import Path

import cv2
import pytest
from fastapi.testclient import TestClient

from facadekit.api.app import create_app
from facadekit.calib import learn_align as la
from facadekit.store.project_store import load_project_dir, save_project_dir
from tests.test_calib_realign import (
    H,
    W,
    _add_calibration,
    known_h,
    make_project,
    textured,
)

API = "/api/v1"


def rec(shift: float, n: int = 0) -> la.AlignRecord:
    return la.AlignRecord(
        id=f"aln_{n}", at="t", project_id="p", max_shift_px=shift, reproj_error_px=1.0, inliers=50
    )


def test_empty_and_short_log_gives_no_threshold() -> None:
    assert la.warn_threshold([]) is None
    assert la.warning_for([], 500.0) is None
    assert la.warn_threshold([rec(10)] * (la.MIN_SAMPLES - 1)) is None


def test_threshold_is_bounded() -> None:
    small = [rec(1.0, i) for i in range(10)]
    assert la.warn_threshold(small) == la.WARN_DEFAULT_SHIFT_PX  # never below the default
    mid = [rec(30.0, i) for i in range(10)]
    assert la.warn_threshold(mid) == 60.0
    huge = [rec(900.0, i) for i in range(10)]
    assert la.warn_threshold(huge) == la.WARN_CAP_SHIFT_PX


def test_median_window_ignores_old_outliers() -> None:
    old = [rec(500.0, i) for i in range(30)]
    recent = [rec(25.0, 100 + i) for i in range(la.WINDOW)]
    assert la.warn_threshold(old + recent) == 50.0


def test_warning_only_above_threshold() -> None:
    records = [rec(25.0, i) for i in range(8)]
    assert la.warning_for(records, 50.0) is None
    msg = la.warning_for(records, 51.0)
    assert msg is not None and "51 px" in msg


def test_memory_roundtrip_trim_and_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mem = la.AlignMemory(tmp_path / "learning" / "realign.jsonl")
    assert mem.records() == []
    for i in range(la.MAX_RECORDS + 7):
        mem.record_applied("p", float(i), 1.0, 40)
    got = mem.records()
    assert len(got) == la.MAX_RECORDS
    assert got[-1].max_shift_px == la.MAX_RECORDS + 6
    mem.path.write_text(mem.path.read_text() + "{broken\n", encoding="utf-8")
    assert len(mem.records()) == la.MAX_RECORDS
    mem.clear()
    assert mem.records() == []
    monkeypatch.setenv("FACADEKIT_LEARN", "0")
    mem.record_applied("p", 10.0, 1.0, 40)
    assert mem.records() == []
    assert mem.warning(10_000.0) is None


def test_damaged_utf8_log_keeps_valid_lines(tmp_path: Path) -> None:
    log = tmp_path / "learning" / "realign.jsonl"
    mem = la.AlignMemory(log)
    mem.record_applied("p", 3.0, 1.0, 40)
    log.write_bytes(log.read_bytes() + b"\xff\xfe\xc3\x28\n")
    assert [r.max_shift_px for r in mem.records()] == [3.0]
    mem.record_applied("p", 4.0, 1.0, 40)
    assert [r.max_shift_px for r in mem.records()] == [3.0, 4.0]
    assert [p.name for p in log.parent.iterdir()] == [log.name]


def test_two_instances_share_one_log(tmp_path: Path) -> None:
    log = tmp_path / "learning" / "realign.jsonl"
    a, b = la.AlignMemory(log), la.AlignMemory(log)
    for i in range(9):
        (a if i % 2 == 0 else b).record_applied("p", float(i), 1.0, 40)
    assert [r.max_shift_px for r in a.records()] == [float(i) for i in range(9)]
    assert [p.name for p in log.parent.iterdir()] == [log.name]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:  # type: ignore[misc]
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c  # type: ignore[misc]


def _setup(client: TestClient, tmp_path: Path) -> tuple[str, dict[str, object]]:
    pid = client.post(f"{API}/projects", json={"name": "demo"}).json()["id"]
    project_dir = tmp_path / "projects" / f"{pid}.fkproj"
    old = textured()
    new = cv2.warpPerspective(old, known_h(), (W, H), borderValue=(90, 90, 90))
    _add_calibration(project_dir, "cal_0000000a", old)
    _add_calibration(project_dir, "cal_0000000b", new)
    project = load_project_dir(project_dir)
    save_project_dir(
        project_dir,
        project.model_copy(
            update={"surfaces": [make_project().surfaces[0]], "calibration_id": "cal_0000000a"}
        ),
    )
    body: dict[str, object] = {
        "old_calibration_id": "cal_0000000a",
        "new_calibration_id": "cal_0000000b",
        "apply": True,
    }
    return pid, body


def test_route_logs_applied_only_and_warns(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(la, "WARN_DEFAULT_SHIFT_PX", 5.0)  # the test drift is ~20 px
    pid, body = _setup(client, tmp_path)
    log = tmp_path / "learning" / "realign.jsonl"

    first = client.post(f"{API}/projects/{pid}/realign", json={**body, "apply": False}).json()
    assert first["warning"] is None
    assert not log.exists()  # previews are not logged

    shift = client.post(f"{API}/projects/{pid}/realign", json=body).json()["max_shift_px"]
    assert len(log.read_text().splitlines()) == 1
    summary = client.get(f"{API}/learning/realign").json()
    assert summary["corrections"] == 1
    assert summary["warn_threshold_px"] is None  # too little history

    # A user who usually makes tiny corrections: a big one now draws a warning.
    mem = client.app.state.fk.align_memory  # type: ignore[attr-defined]
    for _ in range(la.MIN_SAMPLES):
        mem.record_applied(pid, 1.0, 1.0, 40)
    resp = client.post(f"{API}/projects/{pid}/realign", json={**body, "apply": False}).json()
    assert shift > 5.0
    assert resp["warning"] is not None
    summary = client.get(f"{API}/learning/realign").json()
    assert summary["warn_threshold_px"] == 5.0

    assert client.delete(f"{API}/learning/realign").status_code == 204
    assert client.get(f"{API}/learning/realign").json()["corrections"] == 0


def test_route_with_learning_off_writes_nothing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FACADEKIT_LEARN", "0")
    pid, body = _setup(client, tmp_path)
    resp = client.post(f"{API}/projects/{pid}/realign", json=body).json()
    assert resp["warning"] is None
    assert not (tmp_path / "learning" / "realign.jsonl").exists()
    assert client.get(f"{API}/learning/realign").json()["enabled"] is False


def test_route_survives_damaged_log(client: TestClient, tmp_path: Path) -> None:
    pid, body = _setup(client, tmp_path)
    log = tmp_path / "learning" / "realign.jsonl"
    log.parent.mkdir(exist_ok=True)
    log.write_bytes(b"\xff\xfe\x80 not utf-8\n")
    resp = client.post(f"{API}/projects/{pid}/realign", json=body)
    assert resp.status_code == 200, resp.text
    assert client.get(f"{API}/learning/realign").json()["corrections"] == 1
    assert [p.name for p in log.parent.iterdir()] == [log.name]
