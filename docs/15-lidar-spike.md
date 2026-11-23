# 15 – LiDAR + structured light spike (S-11)

Status: Part A (offline pipeline) built and evaluated **in simulation only** (section 7, 2026-11-23); Parts B and C not started. Evidence tags as in `docs/01-landscape.md`: **[V]** verified, **[A]** assumption or recollection that this spike must check.
Related: `docs/14-illumibot-comparison.md` (phone-first competitor), `docs/08-camera-quality-and-phone-camera.md` (phone camera by upload), `docs/09-rl-simulation.md` (simulator evaluation style).

## 1. Question

Can an iPhone's LiDAR depth and captured images, combined with FacadeKit's structured-light (Gray-code) scan, give a projector-to-surface mapping that is measurably more accurate than the current plane-based mapping, especially on non-flat surfaces? Decide by numbers whether a full iOS app is worth building.

## 2. Background and honest limits [A]

- iPhone Pro models (12 Pro and later) and iPad Pro have LiDAR. Depth maps are small (about 256×192) with centimetre-level error. Range is about 5 m. Dark, glossy and transparent surfaces give poor depth. Verify all of this on the actual device in this spike.
- LiDAR alone cannot give projector alignment: the projector is not a camera. The accurate part is the Gray-code correspondence between camera pixels and projector pixels (already built, 0.28 px corner error in simulation [V]).
- Hypothesis: with camera pose and approximate 3D points from LiDAR/ARKit, plus dense camera-to-projector correspondences, the projector's intrinsics and pose can be solved (camera calibration from 3D-2D pairs). That enables perspective-correct mapping on curved or stepped surfaces and a 3D "has anything moved" check.
- For house-scale scenes beyond LiDAR range, photogrammetry (for example COLMAP) is the fallback and also works on non-LiDAR phones.

## 3. Plan

### Part A – offline pipeline (no iPhone needed; build first)

New module `facadekit/calib3d/` plus `facadekit calib3d solve <capture-dir>`:
1. A capture format (documented, versioned): RGB frames, optional depth map(s) with confidence, camera intrinsics per frame, camera pose per frame, and the Gray-code capture set as FacadeKit already stores it.
2. Fuse depth into a surface model; estimate planes and non-planar regions.
3. Solve projector intrinsics and pose from the 3D points and their decoded projector pixels (robust, RANSAC then refinement); report reprojection error in projector pixels.
4. Produce a per-pixel 3D-aware warp for rendering, and compare against the current homography mapping.
5. Simulator: extend `SimScene` with 3D objects (a box, a cylinder segment, a stepped wall) and a simulated depth sensor with noise, quantisation and range limits, so the pipeline is tested with ground truth before any hardware.

### Part B – iPhone capture app (needs a Pro iPhone)

Minimal SwiftUI app, capture only, no UI polish: ARKit session with scene depth, saves RGB, depth, confidence, intrinsics and pose per shot in the Part A format, and exports over the local network or Files. Lock exposure and focus (iPhone cameras allow it). Requires an Apple developer account and a physical device; cannot be verified in the simulator.

### Part C – experiment

Targets: (1) a flat wall or board, (2) one 3D object (box or curved prop), (3) the owner's garage or porch if within about 5 m.
For each: Gray-code scan plus LiDAR capture, same session, camera not moved.

## 4. Success criteria (set before running; tighten, never loosen afterwards)

- Flat target: the 3D method must not be worse than the current plane mapping by more than 0.1 px (projector pixels, median and 95th percentile).
- 3D target: median reprojection error at least 3× lower than the plane mapping, and 95th percentile under 2 projector pixels at 1080p.
- Reported maximum useful range and the surfaces where depth fails (dark, glossy, glass), measured.
- Move detection: a 5 mm camera or projector shift is detected on the 3D target at a false-alarm rate under 2 % on stable captures (builds on the Verify surface work).
- Stop condition: if the 3D target does not beat the plane mapping by the margin above, record that and do not build the app.

## 5. Out of scope for the spike

An App Store app, Raspberry Pi projector node, multi-projector timecode sync, any cloud service. Those are separate roadmap items (see `docs/14-illumibot-comparison.md`).

## 6. Owner decisions (2026-11-22)

- **Test device:** iPhone 17 Pro (LiDAR [A]: confirm in the spike that ARKit reports scene depth on this device). The M2 iPad Pro is gone; no iPad testing.
- **Scope:** the LiDAR feature is iOS-only and optional. The core (scan, surfaces, arrange, render, publish) stays cross-platform and does not depend on it. Without a LiDAR device FacadeKit keeps using the plane-based mapping.
- **Phone as camera (no LiDAR)** remains a separate, later track: it works on any phone via the existing record-and-upload path (`docs/08`), and could later be added to the iOS app for non-Pro iPhones. Android and mobile web are not ruled out for that track.
- **Still open:** native iOS long term means an Apple developer account and TestFlight or App Store distribution; decide before Part B ships to anyone but the owner. Photogrammetry-first for house-scale scenes beyond LiDAR range is still undecided.

## 7. Part A results (2026-11-23) – simulation only

**Everything in this section comes from the simulator. No phone, depth sensor, projector or
wall was used. The depth-sensor model is an assumption (`sim/scene3d.py`, `DepthSensorModel`:
256x192, noise 4 mm + 1.5 mm/m², a smooth correlated error of 3 mm per metre, 1 mm
quantisation, 5 m range, dropout on dark/glossy/transparent surfaces, flying pixels at depth
edges, phone pose error 0.15° / 3 mm per shot). The real device decides (Part C).**

Code: `facadekit/calib3d/`, CLI `facadekit calib3d solve`, capture format and method in
`docs/18-calib3d-capture-format.md`. Evaluation: `python -m facadekit.sim.calib3d_eval`, full
tables in `studio/backend/calib3d_data/report.md` (and `report.json`).

### Setup

- Five scene kinds (flat wall, flat board in front of a wall, box, cylinder segment, stepped
  wall), 30 seeded scenes each; `index % 3 == 0` held out (10 per kind), every constant chosen
  on the 20 train scenes per kind. Phone 1.5 to 3.5 m from the wall, projector further back
  (1920x1080, throw 1.2 to 1.8, lens shift), phone-projector baseline 0.3 to 0.8 m, four depth
  shots per session.
- The Gray-code scan is rendered with the real patterns and decoded with the real
  `calib.decode`. Ground truth: the projector pixel that really lights the surface point each
  camera pixel sees. Errors in projector pixels; per scene the median and 95th percentile over
  the target's lit pixels; tables give the median over scenes.
- Mappings: `plane` = one RANSAC homography (what the plane mapping assumes; fitted at least as
  well as `calib.maps` does), `dense` = FacadeKit's current dense map (`calib.maps.p2c`,
  inverted), `model` = depth sensor surface + solved projector, `hybrid` = what `calib3d`
  ships (per-plane scan homographies, otherwise the solved projector on the scan-corrected
  surface, the plane mapping as fallback).

### Held-out results (median / p95, projector px, on the target)

| Scene | plane | dense (current map) | model (depth only) | hybrid |
|---|---|---|---|---|
| flat wall | 0.01 / 0.02 | 0.05 / 0.35 | – (planar) | 0.01 / 0.02 |
| board | 0.01 / 0.03 | 0.08 / 0.66 | 0.28 / 0.64 | 0.01 / 0.02 |
| box | 91.95 / 132.73 | 0.13 / 103.20 | 0.40 / 2.17 | 0.01 / 0.39 |
| cylinder | 97.61 / 135.60 | 0.08 / 81.63 | 0.60 / 4.36 | 0.13 / 0.63 |
| stepped | 1.83 / 60.84 | 0.06 / 0.65 | 0.25 / 1.48 | 0.02 / 0.26 |

Where the scan has no data (96 px tiles removed before fitting): box hybrid 0.02 / 3.10 against
dense 60.21 / 134.15; cylinder 0.59 / 2.74 against 62.22 / 234.95; stepped 0.04 / 1.07 against
3.15 / 108.27. The current dense map marks such regions as unmeasured rather than trusting them.

### Success criteria (section 4), checked in simulation

A criterion counts as met only if it holds in at least 90 % of the held-out scenes of that kind
(section 4 does not say how many; this is stricter).

| Criterion | Scene | Holds in | Met in simulation |
|---|---|---|---|
| Flat: not worse than plane by more than 0.1 px (median and p95) | flat wall | 100 % | yes (by design: a plane gives status `planar` and the plane mapping) |
| same | board | 90 % | yes |
| 3D: median ≥ 3x lower than plane and p95 < 2 px (object and all lit points) | box | 80 % (ratio 100 %, p95 80 %) | **no** |
| same | cylinder | 100 % | yes |
| same | stepped | 90 % | yes |

The box misses on its p95 in 2 of 10 scenes: side faces the phone sees at a grazing angle are
barely decoded and the depth there mixes face and wall, so a few percent of the box is
several pixels off. Range, failure surfaces and move detection are not measurable here (they
need the device); move detection was not built in Part A.

Projector self-calibration (held-out, solved scenes): focal length within 0.5 to 0.9 %,
principal point within 2 to 8 px, position within 18 to 45 mm, rotation within 0.1°;
reprojection of the solve 0.6 px median, 1.1 to 1.6 px p95, 96 to 99 % inliers. A flat wall
always gives `planar` (under-constrained), never a projector.

### Sensitivity (held-out sweep scenes; full table in the report)

- **Depth noise:** ×0.5 to ×2 change little for `hybrid` (3D objects p95 0.53 → 0.66 px; the
  scan carries the precision); `model` alone degrades (p95 2.8 → 7.4 px). At ×4 (about 3 to
  5 cm) the relief drowns in noise: the solve reports `planar` or `failed` and the mapping falls
  back to the plane mapping (no gain, no harm).
- **Phone pose error** up to 1° / 20 mm: `hybrid` 0.53 → 0.64 px p95; `model` alone degrades
  badly (p95 17 px).
- **Dark, glossy, transparent objects** (ASSUMED dropout): no usable depth on the object, the
  solve reports `planar`/`failed` and the plane mapping is used; the 3D gain is lost on such
  objects. Transparent objects that return wrong depth with high confidence are the worst case.
- **One depth shot instead of four:** small loss (p95 0.53 → 0.65 px).
- **Range:** with the default confidence floor (2, high) and the assumed 5 m sensor, depth is
  complete to a 2.5 m phone distance, 61 % at 3.5 m and gone at 5 m and beyond, where the
  method reports `too_few` and falls back to the plane mapping. 1 m is worse (7 of 12 meet the
  criteria) because side faces are seen at steeper angles.

### Honest reading

1. In simulation, on its own terms (versus the plane mapping), the 3D method meets the flat
   criterion and the 3D criterion for the cylinder and the steps, and misses it for the box
   (p95 in 2 of 10 scenes). By the stop condition, Part A alone does not clear the bar for all
   3D targets.
2. Versus FacadeKit's **current dense map**, the gain is narrower than "3x": where the scan has
   data the dense map is already good on 3D objects (median 0.06 to 0.13 px) except along depth
   edges, where its local fit smooths across the edge (p95 80 to 100 px on box and cylinder,
   against 0.4 to 0.6 px for `hybrid`). The clear gains are at depth edges and in parts the scan
   did not reach; a perspective-correct content mapping on a known 3D surface is the other use.
3. The 3D gain comes mostly from the Gray-code scan once the projector is known; the depth
   sensor's job is to give the projector and to fill gaps. `model` (depth only) is far worse
   than `hybrid` everywhere.
4. These numbers rest on an assumed sensor. Real depth noise structure, ARKit pose drift,
   rolling shutter, lens distortion and real decode failure modes are not in the simulator.

### How the evaluation was run, including what changed after seeing held-out numbers

The held-out scenes were first scored with an earlier version. Its base numbers were the same
as above, but the sensitivity sweep showed silent failures: with depth noise ×4 and with glossy
objects the projector "solved" with status `ok` from points that were effectively one plane
(depth relief below the noise) and the mapping was up to 130 px wrong. Four safety changes
followed, none of which tunes accuracy: the planar test became noise-aware and is repeated
after outlier rejection; a solve with under 60 % inliers is `failed`; a forced solve when depth
looked planar but the scan did not was removed; and a scan that one homography explains is
treated as planar (found on a train scene). The evaluation was then rerun from scratch; the
tables above are from the rerun. The base held-out numbers did not change.

### Reproduce

```
cd studio/backend
uv run python -m facadekit.sim.calib3d_eval run --out .calib3d_cache --n 30 --n-sens 6 --n-range 4
uv run python -m facadekit.sim.calib3d_eval report --data .calib3d_cache --out calib3d_data
```

About 2.5 minutes on 15 cores (CPU only, no GPU, no network). Per capture the solve takes about
2 s on one core.
