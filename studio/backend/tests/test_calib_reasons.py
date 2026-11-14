from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from facadekit.api.app import create_app
from facadekit.calib.pipeline import run_calibration
from facadekit.calib.reasons import (
    BRIGHT,
    DARK,
    MEASURED,
    OUTSIDE,
    PNG_STEP,
    UNCLEAR,
    reason_fractions,
    unmeasured_reasons,
)
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import default_scene


def test_each_reason_is_told_apart() -> None:
    """A 40x40 projector seen 1:1 by a 40x40 camera, shifted so 10 columns fall outside."""
    ys, xs = np.mgrid[0:40, 0:40]
    p2c = np.stack([xs + 10 + 0.5, ys + 0.5], axis=2).astype(np.float32)
    contrast = np.full((40, 40), 100.0, np.float32)
    white = np.full((40, 40), 150.0, np.float32)
    contrast[:20, 10:20] = 2.0  # camera x 10..20, top half: no projector light arrived
    white[20:, 10:20] = 255.0  # same columns, bottom half: washed out
    measured = np.zeros((40, 40), bool)
    measured[:, 20:28] = True
    reasons = unmeasured_reasons(p2c, measured, contrast, white)
    assert reasons[5, 3] == DARK and reasons[30, 3] == BRIGHT  # projector x 3 -> camera x 13
    assert reasons[5, 15] == UNCLEAR  # lit, not washed out, still not measured
    assert reasons[5, 24] == MEASURED
    assert (reasons[:, 31:] == OUTSIDE).all()  # camera x >= 41
    shares = reason_fractions(reasons)
    assert set(shares) == {"dark", "bright", "outside", "unclear"}
    assert abs(sum(shares.values()) + measured.mean() - 1.0) < 1e-9


def test_nan_map_counts_as_outside() -> None:
    p2c = np.full((4, 4, 2), np.nan, np.float32)
    reasons = unmeasured_reasons(
        p2c, np.zeros((4, 4), bool), np.ones((8, 8), np.float32), np.ones((8, 8), np.float32)
    )
    assert (reasons == OUTSIDE).all()


def test_scan_of_a_shadowed_wall_blames_darkness_where_the_shadow_is(tmp_path: Path) -> None:
    """Something stands in the beam: that part of the wall gets no projector light."""
    scene = replace(default_scene(), occluders_world=[(700.0, 300.0, 1000.0, 600.0)])
    display = SimDisplay(scene)
    cal = run_calibration(
        tmp_path, display, SimCamera(scene, display), "sim",
        CalibSettings(discard_frames=0, average_frames=1), sleep=lambda s: None,
    )  # fmt: skip
    folder = tmp_path / "calibration" / cal.id
    reasons = cv2.imread(str(folder / "reasons.png"), cv2.IMREAD_GRAYSCALE) // PNG_STEP
    mask = cv2.imread(str(folder / "measured_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
    assert reasons.shape == (1080, 1920)
    assert ((reasons == MEASURED) == mask).all()
    unmeasured = ~mask
    assert unmeasured.mean() > 0.03
    dark_share = (reasons[unmeasured] == DARK).mean()
    assert dark_share > 0.8, dark_share
    assert cal.quality is not None and cal.quality.unmeasured is not None
    assert cal.quality.unmeasured["dark"] > 0.03
    assert cal.quality.unmeasured["outside"] < 0.01
    total = sum(cal.quality.unmeasured.values()) + mask.mean()
    assert abs(total - 1.0) < 1e-6


def test_reasons_route_and_older_scans(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path, sim=True)) as c:
        pid = c.post("/api/v1/projects", json={"name": "p"}).json()["id"]
        job_id = c.post(
            f"/api/v1/projects/{pid}/calibrations",
            json={"mode": "scan", "camera_id": "sim", "settings": {"settle_ms": 300}},
        ).json()["job_id"]
        while (job := c.get(f"/api/v1/jobs/{job_id}").json())["state"] in ("queued", "running"):
            pass
        assert job["state"] == "succeeded", job
        cid = job["result"]["calibration_id"]
        res = c.get(f"/api/v1/projects/{pid}/calibrations/{cid}/reasons.png")
        assert res.status_code == 200 and res.headers["content-type"] == "image/png"
        cal = c.get(f"/api/v1/projects/{pid}/calibrations/{cid}").json()
        assert set(cal["quality"]["unmeasured"]) == {"dark", "bright", "outside", "unclear"}
        # A scan from before this change has no reasons.png: 404, and the UI falls back.
        folder = c.app.state.fk.store.project_dir(pid)  # type: ignore[attr-defined]
        (folder / "calibration" / cid / "reasons.png").unlink()
        res = c.get(f"/api/v1/projects/{pid}/calibrations/{cid}/reasons.png")
        assert res.status_code == 404
