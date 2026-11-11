from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from facadekit.calib.pipeline import load_calibration, run_calibration
from facadekit.errors import INTERNAL, FkError
from facadekit.geom.homography import apply_h, estimate_aspect
from facadekit.ids import new_id
from facadekit.models.calibration import CalibSettings
from facadekit.models.project import Asset, Clip, Project, Surface, Track
from facadekit.render.compositor import Compositor
from facadekit.render.pipeline import render_project
from facadekit.sim.devices import SimCamera, SimDisplay
from facadekit.sim.scene import default_scene
from facadekit.store.project_store import SUBFOLDERS, load_project_dir, save_project_dir

Point = tuple[float, float]

MAX_CORNER_ERROR_PX = 1.5


@dataclass
class E2EResult:
    project_dir: Path
    render_path: Path
    max_corner_error_px: float
    coverage: float


def _order_corners(pts: np.ndarray) -> np.ndarray:
    by_y: np.ndarray = pts[np.argsort(pts[:, 1], kind="stable")]
    top = by_y[:2][np.argsort(by_y[:2, 0])]
    bottom = by_y[2:][np.argsort(-by_y[2:, 0])]
    return np.array([top[0], top[1], bottom[0], bottom[1]], dtype=np.float64)


def _intersect(l1: np.ndarray, l2: np.ndarray) -> np.ndarray:
    """Intersect two lines given as (vx, vy, x0, y0)."""
    a = np.array([[l1[0], -l2[0]], [l1[1], -l2[1]]], dtype=np.float64)
    b = np.array([l2[2] - l1[2], l2[3] - l1[3]], dtype=np.float64)
    try:
        s = np.linalg.solve(a, b)
    except np.linalg.LinAlgError as exc:
        raise FkError(INTERNAL, "Door edges are parallel") from exc
    return np.array([l1[2] + s[0] * l1[0], l1[3] + s[0] * l1[1]], dtype=np.float64)


def detect_door_quad(proj_view: np.ndarray) -> list[Point]:
    """Find the light-gray door in the projector view; returns TL, TR, BR, BL corners."""
    img = proj_view.astype(np.int16)
    bright = (img > 110).all(axis=2)
    spread = img.max(axis=2) - img.min(axis=2)
    mask = (bright & (spread < 40)).astype(np.uint8) * 255
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise FkError(INTERNAL, "No door region found in the projector view")
    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    approx = None
    eps = 0.02
    for _ in range(10):
        candidate = cv2.approxPolyDP(contour, eps * perimeter, True)
        if len(candidate) == 4:
            approx = candidate
            break
        eps += 0.01
    if approx is None:
        raise FkError(INTERNAL, "Could not reduce the door outline to four corners")
    corners = _order_corners(approx.reshape(-1, 2).astype(np.float64))

    # Contour points are indices of boundary pixels inside the mask; their centres are at
    # +0.5 in pixel-corner coordinates, and the true edge lies half a pixel further out.
    pts = contour.reshape(-1, 2).astype(np.float64) + 0.5
    centroid = pts.mean(axis=0)
    lines: list[np.ndarray] = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        ab = b - a
        length2 = float(ab @ ab)
        t = ((pts - a) @ ab) / length2
        dist = np.abs((pts - a) @ np.array([-ab[1], ab[0]])) / np.sqrt(length2)
        side = pts[(t >= 0.15) & (t <= 0.85) & (dist < 6.0)]
        if len(side) < 2:
            raise FkError(INTERNAL, "Too few contour points on a door edge")
        fit = cv2.fitLine(side.astype(np.float32), cv2.DIST_L2, 0, 0.01, 0.01).reshape(-1)
        line = fit.astype(np.float64)
        normal = np.array([-line[1], line[0]])
        if float(normal @ (line[2:4] - centroid)) < 0:
            normal = -normal
        line[2:4] = line[2:4] + 0.5 * normal
        lines.append(line)
    # Side i joins corner i and i+1; corner i is the intersection of sides i-1 and i.
    refined = [_intersect(lines[(i - 1) % 4], lines[i]) for i in range(4)]
    return [(float(p[0]), float(p[1])) for p in refined]


def _map_corner_error(p2c: np.ndarray, h_p2c: np.ndarray, corners: np.ndarray) -> float:
    """Max distance between p2c (bilinearly sampled at the corners) and the true p2c."""
    h, w = p2c.shape[:2]
    worst = 0.0
    for cx, cy in corners:
        fx, fy = float(cx) - 0.5, float(cy) - 0.5  # p2c[y, x] is the pixel centre (x+.5, y+.5)
        x0, y0 = int(np.clip(np.floor(fx), 0, w - 2)), int(np.clip(np.floor(fy), 0, h - 2))
        tx, ty = fx - x0, fy - y0
        got = (
            p2c[y0, x0] * (1 - tx) * (1 - ty) + p2c[y0, x0 + 1] * tx * (1 - ty)
            + p2c[y0 + 1, x0] * (1 - tx) * ty + p2c[y0 + 1, x0 + 1] * tx * ty
        )  # fmt: skip
        want = apply_h(h_p2c, np.array([[cx, cy]]))[0]
        worst = max(worst, float(np.linalg.norm(got - want)))
    return worst


def _green_dominant(px: np.ndarray) -> bool:
    b, g, r = (int(v) for v in px[:3])
    return g > r + 40 and g > b + 40


def run_e2e(
    workdir: Path,
    *,
    publish_host: str | None = None,
    log: Callable[[str], None] = print,
) -> E2EResult:
    # 1. project
    project_dir = workdir / "e2e.fkproj"
    project_dir.mkdir(parents=True, exist_ok=True)
    for sub in SUBFOLDERS:
        (project_dir / sub).mkdir(exist_ok=True)
    project = Project(id="e2e", name="E2E")
    save_project_dir(project_dir, project)
    log("project created")

    # 2. scan
    scene = default_scene()
    display = SimDisplay(scene)
    camera = SimCamera(scene, display)
    cal = run_calibration(
        project_dir,
        display,
        camera,
        "sim",
        CalibSettings(discard_frames=0, average_frames=1),
        sleep=lambda s: None,
    )
    coverage = cal.quality.coverage if cal.quality is not None else 0.0
    project = load_project_dir(project_dir).model_copy(update={"calibration_id": cal.id})
    log(f"scan done, coverage {coverage * 100:.0f} %")

    # 3-4. door and surface
    proj_view = cv2.imread(str(project_dir / "calibration" / cal.id / "proj_view.png"))
    if proj_view is None:
        raise FkError(INTERNAL, "proj_view.png is missing")
    quad = detect_door_quad(proj_view)
    surface = Surface(
        id="garage_door",
        name="Garage door",
        quad=(quad[0], quad[1], quad[2], quad[3]),
        aspect=estimate_aspect(quad),
    )
    log("door detected")

    # 5. test image and clip
    asset_id = new_id("ast")
    image = np.zeros((360, 640, 3), np.uint8)
    image[:] = (255, 0, 255)
    image[20:-20, 20:-20] = (0, 255, 0)
    rel = f"media/{asset_id}.png"
    if not cv2.imwrite(str(project_dir / rel), image):
        raise FkError(INTERNAL, "Could not write test image")
    asset = Asset(
        id=asset_id, kind="image", filename=rel, original_name="e2e.png",
        width=640, height=360, duration_ms=None, fps=None,
    )  # fmt: skip
    clip = Clip(
        id=new_id("clip"), asset_id=asset_id, surface_id=surface.id,
        start_ms=0, duration_ms=2000, fit="stretch",
    )  # fmt: skip
    timeline = project.timeline.model_copy(
        update={"duration_ms": 2000, "tracks": [Track(id="trk_1", name="Video", clips=[clip])]}
    )
    project = project.model_copy(
        update={"surfaces": [surface], "assets": [asset], "timeline": timeline}
    )
    save_project_dir(project_dir, project)
    project = load_project_dir(project_dir)

    # 6. render
    render_path = project_dir / "renders" / f"{new_id('rnd')}.mp4"
    render_project(
        project_dir, "pi3-1080p30", render_path,
        progress=lambda f, m: log(f"render {f * 100:.0f} %"),
    )  # fmt: skip
    log(f"rendered {render_path}")

    # 7. verify
    with Compositor(project, project_dir) as comp:
        frame = comp.frame(1000)
    truth = np.array(scene.feature_quad_proj("garage_door"), dtype=np.float64)
    detected = np.array(quad, dtype=np.float64)
    err = float(np.max(np.linalg.norm(detected - truth, axis=1)))
    log(f"max corner error {err:.2f} px")
    p2c = np.load(project_dir / "calibration" / cal.id / "p2c.npy")
    map_err = _map_corner_error(p2c, scene.h_p2c, truth)
    log(f"  (a) detected door quad vs ground truth: {err:.2f} px")
    log(f"  (b) calibration map alone (p2c vs scene.h_p2c): {map_err:.2f} px")

    cam = scene.render_camera(frame, np.random.default_rng(scene.seed))
    centre = apply_h(scene.h_p2c, truth.mean(axis=0))[0]
    x0, y0, x1, y1 = scene.features_world["garage_door"]
    wall = apply_h(scene.h_w2c, np.array([[x0 - 60, (y0 + y1) / 2]]))[0]
    cx, cy = round(centre[0]), round(centre[1])
    wx, wy = round(wall[0]), round(wall[1])
    if not _green_dominant(cam[cy, cx]):
        raise FkError(INTERNAL, f"Door centre is not green in the camera view: {cam[cy, cx]}")
    if _green_dominant(cam[wy, wx]):
        raise FkError(INTERNAL, f"Wall beside the door is green in the camera view: {cam[wy, wx]}")
    log("camera colour checks passed")

    # 8. publish
    if publish_host is not None:
        from facadekit.fpp.publish import publish_render

        publish_render(
            publish_host, render_path, media_name="fk_e2e.mp4",
            playlist_name="FacadeKit - E2E", start=True,
            progress=lambda f, m: log(f"publish: {m}"),
        )  # fmt: skip
        log("published")

    load_calibration(project_dir, cal.id)
    return E2EResult(project_dir, render_path, err, coverage)
