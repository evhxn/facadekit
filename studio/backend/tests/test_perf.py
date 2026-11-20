from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from facadekit import perf
from facadekit.api import routes_devices
from facadekit.api.app import create_app
from facadekit.api.routes_devices import CameraManager
from facadekit.cli import main
from facadekit.sysinfo import total_ram_bytes

GB = 1024**3


def test_ram_is_found() -> None:
    assert total_ram_bytes() > GB


@pytest.mark.parametrize(
    ("cores", "ram", "on"),
    [
        (4, 16 * GB, True),  # an old quad-core laptop
        (8, 8 * GB - 200 * 1024**2, True),  # "8 GB" as the system reports it
        (8, 16 * GB, False),
        (18, 24 * GB, False),
        (8, 0, False),  # memory unknown: decide by cores
    ],
)
def test_low_power_is_chosen_from_cores_and_memory(
    monkeypatch: pytest.MonkeyPatch, cores: int, ram: int, on: bool
) -> None:
    monkeypatch.delenv(perf.LOW_POWER_ENV, raising=False)
    assert perf.low_power(cores, ram)[0] is on


def test_low_power_can_be_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(perf.LOW_POWER_ENV, "1")
    assert perf.low_power(18, 24 * GB) == (True, f"{perf.LOW_POWER_ENV} is set")
    monkeypatch.setenv(perf.LOW_POWER_ENV, "0")
    assert perf.low_power(2, 4 * GB)[0] is False


def test_low_power_slows_only_the_preview_camera() -> None:
    assert CameraManager(low_power=True).low_power is True
    assert routes_devices.LOW_POWER_GRAB_INTERVAL_S > routes_devices.GRAB_INTERVAL_S


def test_diagnostics_reports_low_power(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(perf.LOW_POWER_ENV, "1")
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as c:
        body = c.get("/api/v1/diagnostics").json()
    assert body["low_power"] is True
    assert body["machine"]["cores"] >= 1


def test_benchmark_runs_and_reports(tmp_path: Path) -> None:
    results = perf.run(tmp_path)
    assert [r.name for r in results] == list(perf.BUDGET_S)
    for r in results:
        assert r.wall_s > 0 and r.peak_rss_mb > 0
    render = results[-1]
    assert render.per == "second of show" and render.child_cpu_s > 0  # ffmpeg was measured
    lines = perf.report(results)
    assert lines[0].startswith("workload") and len(lines) == len(results) + 2


def test_doctor_without_perf_stays_quick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FACADEKIT_DATA", str(tmp_path))
    assert main(["doctor", "--no-cameras"]) == 0
    out = capsys.readouterr().out
    assert "low-power mode:" in out and "workload" not in out
