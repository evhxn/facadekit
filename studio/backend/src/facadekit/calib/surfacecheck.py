"""Does the light of a surface land on its object, as the camera sees it now?

Three regions in the current camera picture (docs/13-surface-verification.md):

* **I, intended**: the surface outline mapped through the scan's projector -> camera map (and
  through the camera's own motion, if it moved).
* **L, lit**: where the surface's light actually lands, from a ratio image (fill - black) /
  (white - black), thresholded at one half. The ratio cancels ambient light and the
  surface's colour and texture.
* **O, object**: the object itself, segmented in the reflectance picture (white - black,
  colour) with GrabCut, seeded from I. Independent of where the light went.

Edges are measured along outward normals: the lit edge's position against the intended edge
(did the light move?) and against the object's edge (does the light spill past the object or
stop short of it?). Numbers are reported in projector pixels, the unit of the surface editor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

from facadekit.calib.p2cmap import ProjCamMap, densify
from facadekit.calib.rigcheck import DotMatch, find_dots, match_dots, render_dots
from facadekit.geom.homography import apply_h
from facadekit.geom.mask import rasterize_mask
from facadekit.models.project import Mask, Surface

Point = tuple[float, float]
SIDES = ("top", "right", "bottom", "left")

# Thresholds in projector px (docs/13 section "Thresholds"; set from the train scenes).
PASS_EDGE_PX = 2.5  # every edge within this of the object's edge: pass
WARN_EDGE_PX = 6.0  # worst edge within this: warn; beyond: fail
PARALLAX_PX = 1.5  # spread of edge displacement left over after a common shift
INNER_PX = 1.5  # inner dots off the global drift by more than this: not in the wall's plane
OBJECT_CONF_MIN = 0.8  # object/background colour separation needed to trust O
MAX_FIX_FRACTION = 0.25  # a suggested edge move larger than this share of the surface: no fix
MAX_FIX_PX = 60.0
CORNER_SKIP = 0.15  # ignore this share of each side at both ends (rounded corners)
LIT_TOL_PX = 3.0  # "hybrid": measured light edge this close to the prediction: use the prediction
SPILL_SOURCE = "hybrid"  # light edge for the spill: "measured", "predicted" or "hybrid"
NEAR_EDGE_CAM_PX = 3.0  # light edge this close to a colour edge: the ratio crossing is pulled
STRONG_CONTRAST = 20.0  # gray levels of white minus black needed to trust a measured edge
SNAP_CAM_PX = 3.0
CORE_FRACTION = 0.35  # the region's core: eroded by this share of its inradius
COLOUR_K = 3.5  # object colour: within this many robust spreads of the core, every channel
COLOUR_SPREAD_MIN = 4.0  # Lab units; floor for a very even surface


@dataclass
class Shape:
    outer: np.ndarray  # (N, 2) projector px
    holes: list[np.ndarray]
    is_quad: bool


def surface_shape(surface: Surface) -> Shape:
    if surface.mask is not None:
        return Shape(
            outer=np.asarray(surface.mask.outer, dtype=np.float64),
            holes=[np.asarray(h, dtype=np.float64) for h in surface.mask.holes],
            is_quad=False,
        )
    return Shape(outer=np.asarray(surface.quad, dtype=np.float64), holes=[], is_quad=True)


def _orientation(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def outward_normals(poly: np.ndarray) -> np.ndarray:
    """Unit outward normal of each side i (from vertex i to i + 1), image coordinates."""
    d = np.roll(poly, -1, axis=0) - poly
    n = np.stack([d[:, 1], -d[:, 0]], axis=1)
    if _orientation(poly) < 0:
        n = -n
    return np.asarray(n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9))


def side_name(normal: np.ndarray) -> str:
    angle = float(np.degrees(np.arctan2(normal[1], normal[0])))
    if -45 <= angle < 45:
        return "right"
    if 45 <= angle < 135:
        return "bottom"
    if -135 <= angle < -45:
        return "top"
    return "left"


def fill_image(size: tuple[int, int], shape: Shape) -> np.ndarray:
    w, h = size
    soft = rasterize_mask(
        w,
        h,
        [(float(p[0]), float(p[1])) for p in shape.outer],
        [[(float(p[0]), float(p[1])) for p in hole] for hole in shape.holes],
    )
    return np.asarray(np.rint(soft * 255).astype(np.uint8))


def _fill_cam(points: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    img = np.zeros((h, w), np.uint8)
    pts = np.round((points - 0.5) * 16).astype(np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(img, [pts], 255, cv2.LINE_8, 4)
    return img > 0


@dataclass
class Intended:
    mask: np.ndarray  # bool camera picture
    outline: np.ndarray  # (M, 2) camera px, current camera
    coverage: float  # share of the outline the scan measured


def to_current(pmap: ProjCamMap, pts: np.ndarray, h_cam: np.ndarray | None) -> np.ndarray:
    cam, _ = pmap.to_cam(pts)
    return apply_h(h_cam, cam) if h_cam is not None else cam


def intended_region(
    pmap: ProjCamMap, shape: Shape, cam_size: tuple[int, int], h_cam: np.ndarray | None
) -> Intended:
    dense = densify(shape.outer, 2.0)
    cam, valid = pmap.to_cam(dense)
    if h_cam is not None:
        cam = apply_h(h_cam, cam)
    mask = _fill_cam(cam, cam_size)
    for hole in shape.holes:
        hc = to_current(pmap, densify(hole, 2.0), h_cam)
        mask &= ~_fill_cam(hc, cam_size)
    return Intended(mask=mask, outline=cam, coverage=float(valid.mean()) if len(valid) else 0.0)


def lit_region(q: np.ndarray, valid: np.ndarray, near: np.ndarray) -> tuple[np.ndarray, float]:
    """Pixels the fill lights (ratio > 0.5 of the white level), connected to `near`."""
    sel = (q > 0.5) & valid & near
    norm = float(np.median(q[sel])) if int(sel.sum()) > 50 else 1.0
    norm = float(np.clip(norm, 0.5, 2.0))
    lit = ((q / norm) > 0.5) & valid
    n, labels = cv2.connectedComponents(lit.astype(np.uint8), connectivity=8)
    if n <= 1:
        return lit, norm
    touch = np.unique(labels[near & lit])
    keep = np.isin(labels, touch[touch > 0])
    return np.asarray(keep), norm


@dataclass
class ObjectRegion:
    mask: np.ndarray  # bool
    confidence: float  # colour separation from the surroundings, 0 = none
    contrast: float


def _lab(bgr: np.ndarray) -> np.ndarray:
    return np.asarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32))


def object_region(
    albedo: np.ndarray,
    intended: np.ndarray,
    search_px: float,
    *,
    low_power: bool = False,
) -> ObjectRegion:
    """The object under the intended region, found by its colour in the reflectance picture:
    the pixels connected to the region's core whose colour stays close to the core's (robust
    per-channel median and spread, so a texture on the object is allowed), within the search
    band. A dark frame around a light door, or the brick beyond it, is not the door.

    (GrabCut was tried first: seeded from a too-large outline it kept the dark frame as part of
    the object in about half the samples; docs/13.)"""
    del low_power  # the colour rule is cheap; kept for the call signature
    h, w = intended.shape
    empty = ObjectRegion(np.zeros_like(intended), 0.0, 0.0)
    if int(intended.sum()) < 50:
        return empty
    r = max(int(round(search_px)), 4)
    inten = intended.astype(np.uint8)
    k_far = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (4 * r + 1, 4 * r + 1))
    far = cv2.dilate(inten, k_far) > 0
    inradius = float(cv2.distanceTransform(inten, cv2.DIST_L2, 5).max())
    core_r = max(2, int(round(CORE_FRACTION * inradius)))
    k_core = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * core_r + 1, 2 * core_r + 1))
    core = cv2.erode(inten, k_core) > 0
    if int(core.sum()) < 20:
        return empty
    lab = _lab(albedo)
    vals = lab[core]
    med = np.median(vals, axis=0)
    spread = 1.4826 * np.median(np.abs(vals - med), axis=0)
    spread = np.maximum(spread, COLOUR_SPREAD_MIN)
    close = np.all(np.abs(lab - med) <= COLOUR_K * spread, axis=2) & far
    close8 = cv2.morphologyEx(close.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    close8 = cv2.morphologyEx(close8, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels = cv2.connectedComponents(close8, connectivity=8)
    best, best_overlap = 0, 0
    for i in range(1, n):
        overlap = int((core & (labels == i)).sum())
        if overlap > best_overlap:
            best, best_overlap = i, overlap
    if best == 0:
        return empty
    comp = (labels == best).astype(np.uint8)
    flood = comp.copy()
    pad = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, pad, (0, 0), 1)
    mask = (comp | (1 - flood)) > 0  # holes (a window in a door, texture blotches) filled
    inside = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    band = (cv2.dilate(mask.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0) & ~(
        cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    )
    if int(inside.sum()) < 20 or int(band.sum()) < 20:
        return ObjectRegion(mask, 0.0, 0.0)
    a, b = lab[inside], lab[band]
    delta = float(np.linalg.norm(np.median(a, axis=0) - np.median(b, axis=0)))
    sp = float(np.mean(np.std(a, axis=0)) + np.mean(np.std(b, axis=0)))
    conf = delta / (sp + 2.0)
    area_ratio = float(mask.sum()) / max(float(intended.sum()), 1.0)
    if area_ratio < 0.4 or area_ratio > 2.5 or bool(mask[~far].any()):
        conf = 0.0
    # the object ran up to the edge of the search band: it is bigger than the check can see
    edge_of_band = far & ~(cv2.erode(far.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    if float((mask & edge_of_band).sum()) > 0.02 * float(edge_of_band.sum()):
        conf = 0.0
    return ObjectRegion(mask=mask, confidence=conf, contrast=delta)


def _crossing(profile: np.ndarray, t: np.ndarray, level: float) -> float | None:
    """Outward (inside >= level -> outside < level) crossing nearest t = 0, linear sub-pixel."""
    above = profile >= level
    idx = np.nonzero(above[:-1] & ~above[1:])[0]
    if len(idx) == 0:
        return None
    pos = []
    for i in idx:
        a, b = profile[i], profile[i + 1]
        frac = (a - level) / (a - b) if a != b else 0.5
        pos.append(t[i] + frac * (t[i + 1] - t[i]))
    arr = np.asarray(pos)
    return float(arr[np.argmin(np.abs(arr))])


@dataclass
class EdgeSample:
    side: str
    proj: np.ndarray  # projector point on the outline
    normal_proj: np.ndarray
    drift: float  # predicted light edge - intended edge (rig motion here), projector px
    lit: float | None  # measured light edge - predicted light edge, projector px
    spill: float | None  # light edge - object edge, projector px (+ light past the object)
    near_edge: bool = False  # the light edge lies on the object's colour edge (ratio is biased)
    u: float = 0.5  # position along its side, 0..1
    index: int = 0  # which side of the outline (vertex index -> index + 1)


@dataclass
class EdgeStats:
    side: str
    spill: float | None  # median along the side
    drift: float | None
    lit: float | None
    samples: int
    spill_lo: float | None = None  # 10th percentile along the side
    spill_hi: float | None = None  # 90th percentile

    @property
    def worst(self) -> float | None:
        if self.spill_lo is None or self.spill_hi is None:
            return None
        return max(abs(self.spill_lo), abs(self.spill_hi))


@dataclass
class EdgeInputs:
    """What the edge measurement needs besides the outline."""

    q: np.ndarray  # ratio image of the fill shot
    valid: np.ndarray  # where the ratio is clear enough to locate the light's edge
    num: np.ndarray | None  # fill minus black, gray levels
    norm: float  # ratio level of fully lit pixels
    obj: np.ndarray | None  # float32 object mask (None: object not usable)
    lab: np.ndarray | None  # reflectance picture in Lab, for snapping to the object's edge
    search_cam: float  # half-length of the search along the normal, camera px


def edge_samples(
    pmap: ProjCamMap,
    shape: Shape,
    h_cam: np.ndarray | None,
    landing: Callable[[np.ndarray], np.ndarray],
    data: EdgeInputs,
) -> list[EdgeSample]:
    """Measure along outward normals at points of the outline. Results in projector px.

    `landing(p)` gives, for drawn projector points p, the scan's projector coordinates of where
    their light lands now (p itself when the projector has not moved). Everything is compared in
    those coordinates: the object's edge, found in the camera picture, is mapped back through the
    scan's map (and the camera's own motion `h_cam`), so the camera's viewing angle and a depth
    step at the object's edge do not distort the numbers.

    The light edge is predicted (from `landing`) and checked against the lit picture. The
    measured edge replaces the prediction only where it is trustworthy (the ratio is valid all
    the way) and disagrees by more than LIT_TOL_PX.
    """
    poly = shape.outer
    normals = outward_normals(poly)
    hgt, wid = data.q.shape
    inv_cam = np.linalg.inv(h_cam) if h_cam is not None else None
    tt = np.arange(-data.search_cam, data.search_cam + 1e-9, 0.5)
    rows: list[dict[str, object]] = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        length = float(np.linalg.norm(b - a))
        if length < 8:
            continue
        n_samples = int(np.clip(length / 6, 4, 40))
        ts = np.linspace(CORNER_SKIP, 1 - CORNER_SKIP, n_samples)
        pts = a[None, :] + ts[:, None] * (b - a)[None, :]
        nrm = normals[i]
        q_pred = landing(pts)
        c0 = to_current(pmap, q_pred, h_cam)
        c1 = to_current(pmap, q_pred + nrm, h_cam)
        _, valid = pmap.to_cam(q_pred)
        side = side_name(nrm)
        for k in range(n_samples):
            if not valid[k]:
                continue
            nc = c1[k] - c0[k]
            s = float(np.linalg.norm(nc))  # camera px per projector px along the normal
            if s < 1e-3:
                continue
            nc = nc / s
            px = c0[k][0] + tt * nc[0] - 0.5
            py = c0[k][1] + tt * nc[1] - 0.5
            if px.min() < 0 or py.min() < 0 or px.max() > wid - 1 or py.max() > hgt - 1:
                continue
            mx = np.ascontiguousarray(px.reshape(1, -1), dtype=np.float32)
            my = np.ascontiguousarray(py.reshape(1, -1), dtype=np.float32)
            prof = cv2.remap(data.q, mx, my, cv2.INTER_LINEAR).reshape(-1) / data.norm
            vprof = cv2.remap(data.valid.astype(np.float32), mx, my, cv2.INTER_LINEAR).reshape(-1)
            t_lit = _crossing(prof, tt, 0.5)
            t_obj: float | None = None
            if data.obj is not None:
                oprof = cv2.remap(data.obj, mx, my, cv2.INTER_LINEAR).reshape(-1)
                t_obj = _crossing(oprof, tt, 0.5)
                if t_obj is not None and data.lab is not None:
                    t_obj = _snap(data.lab, mx, my, tt, t_obj)
            # The measured edge counts only where the surface is bright enough under the
            # white picture all the way from the prediction to it (a dark frame or a black
            # shutter gives a noisy ratio and almost no light to see).
            reliable = False
            if t_lit is not None:
                lo, hi = min(0.0, t_lit) - 2.0, max(0.0, t_lit) + 2.0
                span = (tt >= lo) & (tt <= hi)
                reliable = bool(np.all(vprof[span] > 0.99))
            near_edge = False
            if reliable and t_lit is not None and t_obj is not None:
                near_edge = abs(t_lit - t_obj) < NEAR_EDGE_CAM_PX
                if near_edge and data.num is not None:
                    t_num = _half_level(data.num, mx, my, tt, t_obj)
                    if t_num is not None:
                        t_lit = t_num
            rows.append(
                {
                    "side": side,
                    "p": pts[k],
                    "n": nrm,
                    "u": float(ts[k]),
                    "i": i,
                    "q_pred": q_pred[k],
                    "c0": c0[k],
                    "nc": nc,
                    "s": s,
                    "t_lit": t_lit if reliable else None,
                    "t_obj": t_obj,
                    "near": near_edge,
                }
            )
    if not rows:
        return []

    def back(cam_pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c = apply_h(inv_cam, cam_pts) if inv_cam is not None else cam_pts
        return pmap.to_proj(c)

    c_obj = np.array([r["c0"] + (r["t_obj"] or 0.0) * r["nc"] for r in rows])  # type: ignore[operator]
    c_lit = np.array([r["c0"] + (r["t_lit"] or 0.0) * r["nc"] for r in rows])  # type: ignore[operator]
    q_obj, ok_obj = back(c_obj)
    q_lit, ok_lit = back(c_lit)
    out: list[EdgeSample] = []
    for j, r in enumerate(rows):
        n = np.asarray(r["n"])
        q_pred = np.asarray(r["q_pred"])
        p = np.asarray(r["p"])
        drift = float(n @ (q_pred - p))
        lit: float | None = None
        if r["t_lit"] is not None and ok_lit[j]:
            lit = float(n @ (q_lit[j] - q_pred))
        spill: float | None = None
        if r["t_obj"] is not None and ok_obj[j]:
            light = q_pred
            if SPILL_SOURCE == "measured" and lit is not None:
                light = q_lit[j]
            elif SPILL_SOURCE == "hybrid" and lit is not None and abs(lit) > LIT_TOL_PX:
                light = q_lit[j]
            spill = float(n @ (light - q_obj[j]))
        out.append(
            EdgeSample(
                str(r["side"]),
                p,
                n,
                drift,
                lit,
                spill,
                bool(r["near"]),
                float(r["u"]),  # type: ignore[arg-type]
                int(r["i"]),  # type: ignore[call-overload]
            )
        )
    return out


def _half_level(
    num: np.ndarray, mx: np.ndarray, my: np.ndarray, tt: np.ndarray, t_obj: float
) -> float | None:
    """Light edge from the plain lit-minus-black picture, where it ends at the object's own
    colour edge: half of the level just inside the object. Unlike the ratio, this is not pulled
    across a colour edge by the camera's blur (the light outside is zero, whatever the colour
    there)."""
    prof = cv2.remap(num, mx, my, cv2.INTER_LINEAR).reshape(-1)
    inside = (tt >= t_obj - 8.0) & (tt <= t_obj - 3.0)
    if int(inside.sum()) < 3:
        return None
    level = float(np.median(prof[inside]))
    if level < 4.0:
        return None
    t = _crossing(prof, tt - t_obj, 0.5 * level)
    return t_obj + t if t is not None else None


def _snap(lab: np.ndarray, mx: np.ndarray, my: np.ndarray, tt: np.ndarray, t_obj: float) -> float:
    """Move the segmentation's edge to the strongest colour change within SNAP_CAM_PX
    (sub-pixel peak). GrabCut's edge leans towards where it was seeded."""
    raw = cv2.remap(lab, mx, my, cv2.INTER_LINEAR).reshape(-1, 3).astype(np.float64)
    prof = np.asarray(cv2.GaussianBlur(raw, (1, 5), 0, sigmaY=1.0), dtype=np.float64)
    grad = np.linalg.norm(np.gradient(prof, axis=0), axis=1)
    step = float(tt[1] - tt[0])
    window = np.nonzero(np.abs(tt - t_obj) <= SNAP_CAM_PX)[0]
    if len(window) < 3:
        return t_obj
    k = int(window[np.argmax(grad[window])])
    if k in (0, len(grad) - 1) or grad[k] < 0.25 * float(grad.max()) or grad[k] < 2.0 * step:
        return t_obj
    a, b, c = grad[k - 1], grad[k], grad[k + 1]
    denom = a - 2 * b + c
    frac = 0.5 * (a - c) / denom if denom < 0 else 0.0
    return float(tt[k] + float(np.clip(frac, -0.5, 0.5)) * step)


def _ends(u: np.ndarray, v: np.ndarray) -> tuple[float, float]:
    """Spill along a side as a straight line (a shifted or turned edge is linear in u),
    fitted robustly (Huber weights), read at the 10th and 90th percentile of the measured
    positions: the side's two ends without the noise of single samples."""
    if len(u) < 4 or float(np.ptp(u)) < 0.2:
        med = float(np.median(v))
        return med, med
    a = np.stack([np.ones_like(u), u - 0.5], axis=1)
    w = np.ones_like(v)
    coef = np.array([float(np.median(v)), 0.0])
    for _ in range(8):
        sw = np.sqrt(w)
        coef, *_ = np.linalg.lstsq(a * sw[:, None], v * sw, rcond=None)
        r = v - a @ coef
        scale = 1.4826 * float(np.median(np.abs(r))) + 1e-6
        k = 1.5 * scale
        w = np.where(np.abs(r) <= k, 1.0, k / np.maximum(np.abs(r), 1e-9))
    ends = [coef[0] + coef[1] * (float(np.percentile(u, q)) - 0.5) for q in (10, 90)]
    return float(min(ends)), float(max(ends))


def _median(values: list[float], least: int = 3) -> float | None:
    return float(np.median(values)) if len(values) >= least else None


def summarize_edges(samples: list[EdgeSample]) -> list[EdgeStats]:
    stats: list[EdgeStats] = []
    for side in SIDES:
        mine = [s for s in samples if s.side == side]
        if not mine:
            continue
        spills = [s.spill for s in mine if s.spill is not None]
        lo = hi = None
        if len(spills) >= 3:
            us = [s.u for s in mine if s.spill is not None]
            lo, hi = _ends(np.asarray(us), np.asarray(spills))
        stats.append(
            EdgeStats(
                side=side,
                spill=_median(spills),
                drift=_median([s.drift for s in mine]),
                lit=_median([s.lit for s in mine if s.lit is not None]),
                samples=len(mine),
                spill_lo=lo,
                spill_hi=hi,
            )
        )
    return stats


def parallax_spread(samples: list[EdgeSample]) -> float | None:
    """How unevenly the measured light edge sits against its prediction along the outline:
    robust spread (MAD, as a standard deviation) of the offsets left after the best common
    shift, projector px. A uniform offset (the colour-edge pull, a uniform dilation) does not
    count."""
    rows = [(s.normal_proj, s.lit) for s in samples if s.lit is not None and not s.near_edge]
    if len(rows) < 8:
        return None
    a = np.array([r[0] for r in rows])
    d = np.array([r[1] for r in rows], dtype=np.float64)
    t, *_ = np.linalg.lstsq(a, d, rcond=None)
    resid = d - a @ t
    return float(1.4826 * np.median(np.abs(resid - np.median(resid))))


# --- inner dots: is the surface in the same plane as the wall? -------------------------


def inner_dots(shape: Shape, radius: float) -> np.ndarray:
    poly = shape.outer
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    span = min(x1 - x0, y1 - y0)
    spacing = max(5 * radius, span / 5)
    xs = np.arange(x0 + spacing / 2, x1, spacing)
    ys = np.arange(y0 + spacing / 2, y1, spacing)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    contour = poly.astype(np.float32).reshape(-1, 1, 2)
    keep = [
        cv2.pointPolygonTest(contour, (float(x), float(y)), True) > 2 * radius + 2 for x, y in pts
    ]
    return np.asarray(pts[np.asarray(keep, dtype=bool)]) if len(pts) else np.zeros((0, 2))


def inner_residual(
    pmap: ProjCamMap,
    pts: np.ndarray,
    q: np.ndarray,
    valid: np.ndarray,
    radius_cam: float,
    predict: np.ndarray | None,
    cam_size: tuple[int, int],
) -> tuple[float | None, int]:
    """RMS (projector px) of inner dots against the wall-wide motion `predict`."""
    if len(pts) < 6:
        return None, 0
    expected, ok = pmap.to_cam(pts)
    expected, pts = expected[ok], pts[ok]
    if len(pts) < 6:
        return None, 0
    if predict is not None:
        expected = apply_h(predict, expected)
    observed = find_dots(q, valid, radius_cam)
    spacing_cam = float(np.median(np.linalg.norm(np.diff(expected, axis=0), axis=1)))
    m: DotMatch = match_dots(expected, observed, cam_size, max(spacing_cam, 8.0))
    if len(m.idx_expected) < 6:
        return None, len(m.idx_expected)
    scale = pmap.scale_at(pts[m.idx_expected])
    d = np.linalg.norm(m.observed - expected[m.idx_expected], axis=1) / np.maximum(scale, 1e-3)
    return float(np.sqrt(np.mean(d**2))), len(m.idx_expected)


def inner_image(size: tuple[int, int], pts: np.ndarray, radius: float) -> np.ndarray:
    return render_dots(size, pts, radius)


# --- suggested correction ---------------------------------------------------------------


def _line_intersect(p1: np.ndarray, d1: np.ndarray, p2: np.ndarray, d2: np.ndarray) -> np.ndarray:
    a = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]])
    s = np.linalg.solve(a, p2 - p1)
    return np.asarray(p1 + s[0] * d1)


def shifted_quad(quad: np.ndarray, moves: list[float]) -> np.ndarray | None:
    """Move side i of `quad` outward by moves[i] projector px and re-intersect the sides."""
    normals = outward_normals(quad)
    lines = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        lines.append((a + moves[i] * normals[i], b - a))
    try:
        out = np.array(
            [
                _line_intersect(lines[(i - 1) % 4][0], lines[(i - 1) % 4][1], *lines[i])
                for i in range(4)
            ]
        )
    except np.linalg.LinAlgError:
        return None
    return out


def convex(poly: np.ndarray) -> bool:
    n = len(poly)
    signs = []
    for i in range(n):
        a, b, c = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
        signs.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    s = np.sign(signs)
    return bool(np.all(s > 0) or np.all(s < 0))


@dataclass
class Suggestion:
    quad: list[Point] | None = None
    mask: Mask | None = None
    reason: str = ""


def _fit_line(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Total least squares line, re-weighted (Huber) so a few bad samples do not tilt it."""
    w = np.ones(len(points))
    centre, direction = points.mean(axis=0), np.array([1.0, 0.0])
    for _ in range(8):
        centre = (points * w[:, None]).sum(axis=0) / w.sum()
        d = (points - centre) * np.sqrt(w)[:, None]
        _, _, vt = np.linalg.svd(d)
        direction = vt[0]
        normal = np.array([-direction[1], direction[0]])
        r = np.abs((points - centre) @ normal)
        k = 1.5 * (1.4826 * float(np.median(r)) + 0.05)
        w = np.where(r <= k, 1.0, k / np.maximum(r, 1e-9))
    return centre, direction


def suggest_outline(
    poly: np.ndarray, samples: list[EdgeSample], edges: list[EdgeStats]
) -> np.ndarray | str:
    """Each side of `poly` re-fitted through the object's edge points measured along it (a
    shift and a tilt per side; sides with too few points are shifted by their direction's
    median), then neighbouring sides re-intersected. Returns the new outline, or why there is
    none. Only moderate changes are offered."""
    worst = [e.worst for e in edges if e.worst is not None]
    if not worst or max(worst) <= PASS_EDGE_PX:
        return "Nothing to fix."
    n = len(poly)
    normals = outward_normals(poly)
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    limit = min(MAX_FIX_PX, MAX_FIX_FRACTION * float(min(x1 - x0, y1 - y0)))
    if max(worst) > limit:
        return "The needed change is too large to apply automatically."
    by_side = {e.side: e.spill for e in edges}
    lines: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        direction = (b - a) / max(float(np.linalg.norm(b - a)), 1e-9)
        mine = [s for s in samples if s.index == i and s.spill is not None]
        if len(mine) >= 4:
            pts = np.array([s.proj - (s.spill or 0.0) * s.normal_proj for s in mine])
            centre, d = _fit_line(pts)
            lines.append((centre, d if float(d @ direction) >= 0 else -d))
        else:
            med = by_side.get(side_name(normals[i])) or 0.0
            lines.append((a - med * normals[i], direction))
    out = []
    for i in range(n):
        p1, d1 = lines[(i - 1) % n]
        p2, d2 = lines[i]
        if abs(float(d1[0] * d2[1] - d1[1] * d2[0])) < 0.05:  # nearly straight: keep the corner
            out.append(p2 + float((poly[i] - p2) @ d2) * d2)  # on the new line, opposite the old
            continue
        out.append(_line_intersect(p1, d1, p2, d2))
    new = np.array(out)
    if float(np.max(np.linalg.norm(new - poly, axis=1))) > 2 * limit:
        return "No clean shape fits the measured edges."
    if n == 4 and not convex(new):
        return "No clean four-corner shape fits the measured edges."
    if abs(_orientation(new)) < 0.5 * abs(_orientation(poly)):
        return "No clean shape fits the measured edges."
    return new


def suggest(
    shape: Shape, quad: np.ndarray, samples: list[EdgeSample], edges: list[EdgeStats]
) -> Suggestion:
    fitted = suggest_outline(shape.outer, samples, edges)
    if isinstance(fitted, str):
        return Suggestion(reason=fitted)
    if shape.is_quad:
        return Suggestion(quad=[(float(x), float(y)) for x, y in fitted])
    # A masked surface: the mask follows the measured edges; its placement quad moves by
    # the median of each direction.
    moves = []
    for nrm in outward_normals(quad):
        med = next((e.spill for e in edges if e.side == side_name(nrm)), None)
        moves.append(-(med or 0.0))
    new_quad = shifted_quad(quad, moves)
    if new_quad is None or not convex(new_quad):
        return Suggestion(reason="No clean four-corner shape fits the measured edges.")
    return Suggestion(
        quad=[(float(x), float(y)) for x, y in new_quad],
        mask=Mask(
            outer=[(float(x), float(y)) for x, y in fitted],
            holes=[[(float(p[0]), float(p[1])) for p in h] for h in shape.holes],
        ),
    )


# --- picture for the user ---------------------------------------------------------------


@dataclass
class Overlay:
    image: np.ndarray  # BGR uint8
    notes: list[str] = field(default_factory=list)


def overlay_image(
    photo: np.ndarray,
    intended: np.ndarray,
    lit: np.ndarray,
    obj: np.ndarray | None,
    labels: list[tuple[str, np.ndarray]],
    max_width: int = 720,
) -> np.ndarray:
    """Camera picture cropped to the surface: green = lit object, red = light on the
    background (spill), blue = object left dark, white line = where the light should be,
    yellow line = the object's outline."""
    h, w = intended.shape
    region = intended | lit | (obj if obj is not None else np.zeros_like(intended))
    ys, xs = np.nonzero(region)
    if len(xs) == 0:
        x0, y0, x1, y1 = 0, 0, w, h
    else:
        pad = int(0.25 * max(xs.max() - xs.min(), ys.max() - ys.min())) + 10
        x0, x1 = max(int(xs.min()) - pad, 0), min(int(xs.max()) + pad, w)
        y0, y1 = max(int(ys.min()) - pad, 0), min(int(ys.max()) + pad, h)
    base = (photo.astype(np.float32) * 0.55).astype(np.float32)
    tint = base.copy()
    if obj is not None:
        tint[lit & obj] = (40, 200, 40)
        tint[lit & ~obj] = (40, 40, 230)
        tint[obj & ~lit] = (230, 120, 30)
    else:
        tint[lit] = (40, 200, 200)
    out = cv2.addWeighted(base, 0.45, tint, 0.55, 0).astype(np.uint8)
    for mask, colour in ((intended, (255, 255, 255)), (obj, (0, 220, 255))):
        if mask is None:
            continue
        contours, _ = cv2.findContours(
            mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(out, contours, -1, colour, 1, cv2.LINE_AA)
    crop = np.ascontiguousarray(out[y0:y1, x0:x1])
    factor = min(1.0, max_width / max(crop.shape[1], 1))
    if factor < 1.0 or crop.shape[1] < 360:
        factor = factor if factor < 1.0 else min(2.0, 360 / max(crop.shape[1], 1))
        crop = np.asarray(
            cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA),
            dtype=np.uint8,
        )
    for text, at in labels:
        p = ((at - np.array([x0, y0])) * factor).astype(int)
        p[0] = int(np.clip(p[0], 2, crop.shape[1] - 120))
        p[1] = int(np.clip(p[1], 14, crop.shape[0] - 4))
        cv2.putText(
            crop,
            text,
            (int(p[0]), int(p[1])),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            crop,
            text,
            (int(p[0]), int(p[1])),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return crop
