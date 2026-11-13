# Scan memory: the wizard learns from failed scans

Added 2026-11-10. Code: `studio/backend/src/facadekit/calib/learn.py`, `api/routes_learning.py`; wizard step 3 (`studio/web/src/pages/setup/StepScan.tsx`).

## What it does

Every laptop-driven scan is written to a local log. Before the next scan, FacadeKit scores a small set of scan strategies against that log and uses the best one for this camera and lighting. A scan that fails, or that the user rejects, lowers its strategy's score, so the next attempt tries something else. A strategy that worked is used again.

With an empty log the wizard behaves exactly as before: the "standard" strategy equals the fixed defaults.

## Why a bandit and not a neural network or full reinforcement learning

- A hobbyist scans a handful of times per season. There is no dataset to train a network on, and a model that needs thousands of episodes would never learn anything.
- The scan is a one-step decision (pick settings, see the result), not a sequence. That is a contextual bandit, the simplest form of reinforcement learning, and it learns from a single mistake.
- It has to run on a laptop with no GPU and no download, and it must be explainable in the UI.

## Strategies (the "arms")

| Id | `skip_lsb` | `average_frames` | `min_conf` | Meant for | Prior Beta(a, b) |
|---|---|---|---|---|---|
| `standard` | 2 | 3 | 0.10 | Normal conditions (the old fixed defaults) | (4, 1) |
| `low_light` | 2 | 6 | 0.06 | Faint or noisy picture | (2, 1) |
| `soft_focus` | 3 | 3 | 0.10 | Camera or projector out of focus: 8 px blocks instead of 4 px | (2, 1) |
| `low_light_soft` | 3 | 6 | 0.06 | Both | (1.5, 1) |
| `very_soft` | 4 | 3 | 0.10 | Badly out of focus: 16 px blocks | (1.5, 1) |

`CalibSettings.min_conf` is new (default 0.10, the value `decode` always used). `Calibration.strategy` records which strategy was used (`null` when scan memory was not involved).

## Context

`(camera_id, lighting bucket)`. The bucket comes from the median white-minus-black contrast measured in the pre-check, before any stripes are shown: below 35 = `low`, below 80 = `medium`, otherwise `high`.

Evidence from other contexts counts less: same lighting but another camera ×0.5, same camera but other lighting ×0.25, neither ×0.

Picture scale (T-205): the record also stores `scale`, `"fine"` when `finest_readable_skip(px_per_proj_px) <= 2` and `"coarse"` otherwise (`null` when unknown, and in every record written before T-205). When the caller passes a scale and a record has a different one, that record's weight is multiplied by `DIFFERENT_SCALE = 0.5` on top of the camera/lighting factor (so the sibling share of a failure gets it too). Records without `scale`, and calls without `scale`, count exactly as before, and an empty log is unchanged. Moving the camera closer after a "too small" warning therefore halves what was learned at the old distance. `remembered_strategy` (Pi and phone scans) passes no scale, because the scale is unknown before the recording. `learn_eval` passes no scale either, so its numbers do not depend on this factor.

## Reward

- Failed scan (`SCAN_FAILED`) or user verdict "bad": 0, and it counts double (`MISTAKE_WEIGHT = 2`).
- Otherwise `clip((coverage − 0.5) / 0.45, 0, 1) − 0.01 × (average_frames − 1)`, plus 0.1 when the user confirmed it.

A failed scan also counts against the strategies with the same or finer blocks (`skip_lsb` ≤ the failed one) at `SIBLING_SHARE = 0.5` of its weight: stripes the camera cannot resolve stay unresolved with more frames or a lower threshold. Not applied in the `low` lighting bucket, where noise is the likelier cause and averaging helps.

Each record adds `weight × reward` to `a` and `weight × (1 − reward)` to `b` of its strategy. The policy picks the highest `a / (a + b)`; ties go to the earlier row of the table. There is no random exploration: the optimistic priors make each untried strategy get its turn after failures, and the same history always gives the same choice.

Worked example, out-of-focus camera (simulator, `SimScene(blur_sigma=4.0)`; test `test_out_of_focus_camera_is_learned`): `standard` fails, then `soft_focus` is tried and works.

Not logged: cancelled scans, and failures before a strategy is chosen (`TOO_BRIGHT`, `CAMERA_NO_PROJECTION`, wrong resolution, camera errors). Those are not the strategy's fault.

## Where the signals come from

| Signal | Source |
|---|---|
| Scan failed | `run_calibration` raises `SCAN_FAILED` after the strategy was chosen |
| Scan quality | `Calibration.quality.coverage` |
| "good" | User presses **Continue to surfaces** |
| "bad" | User presses **Picture looks wrong, try another way** (this also starts a new scan) |
| No opinion | **Scan again** (the projector may simply have moved) |

## Interfaces

```python
# calib/learn.py
STRATEGIES: tuple[Strategy, ...]; get_strategy(id) -> Strategy | None
contrast_bucket(median_contrast) -> "low" | "medium" | "high"
class ScanRecord(BaseModel)          # one line of scans.jsonl; `scale` optional (T-205)
reward(record) -> float
posterior(records, camera_id, bucket, scale=None) -> dict[str, tuple[float, float]]
choose(records, camera_id, bucket, scale=None) -> Strategy
scale_class(px_per_proj_px) -> "fine" | "coarse" | None;  DIFFERENT_SCALE = 0.5
class ScanMemory(path): records(), append(record), set_verdict(calibration_id, verdict) -> bool, clear()
class ScanAdvisor(memory): choose(camera_id, pre) -> Strategy; succeeded(...); failed(...)
learning_enabled() -> bool           # env FACADEKIT_LEARN=0 turns it off

# calib/pipeline.py
run_calibration(..., advisor: ScanAdvisor | None = None)
```

| Route | Purpose |
|---|---|
| `POST /api/v1/projects/{pid}/calibrations` | New body field `learn: bool = true`. Scan memory is used for `mode: "scan"` and `"scan_pi"` unless `learn` is false, `FACADEKIT_LEARN=0`, or `settings` explicitly sets `skip_lsb`, `average_frames` or `min_conf`. |
| `POST /api/v1/projects/{pid}/calibrations/{cid}/feedback` | Body `{"verdict": "good" \| "bad"}` → `{"recorded": bool}` |
| `GET /api/v1/learning/scans` | `{"enabled", "scans", "strategies": [{"id", "label", "tries", "mistakes", "mean_reward"}]}` |
| `DELETE /api/v1/learning/scans` | Forget everything (204) |

Storage: `<data dir>/learning/scans.jsonl`, one JSON object per line, trimmed to the newest 500 on rewrite. Nothing is uploaded.

## Tuning results (2026-11-11)

Harness: `studio/backend/src/facadekit/sim/learn_eval.py` (`uv run python -m facadekit.sim.learn_eval --n 18 --cache <file>`). It builds 18 synthetic scenes (blur 0.5-5 px, noise 1-12, ambient 0.02-0.3, gain 0.35-1, gamma 1.8-2.6, door position shifted), scans each with every strategy, then replays the real policy 8 times per scene on the stored outcomes. Scenes with `seed % 3 == 0` (6) are held out and were not looked at while choosing changes; the other 12 are tuning scenes. Regret = reward of the best strategy minus the reward of the one chosen, summed over 8 scans of the same camera, averaged over scenes. "Gap" = same measure on the 8th scan.

| | Before (4 strategies) | After |
|---|---|---|
| Tuning regret | 0.783 | 0.490 |
| Held-out regret | 1.318 | 1.143 |
| Held-out final-scan gap | 0.021 | 0.000 |
| First scan ok (tuning / held-out) | 7/12, 1/6 | unchanged (needs a blur estimate before the scan) |
| Easy scenes (blur ≤ 1.5): strategy | `standard` | `standard`, unchanged |

Corner error (map vs ground truth, px) for scenes `standard` can scan stays 0.3-1.1 px. Sim e2e: 1.40 px before and after (limit 1.5). (Both improved later by the albedo-edge fix, see the next section: 0.1-0.6 px and 0.74 px; the regret numbers did not change.)

Kept: (1) failure sharing to same-or-finer strategies, which removes the wasted `low_light` scan for blurred cameras (the two wasted scans became one); (2) `very_soft` (skip_lsb 4): blur 5 px scenes had only a 58-66 % coverage option, now 100 % at 1-1.3 px.

Tried and rejected: `min_conf` 0.3 or 0.5 (every scan fails: confidence of blurred stripes is low); `skip_lsb=2, min_conf=0.3` (same); sharing failures without the same-or-finer rule (standard got retried a third time); very_soft prior 1.2 (standard was retried before it).

Not solved: coverage is 100 % even when the corner error is 9-18 px (scenes 1005, 1007, 1008 with `soft_focus`), so the reward cannot see that kind of inaccuracy; only the user's "looks wrong" does.

**Correction (2026-11-13, AUDIT-02 follow-up).** That inaccuracy was a fault of the harness, not of the scans. In scenes 1005, 1007 and 1008 the randomly shifted door reaches below the projector frame (lower corners at y = 1087-1110 on a 1080-line projector), and the harness compared the map with the truth at those corners, where no map exists and the value is an extrapolation. Inside the frame the `soft_focus` and `very_soft` maps of these scenes are accurate to a fraction of a pixel. `learn_eval` now scores only door corners inside the projector frame (`door_corners_in_frame`; a scene with none shows `-`). Regret, first-scan and gap numbers are unaffected: they use the reward, which never used the corner error. The sentence above about the reward being blind to inaccuracy remains true in principle, but these three scenes are not evidence for it. Dim scenes where only more averaging helps did not occur in the synthetic set, so the `low` bucket exemption is untested against real data.

## Albedo-edge bias in the projector map (2026-11-11)

Code: `calib/maps.py` (`albedo_edge_weight`, `_block_measurements`, `_local_fit`, `build_maps`). Tests: `tests/test_calib_albedo_edge.py`. Decode itself is unchanged.

### Symptom

`facadekit sim e2e` measured a door-quad error of about 1.50 px averaged over scene seeds 0-5 (limit 1.5 px). With a noise-free photo the door detector is within 0.47 px on the true map but 1.49 px on the scanned map, all four corners pulled inward. 99.8 % of projector pixels are measured, so hole filling was not the cause. The scanned-minus-true map error was a dipole across every door edge: +0.27 to +0.31 camera px (outward) just inside the edge, -0.10 to -0.16 just outside, zero by 24 projector px.

### Mechanism (confirmed)

The camera image of a stripe is `PSF * (albedo x stripe)`. Decode compares pattern and inverse per pixel, i.e. takes the zero crossing of `PSF * (albedo x (2 stripe - 1))`. Where the albedo changes within the blur radius, the brighter side dominates and the crossing moves into the darker side. For a Gaussian PSF and a locally linear albedo the shift is `-sigma^2 * grad log(blurred albedo)` (first order; checked numerically in 1D, exact peak shift for a 0.12 -> 0.78 step is 1.1 camera px at sigma 1). The pixels just outside a bright door therefore decode as door pixels and vice versa, and `build_maps` averaged these pixels into its nodes.

Experiments (noise-free scans, default scene geometry; decode x-bias averaged along the door's left edge, in projector px; map dipole = largest bin of the edge-normal map error, camera px):

| Scan | decode bias next to the frame | map dipole (before) | door (clean photo, scanned map) |
|---|---|---|---|
| blur 1 (default) | -0.64 / +1.09 | +0.31 / -0.16 | 1.49 px |
| blur 0 | -0.11 / +0.11 | +0.06 / -0.05 | 0.75 px |
| blur 1, flat albedo | -0.06 / -0.05 | +0.04 / -0.07 (not at edges) | 0.75 px |
| blur 0.5 | -0.17 / +0.27 | | 0.84 px |
| blur 1.5 | -1.06 / +1.76 | | 2.53 px |

The bias scales with the blur and disappears without blur or without albedo edges. With noise (sigma 2) the profile is the same.

### Fix

1. **Weight by albedo edges.** `w = 1 / (1 + (|grad log(white - black)| / 0.05)^2)` per camera pixel (3x3 Sobel). The white frame is smooth light, so the log-gradient of white minus black is the log-gradient of the blurred albedo, which is what drives the bias. It needs no estimate of the blur, the camera gamma or exposure (log is scale-free), and also flags shadow and occlusion edges, where decoding is biased for the same reason.
2. **One measurement per decoded block.** All pixels of a block decode to the same projector coordinate, so the block's camera centroid is the measurement and its weight is the sum of its pixel weights. Weighting pixels unevenly inside a block (tried first) moves the centroid away from the edge and produced the opposite dipole (-0.13 / +0.08).
3. **Weighted local affine fit per node.** Each node is the intercept of a weighted least-squares fit `centroid = c + J (p - node)` over the blocks of the 5x5 surrounding nodes (Gaussian, sigma 1 node). The slopes `J` get a weak ridge prior towards the global homography's (strength 1 against data weights of hundreds), which only matters where data is sparse. Nodes are never snapped to the plane: with strong barrel distortion (plane fit off by up to 3 px) the map error is 0.03 px mean (0.11 px with the old method); test `test_follows_lens_distortion_not_the_plane_fit`. Holes beyond the fit radius are filled as before.

`measured` and `coverage` are computed exactly as before, so scan-memory rewards are unchanged.

### Results

Sim e2e door-quad error (max corner error, px), scene seeds 0-5, with the T-202 precheck in place:

| | 0 | 1 | 2 | 3 | 4 | 5 | max | mean |
|---|---|---|---|---|---|---|---|---|
| before | 1.510 | 1.494 | 1.479 | 1.536 | 1.481 | 1.488 | 1.536 | 1.498 |
| after | 0.725 | 0.749 | 0.775 | 0.767 | 0.698 | 0.729 | 0.775 | 0.740 |
| map at door corners (b), before | 0.32 | 0.71 | 0.31 | 0.60 | 0.44 | 0.55 | | |
| map at door corners (b), after | 0.08 | 0.10 | 0.12 | 0.07 | 0.10 | 0.10 | | |

Coverage 99.8 % on every seed, before and after (printed as 100 %). The remaining ~0.5 px is mostly the door detector's own inward bias (0.47 px with the true map and a noise-free photo).

Map error, default scene with noise: mean 0.17 -> 0.09 camera px, p99 0.41 -> 0.28; edge dipole within +-0.04 (was +0.31 / -0.16). Synthetic panel scene (`PanelScene` in the tests, bright panel in a dark frame on a grey wall): dipole peak-to-peak 0.46 -> 0.07 camera px (6.7x); flat wall 0.10 -> 0.05.

Scan memory harness (`learn_eval --n 18`), before -> after: tuning regret 0.490 -> 0.490, held-out regret 1.143 -> 1.143, final-scan gap 0.000 -> 0.000, first scan ok 7/12 and 1/6 unchanged, every coverage cell identical. No re-tuning. Corner errors fell where the scan is sound, e.g. 1000 `soft_focus` 1.5 -> 0.3 px, 1004 `standard` 0.6 -> 0.2, 1012 `standard` 1.0 -> 0.6, 1013 `low_light_soft` 1.8 -> 0.8, 1002 `low_light_soft` 4.0 -> 0.8, 1011 `standard` 0.8 -> 0.2. The 6-26 px outliers (1005, 1007, 1008, 1012, 1017 with coarse strategies) are unchanged: they are wrong decoded bits, a different problem (see "Not solved" above).

Runtime on the sim scan (1280x720 camera, 1080p projector, best of 9): `build_maps` 28.4 -> 31.8 ms (+12 %), decode + build_maps 67 -> 71 ms (+5 %); the capture itself takes seconds.

### Rejected ideas

- **Ratio decoding** (`(pattern - inverse) / (white - black)`): dividing by a positive number does not move a zero crossing, so the decoded bits are identical.
- **First-order shift correction** (move each pixel by `k grad log(contrast)`, `k = sigma^2`): with k = 1 it removed the dipole on the default scene (door 0.63-0.77 px), but k must track the camera's blur (k = 4 was needed at blur 2) and its gamma. Estimating k by regressing residuals on `grad log(contrast)` gave 1.2 at skip_lsb 2 and 2.7 at skip_lsb 3 for the same camera (block quantisation leaks into the regression), so it is not reliable enough for real cameras.
- **Per-pixel weights** (no block pooling): over-corrects, see Fix 2.
- **Narrower fit** (3x3 nodes, sigma 2/3 node): panel-scene dipole 0.12 px peak-to-peak against 0.07 px for 5x5, so 5x5 is kept. The weight scale matters little: 0.02-0.08 changes the dipole by about 0.01 px.
- **Snapping nodes to the plane fit**: hides lens distortion and non-planar walls.
- **A gradient-based sub-pixel door-edge fit** (earlier attempt, T-202): fixed two corners, worsened others; the bias was in the map, not the detector.

Not pursued: sub-block refinement from the finest modulated bit (its zero crossing has the same albedo bias), modulation-depth weights (low-modulation pixels are already dropped by `min_conf`).

### Risks on real hardware

- The weight trusts that the projector's white frame is smooth. Projector vignetting is smooth (log-gradient far below 0.05 per camera px). A sharp camera that resolves the projector's pixel grid or a DLP screen-door pattern lowers weights everywhere about equally, which does not bias the fit.
- Walls that are textured everywhere (rough brick, stucco) lower most weights. The fit then behaves like the old unweighted average; random albedo gives biases of both signs that mostly cancel. Regular strong patterns (mortar lines every few cm) leave a small residual.
- The fit spans +-2 nodes (16 projector px at `skip_lsb` 2, 64 px at `very_soft`). A depth step (door recess) with a large camera-projector baseline is smoothed over that width. The old 3x3 median and cell averaging smoothed over a similar span.
- The 0.05 scale was chosen on the simulator. It is not critical there (0.02-0.08 equal), but has not been checked against real webcam noise; a very noisy dim scan raises `|grad log contrast|` from noise alone and so lowers all weights, which again only reduces to the old behaviour.

## Picture scale as context (T-205)

`learn_eval --n 18`, before -> after adding `scale`: tuning regret 0.490 -> 0.490, held-out regret 1.143 -> 1.143, final-scan gap 0.000 -> 0.000, first scan ok 7/12 and 1/6 unchanged, every outcome cell identical. This is by construction: the harness calls `choose` without a scale, which behaves exactly as before. The factor is covered by unit tests (`test_calib_learn.py`), not by the harness.

### Measured: far and phone scenes (2026-11-11)

`learn_eval` now also runs a far/phone group (`far_scenes()`: the default scene as `near`, `scene_far_marginal`, `scene_far_too_coarse`, `scene_phone`; `--no-far` skips it). None of these were used for tuning. The 18 old scenes are replayed exactly as before (no scale): tuning regret 0.490, held-out 1.143, final-scan gap 0.000, first scan ok 7/12 and 1/6, all unchanged.

| Scene | Scale | standard | low_light | soft_focus | low_light_soft | very_soft |
|---|---|---|---|---|---|---|
| near | fine | 1.00 / 0.1 px | 1.00 / 0.1 | 1.00 / 0.1 | 1.00 / 0.1 | 1.00 / 0.6 |
| far_marginal | coarse | 0.92 / 0.3 | 0.97 / 0.2 | 1.00 / 0.0 | 1.00 / 0.1 | 1.00 / 0.4 |
| far_too_coarse | coarse | FAIL | FAIL | 0.93 / 0.3 | 0.97 / 0.3 | 1.00 / 0.2 |
| phone | fine | 1.00 / 0.1 | 1.00 / 0.1 | 1.00 / 0.1 | 1.00 / 0.1 | 1.00 / 0.8 |

Per scene (one camera, empty log, 8 scans): mean regret 0.456 without and 0.456 with scale, first scan ok 3/4, final-scan gap 0.028 (`far_marginal` stays on `standard` at 92 % coverage). With and without are identical by construction: every scan of one scene has the same scale class, so the factor never applies.

Moves (the same 1280x720 camera scans one scene, is moved to another distance, then scans 8 times there; regret after the move, without -> with scale; `phone` is a different camera and is not used for moves):

| Move | Scales | 2 scans before | 8 scans before |
|---|---|---|---|
| near -> far_marginal | fine -> coarse | 0.550 -> 0.550 | 0.550 -> 0.550 |
| near -> far_too_coarse | fine -> coarse | 1.272 -> 1.272 | 3.149 -> 2.211 |
| far_marginal -> near | coarse -> fine | 0 -> 0 | 0 -> 0 |
| far_marginal -> far_too_coarse | coarse -> coarse | 1.272 -> 1.272 | 2.211 -> 2.211 |
| far_too_coarse -> near | coarse -> fine | 0 -> 0 | 0 -> 0 |
| far_too_coarse -> far_marginal | coarse -> coarse | 0 -> 0 | 0 -> 0 |
| Mean | | 0.516 -> 0.516 | 0.985 -> 0.829 |

Verdict: the scale context helps in one case of six, and only after a long history. After 8 good `standard` scans up close, moving the camera to `far_too_coarse` costs 3 failed scans without scale and 2 with it (one failed scan saved). After 2 scans it changes nothing (the one failure already outvotes the history). It never made a choice worse here. Why the benefit is small in the simulator:

- Moving closer needs no unlearning: the coarse strategies learned far away also score full reward up close (coverage 1.00, corner error 0.1 px), so there is no regret to remove.
- `far_marginal` is "coarse" but `standard` still works there (92 % coverage), so nothing fails and the context has nothing to discount; the regret 0.550 is the coverage loss, which the factor cannot see.
- Two classes are too coarse to separate `far_marginal` from `far_too_coarse` (both "coarse"), which is the move with the second-largest regret (2.211).

No thresholds were changed for these numbers. Test: `test_moving_far_away_unlearns_faster_with_scale` (`tests/test_sim_learn_eval.py`) checks the 3 -> 2 failure case on synthetic records.

## Scale floor (2026-11-13)

Before the bandit picks, strategies whose stripes the camera cannot resolve are set aside. This is a measurement, not something learned.

- The pre-check already measures `px_per_proj_px`, the number of camera pixels one projector pixel covers. `quality.finest_readable_skip` turns it into the finest `skip_lsb` whose stripes still span `STRIPE_MIN_CAM_PX = 3` camera pixels.
- `learn.allowed_strategies(floor)` keeps the strategies with `skip_lsb >= floor` (the coarsest ones if none qualify). `learn.choose(..., floor=)` computes the posterior exactly as before and picks only among those. `ScanAdvisor.choose` passes `scale_floor(pre["px_per_proj_px"])`.
- No floor when the scale was not measured, so Pi-driven and phone scans (`remembered_strategy`) behave as before.
- `ScanRecord` gains the optional `px_per_proj_px` (left out of the log line when unknown, like `scale`).
- The wizard's note under a finished scan now says the strategy was chosen "because the picture is small in the camera view or earlier scans with this camera had trouble", and knows `very_soft`.

Why: `docs/09-rl-simulation.md`. On 89 scenes held out from all tuning, regret per 8 scans at one site falls from 0.992 to 0.382 (difference −0.610, 95 % interval −0.766 to −0.461) and first-scan failures from 67 % to 39 %; on 85 scenes from a different distribution, −0.939 (−1.161 to −0.733) and 74 % to 40 %. A test (`test_sim_rl.py`) checks that the simulated floor and this code make the same choice on 400 random histories.

The harness of this document, which was not used in that simulation (`learn_eval --n 18`, outcomes rescanned 2026-11-13):

| | Before | With the scale floor |
|---|---|---|
| Tuning regret (12 scenes) | 0.490 | 0.490 |
| Held-out regret (6 scenes) | 1.143 | 1.143 |
| First scan ok (tuning / held-out) | 7/12, 1/6 | 7/12, 1/6 |
| Far/phone group, mean regret per scene | 0.456 | 0.000 |
| `far_marginal` first pick | `standard` (92 % coverage) | `soft_focus` (100 %) |
| `far_too_coarse` first pick | `standard` (fails) | `very_soft` (100 %) |
| Mean regret after a move, 2 scans before | 0.516 | 0.000 |
| Mean regret after a move, 8 scans before | 0.829 | 0.000 |

The 18 old scenes do not change because the picture fills the frame in all of them (floor 2, nothing is set aside); their failures come from blur, which the scale cannot see. The six camera moves, where the two-class scale context helped once, all reach zero regret: the floor is recomputed from the new picture on every scan, so there is nothing to unlearn. The scale-class discount (`DIFFERENT_SCALE`) is left in place; with the floor it no longer changes any of these numbers.

Cost: the floor trusts the 3-pixel rule. Where a finer strategy would still have worked (for example `standard` at 92 % coverage in `far_marginal`), the scan now starts one step coarser. In the simulation the final map of the held-out scenes is 1.03 px off (95th percentile, median over scenes) with the floor against 0.82 px without it.

## Stripe probe in the pre-check (2026-11-13)

The scale floor cannot see blur: a large picture from an out-of-focus camera still starts on `standard` and fails. The pre-check now measures blur directly.

- After its black and white shots, `capture.precheck(..., probe=True)` projects the finest stripes of `skip_lsb` 2, 3 and 4 (pattern and inverse: six shots) and measures how much of their contrast survives (`quality.probe_modulation`, the same measure the camera check uses). `quality.blur_floor` is the finest of the three that keeps `MOD_OK = 0.30`, or 5 when none does. The pre-check returns it as `blur_floor`, with the three `modulation_<bit>` values.
- `run_calibration` asks for the probe whenever an advisor is in use. `learn.readable_floor(pre)` is the larger of the scale floor and the probe's; `ScanAdvisor.choose` uses that. `ScanRecord` gains the optional `blur_floor`.
- Scans without an advisor (learning off, or strategy fields set by the caller) take no probe and behave as before. Pi-driven and phone scans take no probe: their patterns are fixed before the camera sees anything.

Why (`docs/09-rl-simulation.md`): with both floors, on the 89 held-out scenes regret per 8 scans is 0.003 (shipped 0.992; difference −0.989, 95 % interval −1.169 to −0.794) and the first scan fails at 15 % of sites, which is the share no strategy can scan at all. On the 85 shifted scenes: 0.044 against 1.357, and 16 %.

End-to-end check, not a replay: the first scan of all 89 held-out scenes was run through the real `run_calibration` with a fresh `ScanAdvisor`. It chose the same strategy as the simulated policy in 89 of 89 scenes and failed in 13 (15 %), as predicted. First strategies: `very_soft` 55, `soft_focus` 22, `standard` 12.

Costs and limits:

- Six more shots before every learned scan (each takes the settle time plus the averaged frames, so a few seconds in total).
- `MOD_OK` was tuned in the simulator (`docs/08` §6), whose blur is a Gaussian. On a real lens the threshold may be off. Too pessimistic: the scan starts coarser than needed and the map is less accurate, but it works. Too optimistic: the first scan fails and the log takes over, as before.
- The probe uses x-axis stripes only.
- I proposed this after seeing held-out results of the simulation, so its test is less clean than the scale floor's. It has no tuned parameter.
- The out-of-focus test that used to show "one failure, then the wizard adapts" now needs an advisor that ignores the probe (`_NoProbe` in `test_calib_learn.py`); with the probe that failure no longer happens (`test_out_of_focus_camera_is_seen_before_the_scan`).

## Bug fixed on the way

`build_maps` used an 8 px grid for any `skip_lsb`. With `skip_lsb=3` every decoded block centre fell exactly halfway between two grid nodes and three nodes in four stayed empty (coverage 25 %). The grid step now defaults to `max(8, 2 × block)` and must be a multiple of two blocks.

## Limits

- Tested only in the simulator. Whether these four strategies are the right ones for real webcams and real walls is unknown until field scans (spike S-5).
- Coverage is a proxy for accuracy. A scan can cover everything and still be slightly off; only the user's "looks wrong" catches that.
- Pi-driven scans learn from the lighting of the previous scan only (see below): the first Pi scan of a camera, and the first after the lighting changed bucket, still use a strategy chosen for the old lighting.
- Learning is per computer. Sharing results between users would need consent and a server, and is out of scope.

## Learning from alignment corrections (2026-11-11)

Code: `calib/learn_align.py`, routes in `api/routes_learning.py`, hook in `POST /projects/{pid}/realign`.

**Signal.** The backend never sees the user dragging corners after Quick re-align (the editor saves whole projects), so the signal is what re-align measures itself: `max_shift_px`, the largest distance any surface corner moved when the estimated drift was applied. `reproj_error_px` and `inliers` are stored too but drive nothing. Only applied re-aligns (`apply: true`) are logged; previews are not.

**Log.** `<data dir>/learning/realign.jsonl`, one JSON object per line (`id, at, project_id, max_shift_px, reproj_error_px, inliers`), newest 500 kept, nothing uploaded. `FACADEKIT_LEARN=0` stops both writing and warning.

**Use.** Warning threshold = `clamp(2 x median(max_shift_px of the last 20), 40 px, 160 px)`. Bounds: never below 40 px (`WARN_DEFAULT_SHIFT_PX`), never above 4 x that (`WARN_CAP_SHIFT_PX`). Fewer than 5 records (`MIN_SAMPLES`), including an empty log: no threshold and no warning, exactly the old behaviour. When a re-align (preview or apply) exceeds the threshold, `RealignResponse.warning` carries a message; otherwise it is `null`. `MAX_REPROJ_ERROR_PX` (4 px) is not touched: it stays a hard refusal and is never loosened or tightened by the log.

| Route | Purpose |
|---|---|
| `GET /api/v1/learning/realign` | `{"enabled", "corrections", "median_shift_px", "warn_threshold_px"}` |
| `DELETE /api/v1/learning/realign` | Forget the re-align history (204) |

Limit: the 40 px floor is a guess for 1080p projectors; tune it with field data. The wizard does not show `warning` yet.

## Pi-driven scans (2026-11-11)

Code: `ScanAdvisor.remembered_strategy`, `run_calibration_from_capture(..., strategy, advisor)`, `_submit_pi_scan` in `api/routes_calibration.py`.

A Pi scan renders the pattern video (which depends on `skip_lsb`) before the camera sees anything, so the lighting cannot be measured first. Rule: the bucket is the one stored on the newest scan-log record with the same `camera_id` (laptop or Pi); `choose(records, camera_id, that bucket)` picks the strategy, which sets `skip_lsb`, `average_frames` and `min_conf`. With no record for the camera, the "standard" strategy is used, i.e. the fixed defaults, exactly as before.

After the capture, the contrast is measured from the recorded black and white images, the bucket is derived from it as for laptop scans, and the result is logged under the strategy that was used (`succeeded`/`failed`, same reward, same `POST .../feedback`). So a Pi scan done in different light than the remembered one is logged in its real bucket and corrects the next guess. Only `SCAN_FAILED` from decoding or map building counts against the strategy. Not logged: a lost marker or recording problem, `CAMERA_NO_PROJECTION`, cancellation, and render/upload errors, as these are not the strategy's fault.

Same opt-outs as laptop scans: `learn: false`, `FACADEKIT_LEARN=0`, or explicit `skip_lsb`, `average_frames` or `min_conf` in `settings`. No new endpoint fields (the existing `learn` flag now also covers `scan_pi`).

## Damaged logs and several writers

Code: `calib/jsonl_store.py`, shared by `ScanMemory` and `AlignMemory`. The log is read as bytes and decoded with replacement, so invalid UTF-8 never raises: undecodable or invalid lines are skipped and the valid ones load. Rewrites go through a unique temp file in the same directory and `os.replace`; the temp file is removed on error. Appends and rewrites take a lock file (`<log>.lock`, exclusive create, standard library only, same on Windows, macOS and Linux) so two instances sharing a data dir keep each other's records. Remaining limitation: the lock is best effort. If it cannot be taken within 2 s, or a crashed process left it behind (a lock older than 10 s is broken), the write goes ahead unlocked and can in rare cases lose one record. A plain append is not rewritten, so a crash mid-append can leave one damaged line, which is skipped on read.

## Next steps (not built)

1. ~~Learn from alignment corrections~~ (built, see below).
2. ~~Add `scan_pi` using a bucket remembered from the previous scan with that camera~~ (built, see below).
3. A "What FacadeKit has learned" panel in the wizard's Advanced section, using `GET /learning/scans`, with a Forget button.
