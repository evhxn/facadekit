"""Verify surface: has the rig moved, and does a surface's light land on its object?

Entry point `run_verify`. It needs the projector and the camera that made the scan, takes three
pictures for the rig check (black, white, dots) and two more per surface (the surface filled,
dots inside it), and returns a `VerificationRecord`. docs/13-surface-verification.md explains
the method, the thresholds and the limits.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from facadekit.calib import rigcheck as rc
from facadekit.calib import surfacecheck as sc
from facadekit.calib.p2cmap import ProjCamMap, load_map
from facadekit.calib.pipeline import load_calibration
from facadekit.calib.verify_capture import (
    Exposure,
    Shooter,
    prepare_exposure,
    ratio_image,
)
from facadekit.devices.base import Camera, ProjectorDisplay
from facadekit.devices.shifted import ShiftedCamera
from facadekit.errors import (
    BAD_REQUEST,
    NOT_FOUND,
    RESOLUTION_MISMATCH,
    FkError,
)
from facadekit.geom.homography import apply_h
from facadekit.ids import new_id
from facadekit.models.calibration import Calibration, CalibSettings
from facadekit.models.jobs import utc_now_iso
from facadekit.models.project import Project, Surface
from facadekit.models.verification import (
    EdgeReport,
    RigCheck,
    SurfaceCheck,
    VerificationRecord,
)

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]

VERIFY_DIR = "verification"
RIG_RECORD = "rig"
REF_WHITE = "cam_white.png"  # written by the scan since this feature (calib/pipeline.py)
REF_BLACK = "cam_black.png"
DEFAULT_SETTLE_MS = 300


@dataclass
class Injection:
    """Self-test only: a known error put into the projected pictures (docs/13 "Self-test")."""

    proj_shift: tuple[float, float] = (0.0, 0.0)  # every projected picture moved, projector px
    grow_px: float = 0.0  # the surface outline grown outward (negative: shrunk), projector px
    cam_shift: tuple[float, float] = (0.0, 0.0)  # every camera picture moved, camera px

    @property
    def active(self) -> bool:
        return bool(self.grow_px) or self.proj_shift != (0.0, 0.0) or self.cam_shift != (0.0, 0.0)

    def as_dict(self) -> dict[str, float]:
        return {
            "proj_shift_x": self.proj_shift[0],
            "proj_shift_y": self.proj_shift[1],
            "grow_px": self.grow_px,
            "cam_shift_x": self.cam_shift[0],
            "cam_shift_y": self.cam_shift[1],
        }


@dataclass
class VerifyOutcome:
    record: VerificationRecord
    overlay: np.ndarray | None


def shot_settings(cal: Calibration, *, low_power: bool) -> CalibSettings:
    """The scan's own waits (measured for this camera when the scan tuned itself)."""
    s = cal.settings or CalibSettings()
    settle = s.settle_ms if s.settle_ms > 0 else DEFAULT_SETTLE_MS
    average = min(s.average_frames, 2 if low_power else 3)
    return CalibSettings(
        settle_ms=settle, discard_frames=min(s.discard_frames, 5), average_frames=max(1, average)
    )


def _shift_image(image: np.ndarray, shift: tuple[float, float]) -> np.ndarray:
    if shift == (0.0, 0.0):
        return image
    m = np.array([[1, 0, shift[0]], [0, 1, shift[1]]], dtype=np.float64)
    h, w = image.shape[:2]
    return np.asarray(cv2.warpAffine(image, m, (w, h), flags=cv2.INTER_LINEAR))


def grow_polygon(poly: np.ndarray, grow: float) -> np.ndarray:
    """Every side moved outward by `grow` projector px (sides re-intersected)."""
    if grow == 0:
        return poly
    normals = sc.outward_normals(poly)
    n = len(poly)
    lines = [(poly[i] + grow * normals[i], poly[(i + 1) % n] - poly[i]) for i in range(n)]
    out = []
    for i in range(n):
        p1, d1 = lines[(i - 1) % n]
        p2, d2 = lines[i]
        a = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]])
        if abs(np.linalg.det(a)) < 1e-9:
            out.append(p2)
            continue
        s = np.linalg.solve(a, p2 - p1)
        out.append(p1 + s[0] * d1)
    return np.asarray(out)


def reference_texture(
    folder: Path, pmap: ProjCamMap, cam_size: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray, str]:
    """The scan's picture of the lit wall in camera pixels, its lit area, and where it came
    from. Older scans did not keep the camera pictures; then the projector view is mapped
    back into the camera (less detail, same geometry)."""
    white = cv2.imread(str(folder / REF_WHITE), cv2.IMREAD_GRAYSCALE)
    black = cv2.imread(str(folder / REF_BLACK), cv2.IMREAD_GRAYSCALE)
    w, h = cam_size
    if white is not None and black is not None and white.shape == (h, w):
        contrast = white.astype(np.float32) - black.astype(np.float32)
        lit = contrast > max(12.0, 0.15 * float(np.percentile(contrast, 95)))
        return white.astype(np.float32), lit, "scan pictures"
    view = cv2.imread(str(folder / "proj_view.png"), cv2.IMREAD_GRAYSCALE)
    if view is None:
        raise FkError(NOT_FOUND, "The scan's projector view is missing.")
    img, covered = rc.splat_to_camera(view.astype(np.float32), pmap, cam_size)
    return img, covered & (img > 8), "projector view"


# --- rig ----------------------------------------------------------------------------------


@dataclass
class RigResult:
    check: RigCheck
    h_cam: np.ndarray | None  # reference camera -> current camera (wall texture)
    h_dots: np.ndarray | None  # reference camera -> current camera (dots: both moves)
    drift: np.ndarray | None  # old projector -> new projector, when the projector moved


def _messages(check: RigCheck) -> tuple[str, str]:
    cam = f"{check.camera_shift_px:.1f}" if check.camera_shift_px is not None else "?"
    proj = f"{check.projector_shift_px:.1f}" if check.projector_shift_px is not None else "?"
    total = f"{check.total_shift_px:.1f}" if check.total_shift_px is not None else "?"
    v = check.verdict
    if v == "stable":
        return (
            "Camera and projector have not moved since the scan.",
            "Nothing to do.",
        )
    if v == "camera_moved":
        return (
            f"The camera moved (about {cam} camera px). The projector did not.",
            "The show is not affected: surfaces are drawn in the projector's own view. The "
            "checks allow for the camera's new position. Scan again only if you want a fresh "
            "photo for Magic select.",
        )
    if v == "projector_moved":
        return (
            f"The projector moved: the picture landed about {proj} px from where the scan put it.",
            "Your surfaces will be off by that much. Use Setup > Projector moved? to scan "
            "again and shift every surface, or move the projector back.",
        )
    if v == "both_moved":
        return (
            f"Both moved: the camera about {cam} camera px, the projector about {proj} px.",
            "Use Setup > Projector moved? to scan again and shift every surface.",
        )
    if v == "moved":
        return (
            f"Something moved: the test dots are about {total} px from where the scan put "
            "them. The wall has too little texture to tell whether it was the camera or the "
            "projector.",
            "If you only touched the camera, the show is fine. Otherwise use Setup > "
            "Projector moved? to scan again.",
        )
    return (
        f"Could not judge: only {check.dots_found} of {check.dots_expected} test dots were found.",
        "Check that the camera still sees the projection. If the setup changed a lot, scan again.",
    )


def assess_rig(
    pmap: ProjCamMap,
    layout: rc.DotLayout,
    scale: float,
    observed: np.ndarray,
    cam_size: tuple[int, int],
    motion: rc.CameraMotion | None,
    lit: np.ndarray,
) -> RigResult:
    expected, ok = pmap.to_cam(layout.points)
    pts, expected = layout.points[ok], expected[ok]
    spacing_cam = layout.spacing * scale
    m = rc.match_dots(expected, observed, cam_size, spacing_cam)
    found = len(m.idx_expected)
    base = RigCheck(
        verdict="unknown",
        camera="unknown",
        projector="unknown",
        dots_expected=len(pts),
        dots_found=found,
        texture_matches=motion.inliers if motion is not None else 0,
        texture_method=motion.method if motion is not None else None,
    )
    if found < rc.MIN_DOTS or found < 0.4 * len(pts):
        base.message, base.recommendation = _messages(base)
        return RigResult(base, motion.h if motion else None, None, None)
    p = pts[m.idx_expected]
    e = expected[m.idx_expected]
    o = m.observed
    s = np.maximum(pmap.scale_at(p), 1e-3)
    m_fit = rc.fit_h(e, o)  # scan camera px -> current camera px, both moves together
    total = rc.shift_of(m_fit, e, o, s)
    base.total_shift_px = round(total, 3)
    frac = found / max(len(pts), 1)
    drift: np.ndarray | None = None
    if motion is None:
        moved = total > rc.PROJ_MOVED_PX
        base.verdict = "moved" if moved else "stable"
        base.camera = "unknown" if moved else "stable"
        base.projector = "unknown" if moved else "stable"
        margin = min(1.0, abs(total - rc.PROJ_MOVED_PX) / rc.PROJ_MOVED_PX)
        base.confidence = round(frac * (0.5 + 0.5 * margin) * (0.6 if moved else 1.0), 2)
    else:
        cam_shift = rc.mean_shift(motion.h, lit)
        r = apply_h(np.linalg.inv(motion.h), o)
        q, okq = pmap.to_proj(r)
        # what is left of the dots' motion once the camera's own motion is taken out
        if m_fit is not None:
            proj_only = np.linalg.inv(motion.h) @ m_fit
            proj_shift = rc.shift_of(proj_only, e, e, s)
        else:
            proj_shift = rc.shift_of(None, e, r, s)
        cam_moved = cam_shift > rc.CAM_MOVED_PX
        proj_moved = proj_shift > rc.PROJ_MOVED_PX
        base.camera_shift_px = round(cam_shift, 3)
        base.projector_shift_px = round(proj_shift, 3)
        base.camera = "moved" if cam_moved else "stable"
        base.projector = "moved" if proj_moved else "stable"
        base.verdict = (
            "both_moved"
            if cam_moved and proj_moved
            else "camera_moved"
            if cam_moved
            else "projector_moved"
            if proj_moved
            else "stable"
        )
        margins = [
            min(1.0, abs(cam_shift - rc.CAM_MOVED_PX) / rc.CAM_MOVED_PX),
            min(1.0, abs(proj_shift - rc.PROJ_MOVED_PX) / rc.PROJ_MOVED_PX),
        ]
        base.confidence = round(frac * (0.5 + 0.5 * min(margins)) * min(1.0, motion.score + 0.3), 2)
        if proj_moved and int(okq.sum()) >= 8:
            hh, _ = cv2.findHomography(q[okq], p[okq], cv2.RANSAC, 1.5)
            if hh is not None:
                drift = np.asarray(hh, dtype=np.float64)
                base.drift = [[float(v) for v in row] for row in drift]
    base.message, base.recommendation = _messages(base)
    return RigResult(base, motion.h if motion is not None else None, m.h, drift)


# --- surface ------------------------------------------------------------------------------


def _side_title(side: str) -> str:
    return side.capitalize()


def edge_advice(edge: sc.EdgeStats, usable: bool) -> str:
    title = _side_title(edge.side)
    spill, lo, hi = edge.spill, edge.spill_lo, edge.spill_hi
    if not usable or spill is None:
        return f"{title} edge: the object's edge could not be seen here; check it by eye."
    if lo is not None and hi is not None and lo < -sc.PASS_EDGE_PX and hi > sc.PASS_EDGE_PX:
        return (
            f"{title} edge is tilted against the object's edge: one end spills {hi:.0f} px, "
            f"the other stops {-lo:.0f} px short. Turn it to follow the object."
        )
    worst = edge.worst if edge.worst is not None else abs(spill)
    if spill > sc.PASS_EDGE_PX or (worst > sc.PASS_EDGE_PX and spill > 0):
        return f"{title} edge spills {max(spill, worst):.0f} px past the object: pull it in."
    if spill < -sc.PASS_EDGE_PX or (worst > sc.PASS_EDGE_PX and spill < 0):
        return (
            f"{title} edge stops {max(-spill, worst):.0f} px short of the object's edge: "
            "push it out."
        )
    return f"{title} edge: on the object's edge (within {worst:.1f} px)."


@dataclass
class SurfaceShots:
    fill_num: np.ndarray  # fill minus black, gray levels
    fill_q: np.ndarray
    fill_valid: np.ndarray
    inner_q: np.ndarray | None
    inner_valid: np.ndarray | None


def area_shares(
    shape: sc.Shape, samples: list[sc.EdgeSample]
) -> tuple[float | None, float | None, float | None]:
    """IoU of light and object, share of the light off the object, share of the object left
    dark, from the edge measurements (projector px): each sample stands for its share of its
    side. Measured this way, not by counting camera pixels, because the camera's blur makes a
    one- or two-pixel band at the object's edge ambiguous, which on a small picture is several
    percent of the area."""
    poly = shape.outer
    light_area = abs(sc._orientation(poly)) / 2
    for hole in shape.holes:
        light_area -= abs(sc._orientation(hole)) / 2
    if light_area <= 0:
        return None, None, None
    leak = short = 0.0
    seen = False
    for i in range(len(poly)):
        mine = [s.spill for s in samples if s.index == i and s.spill is not None]
        if not mine:
            continue
        seen = True
        length = float(np.linalg.norm(poly[(i + 1) % len(poly)] - poly[i]))
        step = length / len(mine)
        leak += sum(max(v, 0.0) for v in mine) * step
        short += sum(max(-v, 0.0) for v in mine) * step
    if not seen:
        return None, None, None
    object_area = max(light_area - leak + short, 1.0)
    inter = max(light_area - leak, 0.0)
    union = light_area + short
    return inter / union, min(leak / light_area, 1.0), min(short / object_area, 1.0)


def _landing(pmap: ProjCamMap, rig: RigResult) -> Callable[[np.ndarray], np.ndarray]:
    """Drawn projector px -> the scan's projector px where that light lands now."""
    if rig.drift is not None:
        inv = np.linalg.inv(rig.drift)
        return lambda pts: apply_h(inv, pts)
    if rig.check.verdict == "moved" and rig.h_dots is not None:
        # The camera's motion is unknown: assume the camera stayed and the projector moved.
        h = rig.h_dots

        def through_dots(pts: np.ndarray) -> np.ndarray:
            cam, _ = pmap.to_cam(pts)
            q, ok = pmap.to_proj(apply_h(h, cam))
            return np.where(ok[:, None], q, pts)

        return through_dots
    return lambda pts: np.asarray(pts, dtype=np.float64)


def assess_surface(
    pmap: ProjCamMap,
    surface: Surface,
    shape: sc.Shape,
    exposure: Exposure,
    shots: SurfaceShots,
    rig: RigResult,
    scale: float,
    inner_pts: np.ndarray,
    inner_radius: float,
    *,
    low_power: bool = False,
) -> tuple[SurfaceCheck, np.ndarray]:
    cam_h, cam_w = exposure.black.gray.shape
    cam_size = (cam_w, cam_h)
    h_cam = rig.h_cam
    intended = sc.intended_region(pmap, shape, cam_size, h_cam)
    area = float(intended.mask.sum())
    if area < 50:
        raise FkError(
            BAD_REQUEST,
            "This surface is too small in the camera picture to check, or lies where the scan "
            "measured nothing.",
        )
    search = float(np.clip(0.25 * np.sqrt(area), 12.0, 90.0))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(4 * search) | 1, int(4 * search) | 1))
    near = cv2.dilate(intended.mask.astype(np.uint8), k) > 0
    lit, norm = sc.lit_region(shots.fill_q, shots.fill_valid, near)
    albedo_f = exposure.white.bgr.astype(np.float32) - exposure.black.bgr.astype(np.float32)
    top = float(np.percentile(albedo_f[exposure.lit], 99)) if exposure.lit.any() else 255.0
    albedo = np.clip(albedo_f * (255.0 / max(top, 1.0)), 0, 255).astype(np.uint8)
    obj = sc.object_region(albedo, intended.mask, search, low_power=low_power)
    usable = obj.confidence >= sc.OBJECT_CONF_MIN
    obj_f = obj.mask.astype(np.float32) if usable else None
    landing = _landing(pmap, rig)
    lab = cv2.cvtColor(albedo, cv2.COLOR_BGR2LAB).astype(np.float32)
    data = sc.EdgeInputs(
        q=shots.fill_q,
        valid=shots.fill_valid
        & (
            exposure.white.gray - exposure.black.gray
            >= max(sc.STRONG_CONTRAST, 0.2 * exposure.median_contrast)
        ),
        num=shots.fill_num,
        norm=norm,
        obj=obj_f,
        lab=lab,
        search_cam=search,
    )
    samples = sc.edge_samples(pmap, shape, h_cam, landing, data)
    edges = sc.summarize_edges(samples)
    parallax = sc.parallax_spread(samples)
    inner_res: float | None = None
    if shots.inner_q is not None and shots.inner_valid is not None and len(inner_pts) >= 6:
        inner_res, _n = sc.inner_residual(
            pmap,
            inner_pts,
            shots.inner_q,
            shots.inner_valid,
            inner_radius * scale,
            rig.h_dots,
            cam_size,
        )

    i_m, l_m = intended.mask, lit
    union = float((i_m | l_m).sum())
    iou_int = float((i_m & l_m).sum()) / union if union > 0 else None
    iou_obj = leakage = uncovered = None
    if usable:
        iou_obj, leakage, uncovered = area_shares(shape, samples)
    worsts = [w for w in (e.worst for e in edges) if w is not None]
    max_edge = max(worsts) if worsts else None
    drifts = [abs(e.lit + (e.drift or 0.0)) for e in edges if e.lit is not None]
    max_drift = max(drifts) if drifts else None
    flat_warn = (parallax is not None and parallax > sc.PARALLAX_PX) or (
        inner_res is not None and inner_res > sc.INNER_PX
    )

    if usable and max_edge is not None:
        verdict = (
            "pass"
            if max_edge <= sc.PASS_EDGE_PX
            else "warn"
            if max_edge <= sc.WARN_EDGE_PX
            else "fail"
        )
        missing = [e.side for e in edges if e.spill is None]
        if missing and verdict == "pass":
            verdict = "warn"
    else:
        verdict = "fail" if max_drift is not None and max_drift > sc.WARN_EDGE_PX else "warn"
    if flat_warn and verdict == "pass":
        verdict = "warn"

    reports = [
        EdgeReport(
            side=e.side,  # type: ignore[arg-type]
            spill_px=round(e.spill, 2) if e.spill is not None else None,
            drift_px=round(e.drift, 2) if e.drift is not None else None,
            lit_px=round(e.lit, 2) if e.lit is not None else None,
            samples=e.samples,
            spill_range_px=(
                [round(e.spill_lo, 2), round(e.spill_hi, 2)]
                if e.spill_lo is not None and e.spill_hi is not None
                else None
            ),
            advice=edge_advice(e, usable),
        )
        for e in edges
    ]
    suggestion = sc.Suggestion(reason="")
    if verdict != "pass" and usable and not flat_warn:
        suggestion = sc.suggest(shape, np.asarray(surface.quad, dtype=np.float64), samples, edges)
    elif verdict != "pass" and flat_warn:
        suggestion = sc.Suggestion(
            reason="The edges moved by different amounts (the object is not in the wall's "
            "plane, or it moved), so no single correction fits. Adjust the corners by hand."
        )
    elif verdict != "pass":
        suggestion = sc.Suggestion(
            reason="The object's edges are not clear enough in the camera picture to "
            "suggest a correction."
        )

    if verdict == "pass":
        message = "The light lands on the object."
    elif not usable:
        message = (
            "The light lands where it was meant to, but the camera cannot make out the "
            "object's edges clearly enough to confirm they match."
            if max_drift is None or max_drift <= sc.PASS_EDGE_PX
            else "The light does not land where it was meant to, and the object's edges are "
            "unclear in the camera picture."
        )
    else:
        worst = max(edges, key=lambda e: abs(e.spill) if e.spill is not None else -1.0)
        message = (
            f"The light misses the object by up to {max_edge:.0f} px "
            f"({_side_title(worst.side).lower()} edge)."
            if max_edge is not None
            else "Part of the outline could not be measured."
        )
    if flat_warn:
        message += (
            " The edges moved by different amounts: the object may stick out from the wall "
            "or not be flat."
        )

    labels: list[tuple[str, np.ndarray]] = []
    for e in edges:
        mine = [s.proj for s in samples if s.side == e.side]
        if not mine or e.spill is None:
            continue
        at = sc.to_current(pmap, np.mean(np.array(mine), axis=0, keepdims=True), h_cam)[0]
        labels.append((f"{e.spill:+.0f}px", at))
    picture = sc.overlay_image(
        exposure.white.bgr, intended.mask, lit, obj.mask if usable else None, labels
    )
    check = SurfaceCheck(
        surface_id=surface.id,
        verdict=verdict,  # type: ignore[arg-type]
        iou_intended=_r(iou_int),
        iou_object=_r(iou_obj),
        leakage=_r(leakage),
        uncovered=_r(uncovered),
        max_edge_px=_r(max_edge),
        object_confidence=round(float(obj.confidence), 3),
        parallax_px=_r(parallax),
        inner_residual_px=_r(inner_res),
        flatness_warning=flat_warn,
        outline_measured=round(intended.coverage, 3),
        edges=reports,
        suggested_quad=suggestion.quad,
        suggested_mask=suggestion.mask,
        suggestion_note=suggestion.reason,
        message=message,
    )
    return check, picture


def _r(v: float | None, digits: int = 3) -> float | None:
    return round(float(v), digits) if v is not None else None


# --- orchestration ------------------------------------------------------------------------


def _scale_of(cal: Calibration, pmap: ProjCamMap) -> float:
    if cal.quality is not None and cal.quality.px_per_proj_px:
        return float(cal.quality.px_per_proj_px)
    w, h = pmap.proj_size
    return float(pmap.scale_at(np.array([[w / 2, h / 2]]))[0]) or 1.0


def run_verify(
    project_dir: Path,
    project: Project,
    display: ProjectorDisplay,
    camera: Camera,
    *,
    surface_id: str | None = None,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    low_power: bool = False,
    inject: Injection | None = None,
) -> VerifyOutcome:
    """Check the rig and, with `surface_id`, that surface. Opens and closes `camera`; leaves
    the projector black. Raises FkError for setups it cannot check."""

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    started = clock()
    if project.calibration_id is None:
        raise FkError(BAD_REQUEST, "Scan the house on the Setup page first.")
    cal = load_calibration(project_dir, project.calibration_id)
    if cal.mode != "scan":
        raise FkError(
            BAD_REQUEST,
            "This project uses manual mode, which has no camera measurements to check "
            "against. Scan the house to use Verify.",
        )
    surface: Surface | None = None
    if surface_id is not None:
        surface = next((s for s in project.surfaces if s.id == surface_id), None)
        if surface is None:
            raise FkError(NOT_FOUND, f"surface {surface_id!r} not found")
    pmap = load_map(project_dir, cal)
    size = display.size
    if size != pmap.proj_size:
        raise FkError(
            RESOLUTION_MISMATCH,
            f"Projector window is {size[0]}x{size[1]} but the scan is "
            f"{pmap.proj_size[0]}x{pmap.proj_size[1]}.",
        )
    inject = inject or Injection()
    if inject.cam_shift != (0.0, 0.0):
        camera = ShiftedCamera(camera, inject.cam_shift)
    settings = shot_settings(cal, low_power=low_power)
    shooter = Shooter(display, camera, settings, sleep=sleep, cancel=cancel)
    folder = project_dir / "calibration" / cal.id
    report(0.0, "Opening the camera")
    camera.open()
    try:
        cam_size = camera.resolution
        if cal.camera_width is not None and cal.camera_height is not None:
            if cam_size != (cal.camera_width, cal.camera_height):
                raise FkError(
                    BAD_REQUEST,
                    f"The camera picture is {cam_size[0]}x{cam_size[1]} but the scan was made "
                    f"at {cal.camera_width}x{cal.camera_height}. Use the camera and size of "
                    "the scan.",
                )
        report(0.05, "Checking the light")
        exposure = prepare_exposure(shooter, size)
        scale = _scale_of(cal, pmap)
        layout = rc.dot_layout(pmap, scale)
        report(0.3, "Projecting test dots")
        dots_img = rc.render_dots(size, layout.points, layout.radius)
        dots = shooter.shoot(_shift_image(dots_img, inject.proj_shift), "dots")
        q_dots, v_dots = ratio_image(dots, exposure, gain_mask=~exposure.lit)

        shape: sc.Shape | None = None
        fill_shot = inner_shot = None
        inner_pts = np.zeros((0, 2))
        if surface is not None:
            shape = sc.surface_shape(surface)
            if inject.grow_px:
                shape = sc.Shape(
                    outer=grow_polygon(shape.outer, inject.grow_px),
                    holes=shape.holes,
                    is_quad=shape.is_quad,
                )
            report(0.45, "Lighting the surface")
            fill = _shift_image(sc.fill_image(size, shape), inject.proj_shift)
            fill_shot = shooter.shoot(fill, "fill")
            inner_pts = sc.inner_dots(shape, layout.radius)
            if len(inner_pts) >= 6:
                report(0.6, "Dots on the surface")
                inner = _shift_image(
                    sc.inner_image(size, inner_pts, layout.radius), inject.proj_shift
                )
                inner_shot = shooter.shoot(inner, "inner")
        try:
            display.show(np.zeros((size[1], size[0]), np.uint8))  # leave the projector dark
        except FkError:
            pass
    finally:
        camera.close()

    report(0.7, "Comparing with the scan")
    ref_img, ref_lit, ref_source = reference_texture(folder, pmap, cam_size)
    ref_hp, ref_mask = rc.texture(ref_img, ref_lit)
    cur_hp, cur_mask = rc.texture(exposure.white.gray, exposure.lit)
    motion = rc.camera_motion(ref_hp, ref_mask, cur_hp, cur_mask)
    observed = rc.find_dots(q_dots, v_dots, layout.radius * scale)
    rig = assess_rig(pmap, layout, scale, observed, cam_size, motion, exposure.lit)
    log.info(
        "verify rig: %s (camera %s px, projector %s px, dots %d/%d, texture %s/%d, ref %s)",
        rig.check.verdict,
        rig.check.camera_shift_px,
        rig.check.projector_shift_px,
        rig.check.dots_found,
        rig.check.dots_expected,
        rig.check.texture_method,
        rig.check.texture_matches,
        ref_source,
    )

    surface_check: SurfaceCheck | None = None
    picture: np.ndarray | None = None
    if surface is not None and shape is not None and fill_shot is not None:
        report(0.85, "Measuring the edges")
        fq, fv = ratio_image(fill_shot, exposure, gain_mask=None)
        k = np.ones((41, 41), np.uint8)
        dark = ~(cv2.dilate((fq > 0.3).astype(np.uint8), k) > 0)
        fq, fv = ratio_image(fill_shot, exposure, gain_mask=dark)
        iq = iv = None
        if inner_shot is not None:
            iq, iv = ratio_image(inner_shot, exposure, gain_mask=dark)
        surface_check, picture = assess_surface(
            pmap,
            surface,
            shape,
            exposure,
            SurfaceShots(fill_shot.gray - exposure.black.gray, fq, fv, iq, iv),
            rig,
            scale,
            inner_pts,
            layout.radius,
            low_power=low_power,
        )
        log.info(
            "verify surface %s: %s (max edge %s px, leakage %s, uncovered %s, object conf %.2f)",
            surface.id,
            surface_check.verdict,
            surface_check.max_edge_px,
            surface_check.leakage,
            surface_check.uncovered,
            surface_check.object_confidence,
        )
    report(1.0, "Done")
    record = VerificationRecord(
        id=new_id("vrf"),
        created_at=utc_now_iso(),
        calibration_id=cal.id,
        camera_id=cal.camera_id or "",
        surface_id=surface.id if surface is not None else None,
        surface_quad=list(surface.quad) if surface is not None else None,
        surface_mask=surface.mask if surface is not None else None,
        level=round(exposure.level, 3),
        exposure_locked=exposure.locked,
        shots=shooter.shots,
        duration_s=round(clock() - started, 2),
        rig=rig.check,
        surface=surface_check,
        overlay=picture is not None,
        injected=inject.as_dict() if inject.active else None,
    )
    return VerifyOutcome(record=record, overlay=picture)


# --- records on disk ----------------------------------------------------------------------


def _record_name(surface_id: str | None) -> str:
    return surface_id if surface_id is not None else RIG_RECORD


def save_record(project_dir: Path, outcome: VerifyOutcome) -> None:
    folder = project_dir / VERIFY_DIR
    folder.mkdir(exist_ok=True)
    name = _record_name(outcome.record.surface_id)
    if outcome.overlay is not None:
        ok, buf = cv2.imencode(".jpg", outcome.overlay, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok:
            tmp = folder / f".{name}.jpg.tmp"
            tmp.write_bytes(buf.tobytes())
            tmp.replace(folder / f"{name}.jpg")
    tmp = folder / f".{name}.json.tmp"
    tmp.write_text(outcome.record.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(folder / f"{name}.json")


def load_record(project_dir: Path, surface_id: str | None) -> VerificationRecord:
    path = project_dir / VERIFY_DIR / f"{_record_name(surface_id)}.json"
    if not path.is_file():
        raise FkError(NOT_FOUND, "This surface has not been verified yet.")
    try:
        return VerificationRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise FkError(NOT_FOUND, "The saved verification is unreadable; verify again.") from exc


def overlay_path(project_dir: Path, surface_id: str | None) -> Path:
    return project_dir / VERIFY_DIR / f"{_record_name(surface_id)}.jpg"
