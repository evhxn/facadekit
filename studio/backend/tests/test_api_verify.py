"""HTTP routes of Verify surface, against `serve --sim` (docs/13)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from facadekit.api.app import create_app
from facadekit.sim.scene import default_scene

API = "/api/v1"
CODE = "123456"
PHONE = "192.168.1.50"


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while True:
        job: dict[str, Any] = client.get(f"{API}/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline
        time.sleep(0.05)


class Addr:
    host = "127.0.0.1"

    def __call__(self) -> str:
        return self.host


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[TestClient, str, Addr]]:
    addr = Addr()
    app = create_app(
        data_dir=tmp_path_factory.mktemp("api"),
        sim=True,
        lan=True,
        pairing_code=CODE,
        client_addr_override=addr,
        testing=True,
    )
    with TestClient(app) as client:
        pid = client.post(f"{API}/projects", json={"name": "Verify"}).json()["id"]
        resp = client.post(
            f"{API}/projects/{pid}/calibrations",
            json={"mode": "scan", "camera_id": "sim", "learn": False},
        )
        assert resp.status_code == 202, resp.text
        assert _wait(client, resp.json()["job_id"])["state"] == "succeeded"
        project = client.get(f"{API}/projects/{pid}").json()
        quad = default_scene().feature_quad_proj("garage_door")
        project["surfaces"] = [
            {
                "id": "srf_door",
                "name": "Door",
                "quad": quad,
                "mask": None,
                "feather_px": 0,
                "aspect": 2.0,
                "mesh": None,
            }
        ]
        assert client.put(f"{API}/projects/{pid}", json=project).status_code == 200
        yield client, pid, addr


def test_verify_a_surface(served: tuple[TestClient, str, Addr]) -> None:
    client, pid, _ = served
    rev = client.get(f"{API}/projects/{pid}").json()["revision"]
    resp = client.post(f"{API}/projects/{pid}/verify", json={"surface_id": "srf_door"})
    assert resp.status_code == 202, resp.text
    job = _wait(client, resp.json()["job_id"])
    assert job["state"] == "succeeded", job
    rec = job["result"]["verification"]
    assert rec["rig"]["verdict"] == "stable"
    assert rec["surface"]["verdict"] == "pass"
    saved = client.get(f"{API}/projects/{pid}/verifications/srf_door")
    assert saved.status_code == 200 and saved.json()["id"] == rec["id"]
    pic = client.get(f"{API}/projects/{pid}/verifications/srf_door/overlay.jpg")
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/jpeg"
    # the check is stored next to the project, never in it
    assert client.get(f"{API}/projects/{pid}").json()["revision"] == rev


def test_rig_only_and_self_test_injection(served: tuple[TestClient, str, Addr]) -> None:
    client, pid, _ = served
    job = _wait(client, client.post(f"{API}/projects/{pid}/verify", json={}).json()["job_id"])
    assert job["result"]["verification"]["surface"] is None
    assert client.get(f"{API}/projects/{pid}/verifications/rig").status_code == 200
    body = {"surface_id": "srf_door", "inject": {"proj_shift": [6, 0], "grow_px": 0}}
    job = _wait(client, client.post(f"{API}/projects/{pid}/verify", json=body).json()["job_id"])
    rec = job["result"]["verification"]
    assert rec["rig"]["verdict"] == "projector_moved" and rec["injected"]["proj_shift_x"] == 6
    assert job["result"]["overlay_jpg_b64"]
    # a self-test run does not replace the surface's own record
    saved = client.get(f"{API}/projects/{pid}/verifications/srf_door").json()
    assert saved["injected"] is None


def test_refusals(served: tuple[TestClient, str, Addr]) -> None:
    client, pid, _ = served
    r = client.post(f"{API}/projects/{pid}/verify", json={"surface_id": "srf_nope"})
    assert r.status_code == 404
    r = client.post(f"{API}/projects/{pid}/verify", json={"surface_id": "srf_door", "x": 1})
    assert r.status_code == 422
    r = client.post(f"{API}/projects/{pid}/verify", json={"camera_id": "phone"})
    assert r.status_code == 422 and "laptop camera" in r.json()["error"]["message"]
    assert client.get(f"{API}/projects/{pid}/verifications/srf_nope").status_code == 404
    assert client.get(f"{API}/projects/{pid}/verifications/..%2Fx").status_code == 404
    other = client.post(f"{API}/projects", json={"name": "No scan"}).json()["id"]
    r = client.post(f"{API}/projects/{other}/verify", json={})
    assert r.status_code == 422 and "Scan the house" in r.json()["error"]["message"]


def test_manual_mode_is_refused(served: tuple[TestClient, str, Addr]) -> None:
    client, _, _ = served
    pid = client.post(f"{API}/projects", json={"name": "Manual"}).json()["id"]
    job = client.post(f"{API}/projects/{pid}/calibrations", json={"mode": "manual"}).json()
    assert _wait(client, job["job_id"])["state"] == "succeeded"
    r = client.post(f"{API}/projects/{pid}/verify", json={})
    assert r.status_code == 422 and "manual mode" in r.json()["error"]["message"]


def test_camera_busy_is_409(served: tuple[TestClient, str, Addr]) -> None:
    client, pid, _ = served
    state = client.app.state.fk  # type: ignore[attr-defined]
    state.cameras.acquire("sim")
    try:
        r = client.post(f"{API}/projects/{pid}/verify", json={})
        assert r.status_code == 409
    finally:
        state.cameras.release("sim")


def test_not_open_to_paired_devices(served: tuple[TestClient, str, Addr]) -> None:
    client, pid, addr = served
    addr.host = PHONE
    try:
        assert client.post(f"{API}/remote/pair", json={"code": CODE}).status_code == 200
        assert client.get(f"{API}/projects/{pid}").status_code == 200  # paired: allowed
        assert client.post(f"{API}/projects/{pid}/verify", json={}).status_code == 403
        assert client.get(f"{API}/projects/{pid}/verifications/srf_door").status_code == 403
        assert (
            client.get(f"{API}/projects/{pid}/verifications/srf_door/overlay.jpg").status_code
            == 403
        )
    finally:
        addr.host = "127.0.0.1"
        client.cookies.clear()


def test_cli_verify_sim_and_self_test(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json
    import zipfile

    from facadekit.cli import main
    from facadekit.store.project_store import load_project_dir, save_project_dir

    proj = tmp_path / "yard.fkproj"
    assert main(["new", str(proj), "--name", "Yard"]) == 0
    assert main(["calibrate", str(proj), "--sim"]) == 0
    p = load_project_dir(proj)
    quad = default_scene().feature_quad_proj("garage_door")
    from facadekit.models.project import Surface

    p = p.model_copy(
        update={"surfaces": [Surface(id="srf_door", name="Door", quad=tuple(quad), aspect=2.0)]}
    )  # type: ignore[arg-type]
    save_project_dir(proj, p)
    capsys.readouterr()
    assert main(["verify", str(proj), "--sim", "--surface", "srf_door"]) == 0
    out = capsys.readouterr().out
    assert "rig: stable" in out and "surface srf_door: PASS" in out
    assert main(["verify", str(proj), "--sim", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rig"]["verdict"] == "stable"
    bundle = tmp_path / "selftest.zip"
    code = main(
        [
            "verify",
            str(proj),
            "--sim",
            "--self-test",
            "--surface",
            "srf_door",
            "--bundle",
            str(bundle),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "SELF-TEST PASSED" in out
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
    assert "report.txt" in names and any(n.startswith("records/") for n in names)
    assert any(n.startswith("pictures/") for n in names)


def test_cli_verify_without_a_server(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from facadekit.cli import main

    code = main(["verify", str(tmp_path / "x.fkproj"), "--server", "http://127.0.0.1:9"])
    assert code == 1
    assert "SERVER_UNREACHABLE" in capsys.readouterr().err
