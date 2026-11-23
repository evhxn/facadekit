"""3D simulator for calib3d (spike S-11 Part A, docs/15 and docs/18).

A scene is a few analytic surfaces (rectangles and a cylinder segment) in metres, an explicit
projector (pinhole, known intrinsics and pose), a colour camera that photographs the Gray-code
patterns, and a simulated depth sensor in the style of a phone LiDAR. Everything is seeded.

**Every sensor number here is ASSUMED.** No real LiDAR data was available when this was
written; the depth-sensor defaults below are guesses from public descriptions of phone LiDAR
(about 256x192 depth, centimetre-level noise, about 5 m range, poor on dark, glossy and
transparent surfaces). Results from this simulator are statements about the simulator, never
about a device.

World frame: x right, y up, z out of the wall towards the viewer; the wall is z = 0.
Cameras and the projector use OpenCV axes (``calib3d.geometry``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

from facadekit.calib.patterns import PatternSpec, pattern_sequence, render_pattern
from facadekit.calib3d.capture_format import CaptureWriter
from facadekit.calib3d.geometry import (
    Intrinsics,
    look_at,
    make_pose,
    pixel_rays,
    pose_inverse,
    project,
    rot_axis,
)

DepthBehaviour = Literal["matte", "dark", "glossy", "transparent"]
SceneKind = Literal["flat", "board", "box", "cylinder", "stepped"]
SCENE_KINDS: tuple[SceneKind, ...] = ("flat", "board", "box", "cylinder", "stepped")
UP = np.array([0.0, 1.0, 0.0])


@dataclass(frozen=True)
class Material:
    albedo: float
    depth: DepthBehaviour = "matte"


@dataclass
class Rect:
    centre: np.ndarray
    u: np.ndarray  # unit, in-plane
    v: np.ndarray  # unit, in-plane; normal = u x v faces the viewer
    hu: float
    hv: float
    material: Material
    obj: str

    @property
    def normal(self) -> np.ndarray:
        return np.asarray(np.cross(self.u, self.v))

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        n = self.normal
        denom = d @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((self.centre - o) @ n) / denom
        p = o + t[:, None] * d
        rel = p - self.centre
        ok = (np.abs(rel @ self.u) <= self.hu) & (np.abs(rel @ self.v) <= self.hv) & (t > 1e-6)
        ok &= np.abs(denom) > 1e-12
        return np.where(ok, t, np.inf)

    def normals(self, p: np.ndarray) -> np.ndarray:
        return np.broadcast_to(self.normal, p.shape)


@dataclass
class CylinderSegment:
    base: np.ndarray  # a point on the axis at the bottom
    axis: np.ndarray  # unit
    ref: np.ndarray  # unit, perpendicular to axis: angle 0
    radius: float
    height: float
    angle0: float  # radians, angular range [angle0, angle1] around the axis from ref
    angle1: float
    material: Material
    obj: str

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        a = self.axis
        oc = o - self.base
        d_perp = d - (d @ a)[:, None] * a
        o_perp = oc - (oc @ a) * a
        qa = np.einsum("ij,ij->i", d_perp, d_perp)
        qb = 2 * (d_perp @ o_perp)
        qc = float(o_perp @ o_perp) - self.radius**2
        disc = qb * qb - 4 * qa * qc
        out = np.full(len(d), np.inf)
        ok = (disc >= 0) & (qa > 1e-12)
        sq = np.sqrt(np.where(ok, disc, 0))
        w = np.cross(a, self.ref)
        for sign in (-1.0, 1.0):
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (-qb + sign * sq) / (2 * qa)
            p = oc + t[:, None] * d
            h = p @ a
            ang = np.arctan2(p @ w, p @ self.ref)
            good = ok & (t > 1e-6) & (h >= 0) & (h <= self.height)
            good &= (ang >= self.angle0) & (ang <= self.angle1)
            out = np.where(good & (t < out), t, out)
        return out

    def normals(self, p: np.ndarray) -> np.ndarray:
        rel = p - self.base
        radial = rel - (rel @ self.axis)[:, None] * self.axis
        return np.asarray(radial / np.linalg.norm(radial, axis=1, keepdims=True))


Prim = Rect | CylinderSegment


@dataclass(frozen=True)
class DepthSensorModel:
    """Phone-LiDAR-like depth sensor. ALL VALUES ASSUMED (see module docstring)."""

    width: int = 256
    height: int = 192
    noise_scale: float = 1.0  # multiplies both noise terms (sensitivity sweeps)
    sigma0_m: float = 0.004  # white noise sigma = noise_scale * (sigma0 + sigma_k * z^2)
    sigma_k: float = 0.0015  # per m^2
    corr_sigma_m: float = 0.003  # smooth (spatially correlated) error, metres per metre of depth
    corr_cells: tuple[int, int] = (8, 6)  # its spatial frequency (cells across the frame)
    quant_m: float = 0.001
    max_range_m: float = 5.0
    dropout_scale: float = 1.0  # multiplies the material dropout probabilities
    dropout_base: float = 0.01
    dropout: dict[str, float] = field(
        default_factory=lambda: {"matte": 0.0, "dark": 0.6, "glossy": 0.5, "transparent": 0.8}
    )
    edge_m: float = 0.05  # depth spread inside one depth pixel that makes a flying pixel


@dataclass
class Scene3d:
    kind: SceneKind
    prims: list[Prim]
    proj_intr: Intrinsics
    proj_pose: np.ndarray  # world_from_projector
    cam_intr: Intrinsics  # Gray-code camera (= the phone's colour camera at the anchor shot)
    cam_pose: np.ndarray
    ambient: float = 0.05
    gain: float = 0.85
    gamma: float = 2.2
    blur_sigma: float = 0.8
    noise_sigma: float = 2.0
    seed: int = 0
    params: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ ray casting

    def raycast(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(t (N,), primitive index (N,), -1 = miss) along rays o + t d."""
        best = np.full(len(d), np.inf)
        idx = np.full(len(d), -1, dtype=np.int32)
        for i, prim in enumerate(self.prims):
            t = prim.intersect(o, d)
            closer = t < best
            best = np.where(closer, t, best)
            idx = np.where(closer, i, idx)
        return best, idx

    def normals(self, pts: np.ndarray, idx: np.ndarray) -> np.ndarray:
        out = np.zeros_like(pts)
        for i, prim in enumerate(self.prims):
            m = idx == i
            if m.any():
                out[m] = prim.normals(pts[m])
        return out

    def view(self, intr: Intrinsics, pose: np.ndarray) -> dict[str, np.ndarray]:
        """Per pixel: world point, z-depth, primitive index, normal (pixel centres)."""
        vs, us = np.mgrid[0 : intr.height, 0 : intr.width]
        rays_c = pixel_rays(intr, us.ravel(), vs.ravel())
        d = rays_c @ pose[:3, :3].T
        t, idx = self.raycast(pose[:3, 3], d)
        hit = np.isfinite(t)
        pts = pose[:3, 3] + np.where(hit, t, 0)[:, None] * d
        n = self.normals(pts, idx)
        shape = (intr.height, intr.width)
        return {
            "points": pts.reshape(*shape, 3),
            "z": np.where(hit, t, np.nan).reshape(shape),  # rays have z = 1: t is z-depth
            "prim": idx.reshape(shape),
            "normal": n.reshape(*shape, 3),
            "dir": d.reshape(*shape, 3),
        }

    # ------------------------------------------------------------ ground truth

    def camera_truth(self) -> dict[str, np.ndarray]:
        """Gray-code camera ground truth: where each camera pixel's surface point is lit from
        (projector px, OpenCV centre convention), whether it is lit at all, and shading."""
        cam = self.view(self.cam_intr, self.cam_pose)
        pts = cam["points"].reshape(-1, 3)
        hit = cam["prim"].reshape(-1) >= 0
        q, zp = project(self.proj_intr, self.proj_pose, pts)
        pw, ph = self.proj_intr.width, self.proj_intr.height
        inside = hit & (zp > 0) & (q[:, 0] > -0.5) & (q[:, 0] < pw - 0.5)
        inside &= (q[:, 1] > -0.5) & (q[:, 1] < ph - 0.5)
        zbuf = self.view(self.proj_intr, self.proj_pose)["z"]
        qi = np.clip(np.rint(np.nan_to_num(q[:, 0])).astype(np.int64), 0, pw - 1)
        qj = np.clip(np.rint(np.nan_to_num(q[:, 1])).astype(np.int64), 0, ph - 1)
        zb = zbuf[qj, qi]
        lit = inside & np.isfinite(zb) & (zp <= zb + 0.01 + 0.004 * zp)
        to_proj = self.proj_pose[:3, 3] - pts
        to_proj /= np.maximum(np.linalg.norm(to_proj, axis=1, keepdims=True), 1e-12)
        n = cam["normal"].reshape(-1, 3)
        to_cam = -cam["dir"].reshape(-1, 3)
        n = np.where((np.einsum("ij,ij->i", n, to_cam) < 0)[:, None], -n, n)
        cos = np.clip(np.einsum("ij,ij->i", n, to_proj), 0, 1)
        lit &= cos > 0.02
        albedo = np.zeros(len(pts))
        for i, prim in enumerate(self.prims):
            albedo[cam["prim"].reshape(-1) == i] = prim.material.albedo
        h, w = self.cam_intr.height, self.cam_intr.width
        return {
            "q": q.reshape(h, w, 2).astype(np.float64),
            "lit": lit.reshape(h, w),
            "cos": cos.reshape(h, w),
            "albedo": albedo.reshape(h, w),
            "prim": cam["prim"],
            "points": cam["points"],
            "z": cam["z"],
        }

    def object_of(self, prim: np.ndarray) -> np.ndarray:
        names = np.array([p.obj for p in self.prims] + ["none"])
        return np.asarray(names[np.where(prim >= 0, prim, len(self.prims))])

    # ------------------------------------------------------------ rendering

    def render_graycode(
        self, spec: PatternSpec, truth: dict[str, np.ndarray], rng: np.random.Generator
    ) -> list[np.ndarray]:
        """Camera pictures of every pattern frame (black and white as BGR, stripes gray)."""
        q = truth["q"].astype(np.float32)
        mx, my = np.ascontiguousarray(q[..., 0]), np.ascontiguousarray(q[..., 1])
        lit = (truth["lit"] * truth["cos"]).astype(np.float32)
        albedo = truth["albedo"].astype(np.float32)
        out: list[np.ndarray] = []
        for frame in pattern_sequence(spec):
            pat = render_pattern(spec, frame).astype(np.float32) / np.float32(255.0)
            lin = np.power(pat, np.float32(self.gamma))
            s = cv2.remap(lin, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            rad = albedo * (np.float32(self.ambient) + np.float32(self.gain) * lit * s)
            if self.blur_sigma > 0:
                rad = cv2.GaussianBlur(rad, (0, 0), self.blur_sigma)
            img = np.clip(rad * 255 + rng.normal(0, self.noise_sigma, rad.shape), 0, 255)
            g = img.astype(np.uint8)
            out.append(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR) if frame.kind == "white" else g)
        return out

    def render_photo(
        self, intr: Intrinsics, pose: np.ndarray, rng: np.random.Generator
    ) -> np.ndarray:
        """Room-light photo (projector off) for a shot's RGB image."""
        v = self.view(intr, pose)
        albedo = np.zeros(v["prim"].shape, np.float32)
        for i, prim in enumerate(self.prims):
            albedo[v["prim"] == i] = prim.material.albedo
        img = np.clip(albedo * 200 + rng.normal(0, 2.0, albedo.shape), 0, 255).astype(np.uint8)
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    def render_depth(
        self, pose: np.ndarray, model: DepthSensorModel, rng: np.random.Generator
    ) -> tuple[np.ndarray, np.ndarray, Intrinsics]:
        """Simulated depth (metres, 0 = no data) and confidence (0, 1, 2) for a camera pose.

        Rendered at 2x the depth resolution and pooled 2x2: a depth pixel whose four samples
        spread over more than ``edge_m`` is a flying pixel (a random mix of near and far).
        """
        dintr = self.cam_intr.scaled(model.width, model.height)
        hi = self.cam_intr.scaled(2 * model.width, 2 * model.height)
        v = self.view(hi, pose)
        z = v["z"]
        prim = v["prim"]
        hgt, wid = model.height, model.width
        blocks = z.reshape(hgt, 2, wid, 2).transpose(0, 2, 1, 3).reshape(hgt, wid, 4)
        zmin = np.nanmin(np.where(np.isfinite(blocks), blocks, np.inf), axis=2)
        zmax = np.nanmax(np.where(np.isfinite(blocks), blocks, -np.inf), axis=2)
        with np.errstate(invalid="ignore"):
            zmean = np.nanmean(blocks, axis=2)
        miss = ~np.isfinite(zmean)
        flying = ~miss & ((zmax - zmin) > model.edge_m)
        mix = rng.uniform(0, 1, (hgt, wid))
        depth = np.where(flying, zmin + mix * (zmax - zmin), zmean)
        conf = np.full((hgt, wid), 2, np.uint8)
        conf[flying] = np.where(rng.uniform(size=int(flying.sum())) < 0.5, 0, 1)

        # noise: white + smooth, both growing with depth
        zz = np.nan_to_num(depth, nan=0.0)
        sigma = model.noise_scale * (model.sigma0_m + model.sigma_k * zz**2)
        cw, ch = model.corr_cells
        smooth = cv2.resize(
            rng.normal(0, 1, (ch, cw)).astype(np.float32), (wid, hgt), interpolation=cv2.INTER_CUBIC
        )
        depth = depth + rng.normal(0, 1, depth.shape) * sigma
        depth = depth + smooth * model.noise_scale * model.corr_sigma_m * zz

        # materials (centre sample of each pixel decides)
        centre_prim = prim[0::2, 0::2]
        behaviour = np.array([p.material.depth for p in self.prims] + ["matte"])
        beh = behaviour[np.where(centre_prim >= 0, centre_prim, len(self.prims))]
        p_drop = np.full((hgt, wid), model.dropout_base)
        for name, p in model.dropout.items():
            p_drop[beh == name] = min(1.0, model.dropout_base + p * model.dropout_scale)
        drop = rng.uniform(size=(hgt, wid)) < p_drop
        glass = (beh == "transparent") & ~drop
        depth = np.where(glass, depth + rng.uniform(0.05, 0.5, depth.shape), depth)  # sees through
        conf[(beh == "glossy") & ~drop] = np.minimum(conf[(beh == "glossy") & ~drop], 1)

        # grazing angles and long range lower the confidence
        n = v["normal"][0::2, 0::2]
        dirs = v["dir"][0::2, 0::2]
        dirs = dirs / np.linalg.norm(dirs, axis=2, keepdims=True)
        graze = np.abs(np.einsum("ijk,ijk->ij", n, dirs)) < 0.3
        conf[graze | (zz > 0.7 * model.max_range_m)] = np.minimum(
            conf[graze | (zz > 0.7 * model.max_range_m)], 1
        )
        bad = miss | drop | (zz > model.max_range_m) | (depth <= 0)
        depth = np.round(depth / model.quant_m) * model.quant_m
        depth[bad] = 0.0
        conf[bad] = 0
        return depth.astype(np.float32), conf, dintr


# ---------------------------------------------------------------- scene construction


def _rect(centre: Any, u: Any, v: Any, hu: float, hv: float, mat: Material, obj: str) -> Rect:
    uu = np.asarray(u, dtype=np.float64)
    vv = np.asarray(v, dtype=np.float64)
    return Rect(
        np.asarray(centre, dtype=np.float64),
        uu / np.linalg.norm(uu),
        vv / np.linalg.norm(vv),
        hu,
        hv,
        mat,
        obj,
    )


def box_prims(
    centre: np.ndarray, size: tuple[float, float, float], yaw: float, mat: Material
) -> list[Prim]:
    """A box standing against the wall: centre is the middle of its front face's footprint
    projected on the wall; size (w, h, depth); yaw about the vertical axis."""
    r = rot_axis(UP, yaw)
    ex, ey, ez = r @ np.array([1.0, 0, 0]), UP, r @ np.array([0, 0, 1.0])
    w, h, dep = size
    c = centre + ez * dep / 2
    return [
        _rect(c + ez * dep / 2, ex, ey, w / 2, h / 2, mat, "box"),  # front
        _rect(c - ez * dep / 2, -ex, ey, w / 2, h / 2, mat, "box"),  # back
        _rect(c + ex * w / 2, -ez, ey, dep / 2, h / 2, mat, "box"),  # right side faces +x
        _rect(c - ex * w / 2, ez, ey, dep / 2, h / 2, mat, "box"),  # left
        _rect(c + ey * h / 2, ex, -ez, w / 2, dep / 2, mat, "box"),  # top
        _rect(c - ey * h / 2, ex, ez, w / 2, dep / 2, mat, "box"),  # bottom
    ]


def _log_uniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(math.exp(rng.uniform(math.log(lo), math.log(hi))))


MATTE = Material(0.6)


def make_scene(
    kind: SceneKind,
    seed: int,
    *,
    distance_m: float | None = None,
    object_material: str = "matte",
    cam_size: tuple[int, int] = (1280, 960),
) -> Scene3d:
    """A seeded scene of `kind`. The phone stands `distance_m` from the wall (default
    log-uniform 1.5 to 3.5 m), the projector further back so that its picture fills 60 to 85 %
    of the phone's view, and the object fills a good part of the picture."""
    rng = np.random.default_rng(seed)
    dist = distance_m if distance_m is not None else _log_uniform(rng, 1.5, 3.5)
    throw = float(rng.uniform(1.2, 1.8))
    fxc = float(rng.uniform(0.72, 0.82)) * cam_size[0]
    fill = float(rng.uniform(0.6, 0.85))  # picture width / camera view width at the wall
    proj_dist = throw * fill * dist * cam_size[0] / fxc
    pw, ph = 1920, 1080
    proj_intr = Intrinsics(
        throw * pw,
        throw * pw * float(rng.uniform(0.995, 1.005)),
        (pw - 1) / 2 + float(rng.uniform(-20, 20)),
        float(rng.uniform(0.5, 1.02)) * ph,  # lens shift: the picture sits above the lens axis
        pw,
        ph,
    )
    proj_pos = np.array([rng.uniform(-0.3, 0.3), rng.uniform(0.2, 0.8), proj_dist])
    aim = np.array(
        [proj_pos[0] + rng.uniform(-0.2, 0.2), proj_pos[1] + rng.uniform(-0.1, 0.3), 0.0]
    )
    proj_pose = look_at(proj_pos, aim, UP, roll_deg=float(rng.uniform(-1.5, 1.5)))
    # where the picture lands on the wall: centre and size
    corners = []
    for u, v in ((0, 0), (pw - 1, 0), (pw - 1, ph - 1), (0, ph - 1)):
        ray = proj_pose[:3, :3] @ pixel_rays(proj_intr, np.array([u]), np.array([v]))[0]
        corners.append(proj_pos + (-proj_pos[2] / ray[2]) * ray)
    cs = np.array(corners)
    pic_c = cs.mean(axis=0)
    pic_w = float(np.linalg.norm(cs[1] - cs[0]))
    pic_h = float(np.linalg.norm(cs[3] - cs[0]))

    side = float(rng.choice([-1.0, 1.0]))
    baseline = float(rng.uniform(0.3, 0.8))
    cam_pos = np.array([proj_pos[0] + side * baseline, pic_c[1] + rng.uniform(-0.2, 0.2), dist])
    cam_intr = Intrinsics(fxc, fxc, (cam_size[0] - 1) / 2 + float(rng.uniform(-4, 4)),
                          (cam_size[1] - 1) / 2 + float(rng.uniform(-4, 4)), *cam_size)  # fmt: skip
    cam_pose = look_at(
        cam_pos, pic_c + np.array([0, 0, 0.1]), UP, roll_deg=float(rng.uniform(-2, 2))
    )

    wall_mat = Material(float(rng.uniform(0.45, 0.8)))
    obj_mat = {
        "matte": Material(float(rng.uniform(0.45, 0.8))),
        "dark": Material(0.07, "dark"),
        "glossy": Material(float(rng.uniform(0.45, 0.8)), "glossy"),
        "transparent": Material(float(rng.uniform(0.3, 0.5)), "transparent"),
    }[object_material]
    prims: list[Prim] = [_rect([0, 0, 0], [1, 0, 0], [0, 1, 0], 20.0, 20.0, wall_mat, "wall")]
    scale = proj_dist / 3.0
    params: dict[str, Any] = {
        "kind": kind, "seed": seed, "distance_m": dist, "projector_distance_m": proj_dist,
        "throw": throw,
        "baseline_m": baseline, "picture_w_m": pic_w, "object_material": object_material,
    }  # fmt: skip
    if kind == "board":
        bw, bh = pic_w * float(rng.uniform(0.6, 0.75)), pic_h * float(rng.uniform(0.6, 0.75))
        off = float(rng.uniform(0.1, 0.35)) * scale
        yaw = math.radians(float(rng.uniform(-10, 10)))
        r = rot_axis(UP, yaw)
        centre = pic_c + np.array([rng.uniform(-0.05, 0.05) * pic_w, 0, off])
        prims.append(_rect(centre, r @ np.array([1.0, 0, 0]), UP, bw / 2, bh / 2, obj_mat, "board"))
        params.update(board_offset_m=off, board_yaw_deg=math.degrees(yaw))
    elif kind == "box":
        bw = pic_w * float(rng.uniform(0.25, 0.4))
        bh = pic_h * float(rng.uniform(0.3, 0.5))
        dep = float(rng.uniform(0.2, 0.5)) * scale
        yaw = math.radians(float(rng.uniform(-30, 30)))
        centre = pic_c + np.array(
            [rng.uniform(-0.15, 0.15) * pic_w, rng.uniform(-0.1, 0.1) * pic_h, 0]
        )
        prims += box_prims(centre, (bw, bh, dep), yaw, obj_mat)
        params.update(box_depth_m=dep, box_yaw_deg=math.degrees(yaw))
    elif kind == "cylinder":
        rad = pic_w * float(rng.uniform(0.15, 0.25))
        hgt = pic_h * float(rng.uniform(0.6, 0.9))
        base = pic_c + np.array([rng.uniform(-0.15, 0.15) * pic_w, -hgt / 2, 0.0])
        span = math.radians(float(rng.uniform(120, 180)))
        prims.append(
            CylinderSegment(
                base, UP, np.array([0, 0, 1.0]), rad, hgt, -span / 2, span / 2, obj_mat, "cylinder"
            )
        )
        params.update(cylinder_radius_m=rad, cylinder_span_deg=math.degrees(span))
    elif kind == "stepped":
        n = int(rng.integers(3, 5))
        x0 = pic_c[0] - pic_w * 0.6
        widths = rng.dirichlet(np.ones(n) * 3) * pic_w * 1.2
        offs = [float(rng.uniform(0.0, 0.35)) * scale for _ in range(n)]
        top, bottom = pic_c[1] + pic_h * 0.7, pic_c[1] - pic_h * 0.7
        cy, hy = (top + bottom) / 2, (top - bottom) / 2
        x = x0
        for i in range(n):
            wdt = float(widths[i])
            prims.append(
                _rect(
                    [x + wdt / 2, cy, offs[i]], [1, 0, 0], [0, 1, 0], wdt / 2, hy, obj_mat, "steps"
                )
            )
            if i + 1 < n:
                a, b = offs[i], offs[i + 1]
                if abs(a - b) > 1e-4:  # riser between strips (two-sided)
                    riser_u = [0.0, 0.0, -1.0] if b > a else [0.0, 0.0, 1.0]
                    prims.append(
                        _rect(
                            [x + wdt, cy, (a + b) / 2],
                            riser_u,
                            [0, 1, 0],
                            abs(a - b) / 2,
                            hy,
                            obj_mat,
                            "steps",
                        )
                    )
            x += wdt
        params.update(step_offsets_m=offs)
    params["projector_true"] = {"intrinsics": proj_intr.to_json(), "pose": proj_pose.tolist()}
    return Scene3d(kind, prims, proj_intr, proj_pose, cam_intr, cam_pose, seed=seed, params=params)


# ---------------------------------------------------------------- capture sessions


@dataclass(frozen=True)
class SessionOptions:
    """How the phone was used. ASSUMED values."""

    extra_shots: int = 3  # depth shots besides the anchor (Gray-code) shot
    move_m: float = 0.15  # the phone moves up to this far between shots
    turn_deg: float = 3.0
    pose_rot_err_deg: float = 0.15  # tracking error per shot (1 sigma)
    pose_trans_err_m: float = 0.003
    skip_lsb: int = 2


def _perturb(
    pose: np.ndarray, rng: np.random.Generator, rot_deg: float, trans_m: float
) -> np.ndarray:
    axis = rng.normal(size=3)
    r = rot_axis(axis, math.radians(rng.normal(0, rot_deg))) if rot_deg > 0 else np.eye(3)
    out = make_pose(pose[:3, :3] @ r, pose[:3, 3] + rng.normal(0, trans_m, 3))
    return out


def write_session(
    scene: Scene3d,
    out_dir: Path,
    *,
    sensor: DepthSensorModel | None = None,
    session: SessionOptions | None = None,
    seed: int | None = None,
    graycode_images: list[np.ndarray] | None = None,
) -> Path:
    """Write a complete capture folder (docs/18) for `scene`, with ground truth."""
    sensor = sensor or DepthSensorModel()
    session = session or SessionOptions()
    rng = np.random.default_rng(scene.seed * 7 + 3 if seed is None else seed)
    w = CaptureWriter(out_dir)
    spec = PatternSpec(scene.proj_intr.width, scene.proj_intr.height, session.skip_lsb)
    frames = pattern_sequence(spec)
    if graycode_images is None:
        truth = scene.camera_truth()
        graycode_images = scene.render_graycode(spec, truth, np.random.default_rng(scene.seed))
    white = graycode_images[[f.kind for f in frames].index("white")]
    true_poses = [scene.cam_pose]
    for _ in range(session.extra_shots):
        move = rng.uniform(-session.move_m, session.move_m, 3) * np.array([1.0, 0.6, 0.5])
        turn = rot_axis(
            rng.normal(size=3), math.radians(rng.uniform(-session.turn_deg, session.turn_deg))
        )
        true_poses.append(make_pose(scene.cam_pose[:3, :3] @ turn, scene.cam_pose[:3, 3] + move))
    small = scene.cam_intr.scaled(640, 480)
    for i, tp in enumerate(true_poses):
        reported = _perturb(tp, rng, session.pose_rot_err_deg, session.pose_trans_err_m)
        depth, conf, dintr = scene.render_depth(tp, sensor, rng)
        sid = f"s{i:03d}"
        if i == 0:
            image, intr = white, scene.cam_intr
        else:
            image, intr = scene.render_photo(small, tp, rng), small
        w.add_shot(sid, 10.0 + 0.5 * i, image, intr, reported, depth=depth, confidence=conf,
                   depth_intrinsics=dintr)  # fmt: skip
        if i == 0:
            w.set_graycode_frames(sid, spec, frames, graycode_images, timestamp_s=5.0)
    gt = {
        "source": "simulator",
        "note": "simulated scene; every sensor value is ASSUMED (facadekit.sim.scene3d)",
        "projector": {"intrinsics": scene.proj_intr.to_json(), "pose": scene.proj_pose.tolist()},
        "camera_true_poses": [p.tolist() for p in true_poses],
        "scene": {k: v for k, v in scene.params.items() if k != "projector_true"},
        "sensor": {k: getattr(sensor, k) for k in sensor.__dataclass_fields__},
        "session": {k: getattr(session, k) for k in session.__dataclass_fields__},
    }
    w.write(
        created="2026-01-01T00:00:00Z", device={"model": "facadekit simulator"}, ground_truth=gt
    )
    return out_dir


def cam_from_world(pose: np.ndarray) -> np.ndarray:
    return pose_inverse(pose)
