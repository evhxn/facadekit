"""Scene distributions for the RL simulation.

Three splits, never mixed:

- ``train``: everything that is fitted or tuned uses only these scenes.
- ``heldout``: same distribution as ``train``, different seeds.
- ``shifted``: a different distribution (keystoned camera, occluders, brighter ambient light,
  dimmer projector, smaller pictures) to see whether a result survives a change of setting.

A scene is fully described by a small JSON-able dict, so outcome caches stay valid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from facadekit.sim.scene import SimScene, default_scene, far_scene, scale_world

SPLITS = ("train", "heldout", "shifted")
_BASE_SEED = {"train": 100_000, "heldout": 200_000, "shifted": 300_000}
_CAMERAS = (((1280, 720), 0.70), ((1920, 1080), 0.20), ((2560, 1440), 0.10))


@dataclass(frozen=True)
class SceneSpec:
    id: str  # "<split>-<index>"
    split: str
    params: dict[str, Any]


def _log_uniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(math.exp(rng.uniform(math.log(lo), math.log(hi))))


def _sample(split: str, index: int) -> dict[str, Any]:
    seed = _BASE_SEED[split] + index
    rng = np.random.default_rng(seed)
    shifted = split == "shifted"
    cams = [c for c, _ in _CAMERAS]
    cam = cams[int(rng.choice(len(cams), p=[p for _, p in _CAMERAS]))]
    k = _log_uniform(rng, 0.20, 0.90) if shifted else _log_uniform(rng, 0.22, 1.0)
    p: dict[str, Any] = {
        "seed": seed,
        "cam": list(cam),
        "k": k,
        "cx": float(rng.uniform(0.0, 1.0)),  # position inside the room left in the frame
        "cy": float(rng.uniform(0.0, 1.0)),
        # blur and noise in camera pixels of a 1280-wide picture; blur grows with resolution
        "blur": _log_uniform(rng, 0.5, 5.0),
        "noise": _log_uniform(rng, 1.0, 12.0),
        "ambient": float(rng.uniform(0.10, 0.45) if shifted else rng.uniform(0.02, 0.30)),
        "gain": float(rng.uniform(0.25, 0.80) if shifted else rng.uniform(0.35, 1.0)),
        "gamma": float(rng.uniform(1.8, 2.6)),
        "door": [float(rng.uniform(-80, 80)), float(rng.uniform(-60, 60))],
        "keystone": [0.0] * 8,
        "occluders": [],
    }
    if shifted:
        p["keystone"] = [float(v) for v in rng.uniform(-0.07, 0.07, 8)]
        for _ in range(int(rng.integers(0, 3))):
            w, h = float(rng.uniform(80, 260)), float(rng.uniform(120, 420))
            x, y = float(rng.uniform(150, 1400 - w)), float(rng.uniform(100, 860 - h))
            p["occluders"].append([x, y, x + w, y + h])
    return p


def make_specs(split: str, n: int) -> list[SceneSpec]:
    assert split in SPLITS
    return [SceneSpec(f"{split}-{i}", split, _sample(split, i)) for i in range(n)]


def build_scene(spec: SceneSpec, realisation: int = 0) -> SimScene:
    """The SimScene of a spec. `realisation` only changes the camera noise sequence."""
    p = spec.params
    cam = (int(p["cam"][0]), int(p["cam"][1]))
    base = default_scene()
    corners = np.asarray(base.world_corners_cam, dtype=np.float64)
    half = (corners.max(axis=0) - corners.min(axis=0)) / 2 * p["k"] * cam[0] / 1280
    margin = 12.0
    lo = np.array([margin, margin]) + half
    hi = np.array([cam[0] - margin, cam[1] - margin]) - half
    mid = np.array([cam[0] / 2, cam[1] / 2])
    lo, hi = np.minimum(lo, mid), np.maximum(hi, mid)  # picture larger than the frame: centre it
    centre = lo + (hi - lo) * np.array([p["cx"], p["cy"]])
    scale = cam[0] / 1280
    scene = far_scene(
        p["k"],
        cam_size=cam,
        centre=(float(centre[0] / cam[0]), float(centre[1] / cam[1])),
        blur_sigma=float(p["blur"] * scale),
        noise_sigma=float(p["noise"]),
        ambient=float(p["ambient"]),
        gain=float(p["gain"]),
        gamma=float(p["gamma"]),
        seed=int(p["seed"]) * 16 + realisation,
    )
    ks = np.asarray(p["keystone"], dtype=np.float64).reshape(4, 2)
    if np.any(ks):
        c = np.asarray(scene.world_corners_cam, dtype=np.float64)
        size = c.max(axis=0) - c.min(axis=0)
        c = c + ks * size
        scene = replace(scene, world_corners_cam=tuple((float(x), float(y)) for x, y in c))  # type: ignore[arg-type]
    dx, dy = p["door"]
    scene.features_world["garage_door"] = (500 + dx, 480 + dy, 1100 + dx, 800 + dy)
    scene.occluders_world = [(o[0], o[1], o[2], o[3]) for o in p["occluders"]]
    if cam[0] > 1280:  # keep the wall texture finer than the camera, as scene_phone does
        scene = scale_world(scene, 2.0)
    return scene
