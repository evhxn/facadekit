# calib3d simulator evaluation (spike S-11 Part A)

**SIMULATED. No real depth sensor, phone, projector or wall was used.** The depth-sensor
model (`sim/scene3d.py`, `DepthSensorModel`) is ASSUMED: about 256x192 depth, centimetre
noise growing with distance plus a smooth correlated error, 5 m range, dropout on dark,
glossy and transparent materials, flying pixels at depth edges, phone pose error 0.15° /
3 mm per shot. The real device decides (docs/15 Part C).

Scenes: 50 held out (reported), 100 train (constants chosen on these).
Numbers are projector pixels at 1920x1080: median over scenes of each scene's median /
95th percentile, on the target (the board, the box, the cylinder, the steps; the wall for
`flat`). `covered`: every lit pixel, all scan data used. `gaps`: 96 px camera tiles whose
scan data was removed before fitting (what each mapping predicts where the scan has no
data). `dense` is FacadeKit's current dense map (`calib.maps`), which marks such regions
as unmeasured instead of trusting them.

## Held-out scenes, covered

| Scene | Scenes | plane | dense (current map) | model (depth only) | hybrid (calib3d) |
|---|---|---|---|---|---|
| flat | 10 | 0.01 / 0.02 | 0.05 / 0.35 | – / – | 0.01 / 0.02 |
| board | 10 | 0.01 / 0.03 | 0.08 / 0.66 | 0.28 / 0.64 | 0.01 / 0.02 |
| box | 10 | 91.95 / 132.73 | 0.13 / 103.20 | 0.40 / 2.17 | 0.01 / 0.39 |
| cylinder | 10 | 97.61 / 135.60 | 0.08 / 81.63 | 0.60 / 4.36 | 0.13 / 0.63 |
| stepped | 10 | 1.83 / 60.84 | 0.06 / 0.65 | 0.25 / 1.48 | 0.02 / 0.26 |

## Held-out scenes, gaps in the scan

| Scene | Scenes | plane | dense (current map) | model (depth only) | hybrid (calib3d) |
|---|---|---|---|---|---|
| flat | 10 | 0.01 / 0.02 | 0.41 / 37.80 | – / – | 0.01 / 0.02 |
| board | 10 | 0.02 / 0.04 | 0.82 / 57.73 | 0.21 / 0.38 | 0.01 / 0.32 |
| box | 10 | 75.26 / 85.17 | 60.21 / 134.15 | 0.28 / 1.41 | 0.02 / 3.10 |
| cylinder | 10 | 98.62 / 126.85 | 62.22 / 234.95 | 0.67 / 2.90 | 0.59 / 2.74 |
| stepped | 10 | 2.13 / 54.05 | 3.15 / 108.27 | 0.29 / 1.07 | 0.04 / 1.07 |

## Success criteria (docs/15 section 4), held-out scenes, in simulation

A criterion is met only if it holds in at least 90 % of the held-out
scenes of that kind (stricter than docs/15, which does not say how many).

| Criterion | Scene | Scenes | Holds in | Met in simulation |
|---|---|---|---|---|
| 3D mapping not worse than plane by more than 0.1 px (median and p95) | flat | 10 | 100 % | yes |
| 3D mapping not worse than plane by more than 0.1 px (median and p95) | board | 10 | 90 % | yes |
| median ≥ 3x lower than plane, and p95 < 2 px on the object and on all lit points | box | 10 | 80 % (ratio 100 %, p95 80 %) | NO |
| median ≥ 3x lower than plane, and p95 < 2 px on the object and on all lit points | cylinder | 10 | 100 % (ratio 100 %, p95 100 %) | yes |
| median ≥ 3x lower than plane, and p95 < 2 px on the object and on all lit points | stepped | 10 | 90 % (ratio 90 %, p95 90 %) | yes |

Not evaluated in Part A (they need the device): maximum useful range and the surfaces
where depth fails, *measured*; move detection (5 mm shift at under 2 % false alarms).
The range and material rows below are the simulator's assumptions played back, not
measurements.

## Projector self-calibration, held-out scenes

| Scene | Status | fx error | cx / cy error | position | rotation | solve reprojection median / p95 | inliers |
|---|---|---|---|---|---|---|---|
| flat | planar 10 | – | – | – | – | – | – |
| board | ok 10 | 0.5 % | 3.6 / 6.2 px | 18 mm | 0.03° | 0.59 / 1.11 px | 99 % |
| box | ok 10 | 0.7 % | 2.0 / 3.2 px | 21 mm | 0.04° | 0.58 / 1.12 px | 99 % |
| cylinder | ok 10 | 0.6 % | 2.6 / 3.4 px | 29 mm | 0.08° | 0.62 / 1.55 px | 96 % |
| stepped | ok 8, planar 2 | 0.9 % | 7.9 / 5.0 px | 45 mm | 0.08° | 0.62 / 1.35 px | 99 % |

`planar`: the lit surface is one plane, the projector cannot be solved (under-
constrained) and the plane mapping is used, which is exact there. Pose errors are
measured in the frame of the phone's reported pose.

## Sensitivity (held-out scenes with sweeps; covered)

3D objects = box, cylinder, steps (median / p95 as above). Board column: median over
board scenes of (hybrid − plane); the flat criterion allows up to +0.1.

| Variant | Runs | Status (ok/weak/planar/other) | 3D objects: plane | model | hybrid | 3D criteria met | board: hybrid − plane (median / p95) |
|---|---|---|---|---|---|---|---|
| base | 24 | 22/0/2/0 | 54.23 / 68.16 | 0.49 / 2.81 | 0.07 / 0.53 | 16/18 | -0.159 / -0.593 |
| noise x0.5 | 24 | 22/0/2/0 | 54.23 / 68.16 | 0.30 / 2.46 | 0.07 / 0.53 | 16/18 | -0.159 / -0.593 |
| noise x2 | 24 | 22/0/2/0 | 54.23 / 68.16 | 0.98 / 7.39 | 0.09 / 0.66 | 16/18 | -0.159 / -0.276 |
| noise x4 | 24 | 1/0/14/9 | 54.23 / 68.16 | – / – | 54.03 / 68.15 | 0/18 | 0.000 / 0.000 |
| pose 0.05 deg / 1 mm | 24 | 22/0/2/0 | 54.23 / 68.16 | 0.40 / 3.00 | 0.07 / 0.53 | 16/18 | -0.159 / -0.593 |
| pose 0.5 deg / 10 mm | 24 | 22/0/2/0 | 54.23 / 68.16 | 0.59 / 5.40 | 0.07 / 0.60 | 16/18 | -0.159 / -0.593 |
| pose 1 deg / 20 mm | 24 | 22/0/2/0 | 54.23 / 68.16 | 0.97 / 17.36 | 0.08 / 0.64 | 15/18 | -0.159 / -0.593 |
| object dark | 24 | 5/0/19/0 | 54.23 / 68.14 | 0.25 / 1.30 | 54.23 / 67.03 | 4/18 | 0.000 / 0.000 |
| object glossy | 24 | 0/0/17/7 | 54.23 / 68.16 | – / – | 54.23 / 68.16 | 0/18 | 0.000 / 0.014 |
| object transparent | 24 | 4/0/9/11 | 54.23 / 68.16 | 2.42 / 63.83 | 50.11 / 63.67 | 2/18 | 0.000 / 0.003 |
| dropout x2, dark | 24 | 0/0/18/6 | 54.23 / 68.14 | – / – | 54.23 / 68.14 | 0/18 | 0.000 / 0.001 |
| one shot only | 24 | 22/0/2/0 | 54.23 / 68.16 | 0.61 / 4.85 | 0.08 / 0.65 | 16/18 | -0.159 / -0.404 |

## Range (held-out seeds, board, box and cylinder; covered)

The phone stands at the given distance; the projector further back. Depth beyond the
sensor's 5 m (ASSUMED) is missing, so the 3D model cannot be solved there.

| Phone to wall | Projector to wall | Runs | Depth coverage | Status (ok/weak/planar/other) | plane | model | hybrid | 3D/flat criteria met |
|---|---|---|---|---|---|---|---|---|
| 1 m | 1.4 m | 12 | 100 % | 12/0/0/0 | 169.70 / 217.58 | 0.75 / 5.06 | 0.05 / 1.39 | 7/12 |
| 1.5 m | 2.2 m | 12 | 100 % | 12/0/0/0 | 124.20 / 154.28 | 0.52 / 3.27 | 0.03 / 0.33 | 11/12 |
| 2.5 m | 3.6 m | 12 | 100 % | 12/0/0/0 | 87.73 / 106.16 | 0.41 / 1.92 | 0.01 / 0.42 | 10/12 |
| 3.5 m | 5.1 m | 12 | 61 % | 12/0/0/0 | 70.96 / 81.84 | 0.36 / 1.80 | 0.01 / 0.46 | 10/12 |
| 5 m | 7.2 m | 12 | 0 % | 0/0/0/12 | 54.17 / 65.16 | – / – | 54.17 / 65.16 | 4/12 |
| 6.5 m | 9.4 m | 12 | 0 % | 0/0/0/12 | 45.39 / 60.56 | – / – | 45.39 / 60.56 | 4/12 |
| 8 m | 11.6 m | 12 | 0 % | 0/0/0/12 | 40.24 / 58.90 | – / – | 40.24 / 58.90 | 4/12 |

## Train scenes (for comparison; constants were chosen on these)

| Scene | Scenes | plane | dense (current map) | model (depth only) | hybrid (calib3d) |
|---|---|---|---|---|---|
| flat | 20 | 0.01 / 0.02 | 0.07 / 0.40 | – / – | 0.01 / 0.02 |
| board | 20 | 0.01 / 0.02 | 0.07 / 0.59 | 0.22 / 0.44 | 0.00 / 0.01 |
| box | 20 | 117.61 / 156.84 | 0.07 / 149.68 | 0.27 / 1.64 | 0.01 / 0.39 |
| cylinder | 20 | 114.67 / 141.09 | 0.12 / 75.30 | 0.74 / 4.09 | 0.14 / 0.68 |
| stepped | 20 | 1.66 / 55.33 | 0.07 / 0.56 | 0.31 / 0.98 | 0.01 / 0.26 |

Solve time per scene (decode, fusion, projector, mapping; one CPU core): median 2.1 s.
