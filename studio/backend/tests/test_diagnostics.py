from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from facadekit import logs
from facadekit.api.app import create_app
from facadekit.cli import main
from facadekit.errors import INTERNAL, FkError


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    path = logs.setup_logging(tmp_path, console=False)
    yield path
    root = logging.getLogger("facadekit")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.propagate = True


def _client(tmp_path: Path, log_file: Path, **kwargs: object) -> TestClient:
    app = create_app(data_dir=tmp_path, sim=True, log_file=log_file, **kwargs)  # type: ignore[arg-type]
    extra = APIRouter()

    @extra.get("/api/v1/_test/crash")
    def crash() -> None:
        raise RuntimeError("boom")

    @extra.get("/api/v1/_test/internal")
    def internal() -> None:
        raise FkError(INTERNAL, "could not write the file")

    app.router.routes[0:0] = extra.routes  # before the catch-all page route
    return TestClient(app, raise_server_exceptions=False)


def _flush() -> str:
    for handler in logging.getLogger("facadekit").handlers:
        handler.flush()
    return ""


def test_every_response_carries_a_request_id(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path, log_file) as c:
        first = c.get("/api/v1/health").headers["X-Request-ID"]
        second = c.get("/api/v1/health").headers["X-Request-ID"]
    assert len(first) == 8 and first != second


def test_refused_request_is_explained_in_the_log(tmp_path: Path, log_file: Path) -> None:
    """The first hardware run had `camera-check -> 500` with nothing saying why."""
    with _client(tmp_path, log_file) as c:
        pid = c.post(
            "/api/v1/projects", json={"name": "p", "projector": {"width": 1280, "height": 720}}
        ).json()["id"]
        res = c.post(f"/api/v1/projects/{pid}/camera-check", json={"camera_id": "sim"})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "RESOLUTION_MISMATCH"
    _flush()
    text = log_file.read_text()
    request_id = res.headers["X-Request-ID"]
    explained = [line for line in text.splitlines() if "RESOLUTION_MISMATCH" in line]
    assert explained and f"[{request_id}]" in explained[0]
    assert "1920x1080" in explained[0] and "1280x720" in explained[0]
    assert "camera-check -> 500" in text


def test_validation_error_is_logged_with_the_field(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path, log_file) as c:
        pid = c.post("/api/v1/projects", json={"name": "p"}).json()["id"]
        res = c.post(f"/api/v1/projects/{pid}/calibrations", json={"mode": "scan", "x": 1})
    assert res.status_code == 422
    _flush()
    assert "BAD_REQUEST: body.x" in log_file.read_text()


def test_crash_returns_json_and_logs_the_traceback(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path, log_file) as c:
        res = c.get("/api/v1/_test/crash")
    assert res.status_code == 500
    error = res.json()["error"]
    assert error["code"] == "INTERNAL"
    assert "RuntimeError" in error["message"] and "boom" not in error["message"]
    request_id = res.headers["X-Request-ID"]
    assert request_id in error["message"]
    _flush()
    text = log_file.read_text()
    assert "Traceback" in text and "RuntimeError: boom" in text
    assert f"[{request_id}] facadekit.request: GET /api/v1/_test/crash crashed" in text


def test_internal_fkerror_logs_a_traceback(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path, log_file) as c:
        assert c.get("/api/v1/_test/internal").status_code == 500
    _flush()
    text = log_file.read_text()
    assert "INTERNAL: could not write the file" in text and "Traceback" in text


def test_failed_job_is_logged(tmp_path: Path, log_file: Path) -> None:
    from facadekit.jobs.runner import JobRunner

    runner = JobRunner()

    def fails(_progress: object, _cancel: object) -> dict[str, object]:
        raise FkError("TOO_BRIGHT", "too much light")

    job = runner.submit("calibration", fails)  # type: ignore[arg-type]
    runner.shutdown()
    assert runner.get(job.id).state == "failed"
    _flush()
    text = log_file.read_text()
    assert f"job {job.id} (calibration) failed: TOO_BRIGHT: too much light" in text
    assert f"job {job.id} (calibration) failed after" in text


def test_diagnostics_reports_versions_jobs_and_log_tail(tmp_path: Path, log_file: Path) -> None:
    with _client(tmp_path, log_file) as c:
        c.get("/api/v1/projects/nope")  # a 404 to find in the tail
        body = c.get("/api/v1/diagnostics").json()
    assert body["system"]["facadekit"] and body["system"]["opencv"]
    assert body["sim"] is True
    assert body["projector"]["connected"] is True
    assert any("NOT_FOUND" in line for line in body["log_tail"])
    assert body["jobs"] == []


def test_diagnostics_hides_the_home_directory(
    tmp_path: Path, log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    logging.getLogger("facadekit.test").warning("wrote %s", tmp_path / "projects" / "x")
    with _client(tmp_path, log_file) as c:
        body = c.get("/api/v1/diagnostics").json()
    assert str(tmp_path) not in str(body)
    assert any("~/projects/x" in line for line in body["log_tail"])


def test_diagnostics_is_not_open_to_paired_devices(tmp_path: Path, log_file: Path) -> None:
    with _client(
        tmp_path, log_file, lan=True, pairing_code="123456", client_addr_override=lambda: "10.0.0.9"
    ) as c:
        assert c.post("/api/v1/remote/pair", json={"code": "123456"}).status_code == 200
        assert c.get("/api/v1/health").status_code == 200
        assert c.get("/api/v1/diagnostics").status_code == 403


def test_log_rotates(tmp_path: Path, log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logger = logging.getLogger("facadekit.test")
    line = "x" * 1000
    for _ in range(logs.MAX_LOG_BYTES // 1000 + 50):
        logger.info(line)
    _flush()
    assert log_file.stat().st_size <= logs.MAX_LOG_BYTES
    assert (log_file.parent / (logs.LOG_NAME + ".1")).is_file()


def test_doctor_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FACADEKIT_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    log = logs.log_path(tmp_path / "data")
    log.parent.mkdir(parents=True)
    log.write_text(f"opened {tmp_path}/data/projects/a\n")
    out = tmp_path / "bundle.zip"
    assert main(["doctor", "--no-cameras", "--bundle", str(out)]) == 0
    assert "diagnostic bundle" in capsys.readouterr().out
    with zipfile.ZipFile(out) as bundle:
        assert set(bundle.namelist()) == {"doctor.txt", "logs/facadekit.log"}
        assert bundle.read("logs/facadekit.log") == b"opened ~/data/projects/a\n"
        report = bundle.read("doctor.txt").decode()
    assert "facadekit" in report and str(tmp_path) not in report


def test_doctor_bundle_needs_a_zip_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACADEKIT_DATA", str(tmp_path / "data"))
    assert main(["doctor", "--no-cameras", "--bundle", str(tmp_path / "x.txt")]) == 1
