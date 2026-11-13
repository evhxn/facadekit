from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from facadekit.ai.segment import (
    GrabCutSegmenter,
    get_segmenter,
    mask_to_polygon_and_quad,
)
from facadekit.api.app import create_app
from facadekit.errors import FkError
from facadekit.sim.scene import default_scene

API = "/api/v1"


Quad = list[tuple[float, float]]


def _iou(a: Quad, b: Quad, size: tuple[int, int]) -> float:
    ma = np.zeros(size[::-1], np.uint8)
    mb = np.zeros(size[::-1], np.uint8)
    cv2.fillPoly(ma, [np.round(np.array(a)).astype(np.int32)], 1)
    cv2.fillPoly(mb, [np.round(np.array(b)).astype(np.int32)], 1)
    return float((ma & mb).sum() / (ma | mb).sum())


def test_quad_from_filled_quadrilateral() -> None:
    corners = [(40.0, 30.0), (260.0, 50.0), (240.0, 190.0), (60.0, 170.0)]
    mask = np.zeros((220, 300), np.uint8)
    cv2.fillPoly(mask, [np.array(corners, np.int32)], 255)
    outer, quad = mask_to_polygon_and_quad(mask)
    assert len(outer) >= 4
    assert len(quad) == 4
    for got, want in zip(quad, corners, strict=True):
        assert np.hypot(got[0] - want[0], got[1] - want[1]) < 3


def test_circle_gives_four_point_quad_and_rich_polygon() -> None:
    mask = np.zeros((300, 300), bool)
    cv2.circle(mask.view(np.uint8), (150, 150), 100, 1, -1)
    outer, quad = mask_to_polygon_and_quad(mask)
    assert len(quad) == 4
    assert 8 <= len(outer) <= 64


def test_empty_mask() -> None:
    with pytest.raises(FkError) as exc:
        mask_to_polygon_and_quad(np.zeros((10, 10), np.uint8))
    assert exc.value.code == "BAD_REQUEST"


def _proj_view() -> tuple[np.ndarray, list[tuple[float, float]]]:
    scene = default_scene()
    w, h = scene.proj_size
    img = cv2.warpPerspective(
        scene.albedo(),
        np.linalg.inv(scene.h_p2w),
        (w, h),
        flags=cv2.INTER_LINEAR,
    )
    return img, scene.feature_quad_proj("garage_door")


def test_grabcut_garage_door() -> None:
    img, door = _proj_view()
    centre = np.mean(door, axis=0)
    seg = GrabCutSegmenter()
    seg.set_image(img)
    result = seg.segment([(float(centre[0]), float(centre[1]), 1)])
    assert len(result.quad) == 4
    assert result.score == 0.5
    assert _iou(result.quad, door, (img.shape[1], img.shape[0])) > 0.8


def test_auto_falls_back_to_grabcut() -> None:
    # The bundled registry holds placeholders, so MobileSAM cannot load.
    assert isinstance(get_segmenter("auto"), GrabCutSegmenter)


@pytest.fixture
def sim_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as c:
        yield c


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
    assert resp.status_code == 202
    deadline = time.monotonic() + 60
    while True:
        job: dict[str, Any] = client.get(f"{API}/jobs/{resp.json()['job_id']}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            break
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert job["state"] == "succeeded", job
    return pid


def test_segment_route(sim_client: TestClient) -> None:
    pid = _calibrated(sim_client)
    centre = np.mean(default_scene().feature_quad_proj("garage_door"), axis=0)
    body = {"points": [{"x": centre[0], "y": centre[1], "label": 1}], "prefer": "grabcut"}
    resp = sim_client.post(f"{API}/projects/{pid}/segment", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["engine"] == "grabcut"
    assert len(data["quad"]) == 4
    # second click reuses the cached segmenter
    assert sim_client.post(f"{API}/projects/{pid}/segment", json=body).status_code == 200
    no_include = {"points": [{"x": 10, "y": 10, "label": 0}], "prefer": "grabcut"}
    assert sim_client.post(f"{API}/projects/{pid}/segment", json=no_include).status_code == 422
    # the cache is bounded: older segmenters (each holds a full image) are dropped
    cache = sim_client.app.state.fk.segmenters  # type: ignore[attr-defined]
    (first,) = list(cache)
    cache[("old", "cal_00000000", "grabcut")] = object()
    assert (
        sim_client.post(
            f"{API}/projects/{pid}/segment", json={**body, "prefer": "auto"}
        ).status_code
        == 200
    )
    assert len(cache) == 2 and first not in cache
    assert list(cache)[-1] == (first[0], first[1], "auto")


def test_segment_route_requires_calibration(sim_client: TestClient) -> None:
    pid = sim_client.post(f"{API}/projects", json={"name": "demo"}).json()["id"]
    body = {"points": [{"x": 5, "y": 5, "label": 1}]}
    assert sim_client.post(f"{API}/projects/{pid}/segment", json=body).status_code == 422


@pytest.mark.models
@pytest.mark.skipif(os.environ.get("FK_TEST_MODELS") != "1", reason="set FK_TEST_MODELS=1")
def test_mobilesam_garage_door() -> None:
    img, door = _proj_view()
    centre = np.mean(door, axis=0)
    seg = get_segmenter("mobilesam")
    seg.set_image(img)
    result = seg.segment([(float(centre[0]), float(centre[1]), 1)])
    assert len(result.quad) == 4
    assert _iou(result.quad, door, (img.shape[1], img.shape[0])) > 0.8
