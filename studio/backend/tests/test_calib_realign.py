from __future__ import annotations

import dataclasses
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.api.app import create_app
from facadekit.calib.pipeline import run_calibration
from facadekit.calib.realign import apply_drift, estimate_drift
from facadekit.errors import FkError
from facadekit.geom.homography import apply_h
from facadekit.models.calibration import Calibration, CalibSettings
from facadekit.models.project import Mask, Mesh, Project, Surface
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import default_scene
from facadekit.store.project_store import SUBFOLDERS, load_project_dir, save_project_dir

API = "/api/v1"
W, H = 960, 540


def textured(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), 90, np.uint8)
    for _ in range(120):
        x, y = int(rng.integers(0, W - 60)), int(rng.integers(0, H - 40))
        w, h = int(rng.integers(15, 120)), int(rng.integers(10, 80))
        color = tuple(int(c) for c in rng.integers(0, 256, 3))
        cv2.rectangle(img, (x, y), (x + w, y + h), color, -1 if rng.random() < 0.6 else 2)
    for i in range(40):
        pos = (int(rng.integers(0, W - 100)), int(rng.integers(20, H - 10)))
        cv2.putText(img, f"Fk{i}x", pos, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return img


def known_h() -> np.ndarray:
    theta = np.deg2rad(2.0)
    c, s = np.cos(theta), np.sin(theta)
    rot = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)
    center = np.array([[1, 0, W / 2], [0, 1, H / 2], [0, 0, 1]], dtype=np.float64)
    uncenter = np.array([[1, 0, -W / 2], [0, 1, -H / 2], [0, 0, 1]], dtype=np.float64)
    shift = np.array([[1, 0, 18], [0, 1, -11], [0, 0, 1]], dtype=np.float64)
    persp = np.array([[1, 0, 0], [0, 1, 0], [2e-5, -1e-5, 1]], dtype=np.float64)
    return np.asarray(shift @ center @ persp @ rot @ uncenter, dtype=np.float64)


FRAME = np.array([(0, 0), (W, 0), (W, H), (0, H)], dtype=np.float64)


def test_estimate_drift_recovers_known_homography() -> None:
    old = textured()
    truth = known_h()
    new = cv2.warpPerspective(old, truth, (W, H), borderValue=(90, 90, 90))
    res = estimate_drift(old, new)
    assert res.inliers >= 30
    err = np.linalg.norm(apply_h(res.h_old2new, FRAME) - apply_h(truth, FRAME), axis=1)
    assert err.max() < 1.5, err


def test_estimate_drift_unrelated_noise_fails() -> None:
    a = np.random.default_rng(1).integers(0, 256, (H, W, 3), dtype=np.uint8)
    b = np.random.default_rng(2).integers(0, 256, (H, W, 3), dtype=np.uint8)
    with pytest.raises(FkError) as exc:
        estimate_drift(a, b)
    assert exc.value.code == "REALIGN_FAILED"


def make_project() -> Project:
    quad = ((100.0, 100.0), (400.0, 110.0), (390.0, 300.0), (90.0, 290.0))
    mesh = Mesh(cols=2, rows=2, offsets=[(float(i), -float(i)) for i in range(9)])
    surface = Surface(
        id="srf_1",
        name="A",
        quad=quad,
        aspect=1.5,
        mask=Mask(
            outer=[(100, 100), (400, 110), (390, 300)], holes=[[(150, 150), (200, 150), (200, 200)]]
        ),
        mesh=mesh,
    )
    return Project(id="p", name="P", surfaces=[surface])


def test_apply_drift_moves_quad_and_keeps_timeline() -> None:
    project = make_project()
    h = known_h()
    moved = apply_drift(project, h)
    expected = apply_h(h, np.array(project.surfaces[0].quad))
    assert np.allclose(np.array(moved.surfaces[0].quad), expected, atol=1e-9)
    assert moved.surfaces[0].mask is not None
    assert moved.timeline == project.timeline
    assert moved.assets == project.assets
    assert moved.markers == project.markers
    assert project.surfaces[0].quad[0] == (100.0, 100.0)  # original untouched


def test_mesh_identity_round_trip() -> None:
    project = make_project()
    moved = apply_drift(project, np.eye(3))
    a, b = project.surfaces[0].mesh, moved.surfaces[0].mesh
    assert a is not None and b is not None
    assert np.allclose(np.array(a.offsets), np.array(b.offsets), atol=1e-6)


def test_simulator_drift(tmp_path: Path) -> None:
    def scan(scene_name: str, scene: object) -> tuple[Path, Calibration]:
        project_dir = tmp_path / f"{scene_name}.fkproj"
        project_dir.mkdir()
        for sub in SUBFOLDERS:
            (project_dir / sub).mkdir(exist_ok=True)
        save_project_dir(project_dir, Project(id=scene_name, name=scene_name))
        display = SimDisplay(scene)  # type: ignore[arg-type]
        camera = SimCamera(scene, display)  # type: ignore[arg-type]
        cal = run_calibration(
            project_dir,
            display,
            camera,
            "sim",
            CalibSettings(discard_frames=0, average_frames=1),
            sleep=lambda _s: None,
        )
        return project_dir, cal

    scene1 = default_scene()
    corners = tuple((x + 12.0, y - 8.0) for x, y in scene1.proj_corners_world)
    scene2 = dataclasses.replace(default_scene(), proj_corners_world=corners)  # type: ignore[arg-type]
    d1, c1 = scan("one", scene1)
    d2, c2 = scan("two", scene2)
    v1 = cv2.imread(str(d1 / "calibration" / c1.id / "proj_view.png"))
    v2 = cv2.imread(str(d2 / "calibration" / c2.id / "proj_view.png"))
    m1 = cv2.imread(str(d1 / "calibration" / c1.id / "measured_mask.png"), cv2.IMREAD_GRAYSCALE)
    m2 = cv2.imread(str(d2 / "calibration" / c2.id / "measured_mask.png"), cv2.IMREAD_GRAYSCALE)
    res = estimate_drift(v1, v2, old_mask=m1, new_mask=m2)
    door1 = np.array(scene1.feature_quad_proj("garage_door"))
    door2 = np.array(scene2.feature_quad_proj("garage_door"))
    err = np.linalg.norm(apply_h(res.h_old2new, door1) - door2, axis=1)
    assert err.max() < 4.0, (err, res)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:  # type: ignore[misc]
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c  # type: ignore[misc]


def _add_calibration(project_dir: Path, cid: str, image: np.ndarray) -> None:
    folder = project_dir / "calibration" / cid
    folder.mkdir(parents=True)
    cal = Calibration(
        id=cid,
        created_at="2026-11-09T21:14:03Z",
        mode="manual",
        projector_width=W,
        projector_height=H,
        camera_id=None,
        camera_width=None,
        camera_height=None,
        settings=None,
        quality=None,
        h_c2p=None,
    )
    (folder / "calibration.json").write_text(cal.model_dump_json(), encoding="utf-8")
    assert cv2.imwrite(str(folder / "proj_view.png"), image)


def test_route_preview_and_apply(client: TestClient, tmp_path: Path) -> None:
    created = client.post(f"{API}/projects", json={"name": "demo"}).json()
    pid = created["id"]
    project_dir = tmp_path / "projects" / f"{pid}.fkproj"
    old = textured()
    new = cv2.warpPerspective(old, known_h(), (W, H), borderValue=(90, 90, 90))
    _add_calibration(project_dir, "cal_0000000a", old)
    _add_calibration(project_dir, "cal_0000000b", new)
    project = load_project_dir(project_dir)
    surface = make_project().surfaces[0]
    save_project_dir(
        project_dir,
        project.model_copy(update={"surfaces": [surface], "calibration_id": "cal_0000000a"}),
    )
    before = client.get(f"{API}/projects/{pid}").json()

    body = {
        "old_calibration_id": "cal_0000000a",
        "new_calibration_id": "cal_0000000b",
        "apply": False,
    }
    resp = client.post(f"{API}/projects/{pid}/realign", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["inliers"] >= 30
    assert data["max_shift_px"] > 5
    assert data["project"] is not None
    assert client.get(f"{API}/projects/{pid}").json()["revision"] == before["revision"]

    resp = client.post(f"{API}/projects/{pid}/realign", json={**body, "apply": True})
    assert resp.status_code == 200, resp.text
    after = client.get(f"{API}/projects/{pid}").json()
    assert after["revision"] > before["revision"]
    assert after["calibration_id"] == "cal_0000000b"
    assert after["surfaces"][0]["quad"] != before["surfaces"][0]["quad"]

    missing = client.post(
        f"{API}/projects/{pid}/realign", json={**body, "old_calibration_id": "nope"}
    )
    assert missing.status_code == 404
