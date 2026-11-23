# 15 – LiDAR + structured light spike (S-11)

Status: proposal, not started. Evidence tags as in `docs/01-landscape.md`: **[V]** verified, **[A]** assumption or recollection that this spike must check.
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
