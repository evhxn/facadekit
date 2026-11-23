# 13. Verify surface: has the rig moved, does the light land on the object?

Added 2026-11-22. Code: `studio/backend/src/facadekit/calib/verify.py` (orchestration),
`calib/rigcheck.py`, `calib/surfacecheck.py`, `calib/verify_capture.py`, `calib/p2cmap.py`,
`calib/selftest.py`, `api/routes_verify.py`, `verify_cli.py`; web
`studio/web/src/pages/surfaces/VerifyPanel.tsx`. Evaluation: `sim/verify_eval.py`,
`sim/verify_report.py`, raw numbers in `studio/backend/verify_data/report.md`.

**Status: built and tested in the simulator only. It has not been run on a real camera,
projector or house** (see "Hardware" at the end).

## 1. What it does

After Magic select (or any edit), **Check on the house** in the Surfaces panel runs two checks
with the projector and the camera that made the scan:

1. **Rig check.** Have the camera or the projector moved since the scan? Verdict `stable`,
   `camera_moved`, `projector_moved`, `both_moved`, `moved` (something moved, but the wall has
   too little texture to say which) or `unknown` (too few test dots seen), with the shift in
   pixels, a confidence and a recommendation.
2. **Surface check.** Does the surface's light land on its object, as the camera sees it now?
   Verdict `pass` / `warn` / `fail`, per side the distance between the light's edge and the
   object's edge (projector px, the unit of the editor; + light spills past the object,
   − it stops short), the share of light off the object and of object left dark, a
   flatness/parallax warning, a camera overlay picture and per-side advice ("Left edge spills
   14 px past the object: pull it in."). When a safe correction exists it is offered as
   **Apply suggested fix**; it is never applied without that click.

It is also available headless: `facadekit verify PROJECT_DIR [--surface ID]` (through a
running `facadekit serve`, or `--sim`), and `facadekit verify --self-test` (section 7).

## 2. Pictures projected

All at one projection level, which starts at 0.6 of full brightness and only goes up (in
steps of 0.2, to 1.0) while the white picture is faint (median white minus black below 40
gray levels), and down (×0.65, to `MIN_LEVEL` 0.35) while it clips. No full-brightness flash
unless the picture needs it.

| Picture | Used for |
|---|---|
| black | ambient light, subtracted from everything |
| white (whole frame) | the denominator of every ratio image; the wall's texture for the camera check; the object's colour |
| jittered dot constellation (about 66 dots at 1080p) | where the projector's pixels land now |
| the surface filled (its mask, the same raster the show uses) | where the surface's light lands |
| dots inside the surface | whether the surface moved like the wall (flatness) |

Rig only: black, white, dots (3–4 pictures). With a surface: 5–6. The waits are the scan's own
(`calibration.settings`, measured for this camera when the scan tuned itself), at most 3
frames averaged (2 in low-power mode).

The camera is locked (exposure, white balance, focus) where it is. A camera that cannot be
locked keeps its automatic exposure; the checks still work because every measurement is a
ratio (next section) and every threshold is relative to the picture's own level, and the
black level is corrected for the exposure change seen in unlit parts of the picture. In the
simulator an unlockable camera (`LaggyCamera(lockable=False)`) gives the same verdicts.

## 3. Method

### Ratio images (reference subtraction)

For a picture P: `q = (P − black) / (white − black)`. Ambient light cancels in the
subtraction, the surface's colour and texture cancel in the division, so "lit" means
`q > 0.5` on a dark brick as on a white door. Pixels with `white − black < 6` gray levels
are not used (too dark to divide by).

The ratio has one bias: at a colour edge the camera's blur pulls its 0.5 crossing towards
the darker side by about the blur radius (the same effect `docs/07` describes for the scan's
stripes). Where the light's edge lies on the object's own colour edge, the edge is therefore
measured on the plain lit-minus-black picture instead (half of the level just inside), which
is not pulled.

### Rig check

- **Dots.** A constellation of dots (spacing 1/11 of the frame, jittered by ±30 % of the
  spacing so matching cannot alias by one grid step, radius at least 3 camera px) is
  projected. Their expected camera positions come from the scan's map (`p2c`). Observed
  centroids are matched (phase correlation for the coarse shift, then mutual nearest
  neighbours, then a RANSAC homography and a refit). One homography through all matched dots
  gives the combined motion; its median displacement is the total shift. Fitting first and
  then measuring matters: the median of single-dot displacements is biased upwards by noise
  (a length is never negative), the fit averages the noise away.
- **Wall texture.** The camera's own motion is measured on the white picture's texture: high
  pass of the log brightness (scale-free, so exposure and projector level do not matter),
  restricted to pixels bright enough to carry texture and away from the edge of the projected
  light (that edge moves with the projector, not with the camera). The reference is the
  scan's own white picture (`cam_white.png`, stored by every scan since this change; for older
  scans the projector view is mapped back into the camera). AKAZE features give a start, ECC
  (enhanced correlation coefficient, sub-pixel, invariant to brightness and contrast) refines
  from the features, from "nothing moved" and from the phase-correlation shift; the best
  correlation wins, and below 0.3 the camera motion is reported as unknown.
- **Projector motion** = the dot motion with the camera motion taken out
  (`H_cam⁻¹ · H_dots`), median displacement over the dots, converted to projector px with the
  map's local scale. When the projector moved, a drift homography (old → new projector px) is
  fitted for the surface check.
- **Telling them apart is physical, not statistical.** The wall's texture moves only with the
  camera; the dots move with both. A plain wall with no texture gives `moved` instead of a
  guess. A move of camera and projector that cancels exactly would look stable (not seen in
  the simulator, not impossible).

### Surface check

Three regions in the current camera picture: **I** (intended: the outline through the scan's
map and the camera's motion), **L** (lit: `q > 0.5` of the fill picture, normalised by its
own median), **O** (the object: pixels connected to the outline's core whose colour in the
reflectance picture, white − black in Lab, stays within 3.5 robust spreads of the core's in
every channel; holes filled; limited to a search band around the outline).

O was first found with GrabCut seeded from the outline. Seeded from an outline drawn too big,
GrabCut kept the door's dark frame as part of the door in about half the samples (simulator,
default scene, outline 8 px too big: measured spill 0 instead of 8 on those samples). The
colour rule does not: the frame does not look like the door's core.

Along outward normals of the outline (4 to 40 samples per side, 15 % left out at each corner)
the check finds where the light's edge is and where the object's edge is (O's edge, snapped
to the strongest colour change within 3 camera px). Both are mapped back through the scan's
map into the scan's projector px, so the camera's viewing angle does not distort the numbers.
The light's edge is predicted from the rig check (drift homography, or no motion); the
measured edge replaces the prediction only where it is trustworthy (white − black at least
20 gray levels, or 20 % of the median, all the way) and disagrees by more than 3 px. A dark
frame or a black shutter next to the object therefore cannot hide spilled light.

Per side: the spill along the side is fitted with a straight line (robust Huber weights; a
shifted or turned edge is linear along the side), read at the 10th and 90th percentile of the
positions; the verdict uses the larger of the two (`max_edge_px`), so a turned outline whose
middle is right but whose ends are off does not pass.

- `leakage` / `uncovered` and `iou_object` come from the same edge measurements (each sample
  stands for its share of its side), not from counting camera pixels: the camera's blur makes
  a one-to-two-pixel band at the object's edge ambiguous, which on a small picture is several
  percent of the area.
- **Flatness / parallax.** (a) the spread (MAD) of the measured light edge against its
  prediction after the best common shift, from samples away from colour edges; (b) the dots
  inside the surface against the wall-wide dot motion. Either above 1.5 px warns that the
  surface did not move like the wall (it stands out from the wall, it is not flat, or it
  moved) and no automatic correction is offered.
- **Suggested fix.** Each side is re-fitted through the object's edge points measured along
  it (shift and tilt), neighbouring sides re-intersected. A mask is corrected the same way,
  side by side, and its placement quad is moved by each direction's median. Offered only when
  the object was clearly seen, there is no flatness warning, the largest change is at most
  60 px and at most a quarter of the surface's size, and the result is a clean shape.

### Verdict

| Verdict | Rule |
|---|---|
| pass | object clearly seen (colour separation ≥ 0.8), every side measured, worst side ≤ `PASS_EDGE_PX`, no flatness warning |
| warn | worst side ≤ `WARN_EDGE_PX`, or a side not measured, or the object unclear, or the flatness warning |
| fail | worst side > `WARN_EDGE_PX` (or, with the object unclear, the light more than that away from where it was meant to go) |

## 4. Thresholds and how they were chosen

All thresholds are module constants (`calib/rigcheck.py`, `calib/surfacecheck.py`). They were
set on the 31 train scenes of the evaluation below; the 14 held-out scenes were scored once
with them. Rule: where the data allowed a choice, the stricter value was kept.

| Constant | Value | How it was chosen |
|---|---|---|
| `PROJ_MOVED_PX` | 0.75 projector px | No false alarm on the 620 train runs where nothing moved (largest estimate there: below 0.75). The "no false alarm on any train run where the projector stayed" value is 1.35 px, because a camera move leaves a small residue in the projector estimate; 0.75 was kept (stricter): on held-out scenes that costs 2 false "projector moved" in 364 runs with a moved camera, and catches every projector move of 0.75 px or more. |
| `NOISE_K` | 3 | A move must also exceed 3 standard errors of the dots' fit (small or noisy pictures need a bigger move before it is called one). Not tuned. |
| `CAM_MOVED_PX` | 0.7 camera px | Smallest value with no false alarm on train runs where the camera stayed (was 0.75 before the evaluation; tightened). |
| `MIN_ECC_SCORE` | 0.3 | Below it the camera motion is "unknown". Not tuned. |
| `PASS_EDGE_PX` / `WARN_EDGE_PX` | 2.5 / 6 projector px | The product's classes (truth pass ≤ 2.5 px, fail > 6 px; about 6 mm and 15 mm on a 5 m wide house at 1080p). The train data would have allowed a pass threshold of 2.8 px with no false pass; 2.5 was kept. |
| `PARALLAX_PX`, `INNER_PX`, `DEPTH_STEP_CAM_PX`, `DISAGREE_SHARE` | 1.5 px, 1.5 px, 1.0 camera px, 30 % | Flatness warning. Set by hand from the default scene (flat door: depth-step residual about 0.2 camera px; raised door: several px). They make the check say `warn` (never `pass`) on raised doors; see the table below. |
| `OBJECT_CONF_MIN` | 0.8 | Colour separation of object and surroundings. Not tuned. |
| `MAX_FIX_PX`, `MAX_FIX_FRACTION` | 60 px, 25 % | Largest change offered as a fix. A guess. |


## 5. Simulator evaluation

Harness `sim/verify_eval.py`, report `sim/verify_report.py`; full output in
`studio/backend/verify_data/report.md` (numbers below copied from it). Reproduce:

```
cd studio/backend
uv run python -m facadekit.sim.verify_eval run --n 60 --out /tmp/vd   # about 35 min on 15 cores
uv run python -m facadekit.sim.verify_eval report --data /tmp/vd
```

60 base scenes, each scanned once through the real `run_calibration`: camera picture size
0.55–1.0 of the default (log-uniform), blur 0.5–2 px, noise 1–6 gray levels, ambient
0.02–0.25, projector gain 0.45–1, gamma 1.8–2.6, door moved ±80 / ±50 world px, brick
pattern on 80 % of walls, blotchy door texture on a third, and on a quarter the door stands
4–12 world px out from the wall (parallax and shadow; after a projector move it moves 30 %
less than the wall). 15 scans failed (coverage below 50 %, the existing scan rule) and were
skipped: 31 train scenes (`index % 3 != 0`), 14 held out. Each scene gets 34 changes: 3
stable runs (ambient ±30 %), camera moved 0.5/1/2/4/10 px and turned 0.3° with 0.4 % zoom,
projector moved 0.35–7 world px (about 0.5–10 projector px) and turned 0.15°, both moved,
outline grown −8…+16 px, shifted 2/4/8 px, turned 1°/2°, an unlockable camera, dim
(ambient 0.35) and bright (gain 1.6, clipping) light. 1530 checks, none refused.

### Rig check, held-out scenes

| True move | Projector moved: reported | Camera moved: reported |
|---|---|---|
| none | 0 / 280 | 0 / 280 |
| under 0.75 px | 0 / 14 | 0 / 14 |
| 0.75–1.5 px | 22 / 22 | 22 / 22 |
| 1.5–3 px | 20 / 20 | 20 / 20 |
| 3–6 px | 14 / 14 | 14 / 14 |
| 6 px and more | 14 / 14 | 14 / 14 |

Which device (moves of 1 px or more; held-out): nothing moved → `stable` 280 of 280;
camera only → `camera_moved` 65 of 67 (2 called `both_moved`: a residue of the camera's
motion read as a small projector move); projector only → `projector_moved` 56 of 56; both →
`both_moved` 25 of 28 and `moved` (camera motion unknown) 3. Train: 620/620, 129/136,
123/124, 59/62. Shift error:
projector median 0.11 px, 95th percentile 0.52 px; camera median 0.02 px, 95th 0.10 px.
Camera motion unknown in 1.5 % of runs. Train scenes: 0 false alarms in 620 still runs, 53 of
54 projector moves of 0.75–1.5 px caught, all larger moves caught.

### Surface check, held-out scenes

Error of the measured spill per side against the truth (projector px):

| Scenes | Sides measured | Median | 90th pct | 95th pct |
|---|---|---|---|---|
| all | 94.8 % | 0.53 | 2.49 | 3.17 |
| flat door | 97.9 % | 0.39 | 0.78 | 0.89 |
| door standing out from the wall | 89.1 % | 1.84 | 3.40 | 3.58 |
| projector moved | 95.0 % | 0.53 | 2.51 | 3.11 |
| camera moved | 96.8 % | 0.52 | 2.59 | 3.12 |
| unlockable camera | 92.5 % | 0.49 | 2.53 | 3.25 |
| dim / bright | 92.0 / 94.6 % | 0.52 / 0.55 | 2.69 / 2.40 | 3.29 / 3.07 |

Verdict against the truth (truth from the true worst edge):

| Truth \ check | pass | warn | fail |
|---|---|---|---|
| pass (≤ 2.5 px) | 103 | 155 | 0 |
| warn | 0 | 92 | 24 |
| fail (> 6 px) | 0 | 6 | 96 |

No outline that was truly more than 6 px off was passed (0 of 102), and none within 2.5 px
was failed (0 of 258). The price is caution: 155 of 258 good outlines got `warn`, mostly on
doors that stand out from the wall (the depth-step warning; there the measurement itself is
1.8 px off at the median) and where a side could not be measured. On flat doors only, the
simple worst-edge rule is right in 95 % of runs.

Suggested fix: offered in 91 of the 218 runs that needed one (none when the flatness warning
or an unclear object blocks it); corner error after the fix median 0.96 px, 90th percentile
1.31 px, worst 3.46 px; never worse than the outline as drawn (0 of 91). Train scenes: 380
offered, 1 worse than the drawn outline.

### Time

Timing model of `sim/lag.py` (its delays are assumptions, not measurements: picture on screen
80 ms after `show`, 100 ms more to reach the camera, 30 frames/s) with the scan's default
waits (300 ms, 5 discarded frames, 3 averaged): rig check 2.7 s of projecting and capturing
(4 pictures, one more white because the default scene is faint at 0.6) plus 0.3 s of
computing on this laptop (M5 Pro, idle) = about 3.0 s; with a surface 4.0 s + 1.4 s. A scan
that tuned itself (automatic settle) discards 1 frame instead of 5, about 0.13 s less per
picture. Not measured on hardware.


## 6. Learned models (ML/RL): measured, not shipped

The task allowed learning where it measurably helps. Two places were tried, on the stored
estimates of the runs above (train scenes for fitting, held-out for scoring, 95 % paired
bootstrap over whole scenes):

| Decision | Rule (shipped) | Logistic regression (NumPy, 5–7 features) | Difference |
|---|---|---|---|
| projector moved (≥ 0.75 px) | 99.6 % right; 0 of 98 moves missed, 2 false alarms in 364 | 99.1 %; 4 moves missed, 0 false alarms | −0.4 points (−1.5 to +0.9) |
| surface needs fixing (truth worse than 2.5 px), rule = worst edge > 2.5 px | 83.4 %; 1 of 218 missed, 78 of 258 false alarms | 95.2 %; 8 missed, 15 false alarms | +11.8 points (+4.4 to +20.2) |

For the projector, learning adds nothing. For the surface the logistic model is more often
right, but it gets there by missing 8 outlines that really need fixing against 1 for the
rule, and its gain is concentrated on doors standing out from the wall (rule 62 %, model 91 %;
flat doors 95 % against 98 %). In this product a missed bad outline costs a show night; a
false alarm costs one look. So it is **not shipped**; this is an owner decision (section 10).
A per-camera threshold or a bandit over patterns/exposure (the `calib/learn.py` machinery)
was not built: the rig thresholds have no false alarms and no misses above 0.75 px on
held-out scenes, so there is no regret for a learner to remove, and the exposure is already
chosen by measurement (faint → brighter, clipped → dimmer).


## 7. Self-test on a real rig (`facadekit verify --self-test`)

Nothing has to be moved by hand. The self-test puts known errors into the real optical path
and checks that they come back:

| Case | Injected | Passes when |
|---|---|---|
| rig, nothing changed | nothing | verdict `stable` |
| projector moved +6, 0 px | every projected picture shifted 6 px right in software | `projector_moved` (or `moved`), measured 6 ± 1.5 px |
| projector moved 0, −3 px | shifted 3 px up | measured 3 ± 1 px |
| camera moved 5 px | every camera picture shifted 5 px in software | `camera_moved`, 5 ± 1.25 px, projector stable (SKIP, not FAIL, on a wall with too little texture) |
| surface as drawn | nothing | INFO: the surface's own numbers (SKIP if the object is not clearly seen) |
| outline grown 8 px | the outline drawn 8 px bigger | every side's spill +8 ± 2.5 px against the run without |
| outline shrunk 6 px | 6 px smaller | every side −6 ± 2.5 px |

Surface cases are compared with the run without an error, so a surface that is itself a
little off does not fail the self-test. Self-test runs are not saved as the surface's record.

Procedure (about two minutes, at dusk or in the dark):

1. `cd studio/backend && uv run facadekit serve` and open the projector window on the
   projector, full screen (Setup, step 1).
2. In Setup, choose the camera (the C920), check it and scan. Do not move anything after the
   scan.
3. On Surfaces, Magic-select one clear object (a door or a window) and keep the surface.
4. In a second terminal: `uv run facadekit verify ~/FacadeKit/projects/<project>.fkproj
   --self-test --surface <surface id> --bundle ~/Desktop/facadekit-selftest.zip`
   (the surface id is in the project file, or in the address of `GET
   /api/v1/projects/<project>`).
5. It prints a table with PASS / FAIL / SKIP per case and `SELF-TEST PASSED` or `FAILED`
   (exit code 1 on a failure). The bundle holds the table, every record, the overlay pictures,
   the system versions and the log. The projector is left black.

## 8. Limits

- Simulator only. The simulator's blur is Gaussian, the wall is flat (except the raised-door
  scenes), there is no rolling shutter, lens distortion, moving shadow, wind or DLP colour
  wheel. The thresholds were set on simulator data and should be re-checked with the
  self-test on the owner's rig.
- The check measures against the scan. If the scan's map is wrong somewhere, the check
  inherits it there; at a depth step (an object standing out from the wall) the map is
  smoothed over about ±2 grid nodes, which is the largest remaining error source (section 5).
- The object is found by colour. An object that looks like its surroundings (a white door in
  a white wall) is reported as "cannot see the object's edges" (`warn`, no fix), not guessed.
- A plain wall gives `moved` instead of `camera_moved` / `projector_moved`.
- Another browser tab that drives the projector during a check would spoil it; the Surfaces
  page pauses its own outline display while a check runs.
- Verification needs the laptop camera that made the scan. Phone and Pi scans are refused
  with that reason.

## 9. Interfaces

Flagged additions to `docs/05-interfaces.md` §13 (additive only):

- `POST /api/v1/projects/{pid}/verify` body `{"surface_id"?: str | null, "camera_id"?: str |
  null, "inject"?: {...}}` -> 202 `{"job_id"}`, job kind `"calibration"`, result
  `{"verification": VerificationRecord}` (plus `overlay_jpg_b64` for self-test runs). Camera
  busy -> 409. Manual mode, no scan, or a camera other than a laptop camera -> 422
  `BAD_REQUEST`. Unknown surface -> 404.
- `GET /api/v1/projects/{pid}/verifications/{sid}` (`sid` = surface id or `rig`) ->
  `VerificationRecord`; `GET .../{sid}/overlay.jpg`.
- Files `<project>/verification/<sid>.json` and `.jpg`; calibration files `cam_white.png`,
  `cam_black.png`. project.json is not changed.
- `models/verification.py`: `RigCheck`, `EdgeReport`, `SurfaceCheck`, `VerificationRecord`.
- CLI `facadekit verify PROJECT_DIR [--surface ID] [--sim] [--server URL] [--self-test]
  [--bundle ZIP] [--apply] [--json]`; new error codes `SELF_TEST_FAILED`,
  `SERVER_UNREACHABLE` (CLI only).
- `SimScene` gains `mortar`, `feature_texture`, `raised` (defaults unchanged).

## 10. Open decisions for the owner

1. **Warn on doors that stand out from the wall.** The check says `warn` there even for a
   good outline (its own error is about 2 px at such edges). Keep (honest, current), or accept
   the learned model's looser call (section 6)?
2. **Auto-check after Magic select** is on by default (it flashes test pictures for a few
   seconds). Keep on, or off by default?
3. **Real-hardware thresholds.** Run the self-test (section 7) on the C920 and the PJD6383s and
   send the bundle; thresholds may need adjusting to real noise.

## Hardware

Not run on hardware. On 2026-11-22 this process had no camera permission (AVFoundation
`not_determined`), no C920 and no projector were attached (cameras: the built-in camera and an
iPhone; displays: the laptop and an external monitor), and macOS permissions were not worked
around. Section 7 is the procedure for the first run.
