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
class ScanRecord(BaseModel)          # one line of scans.jsonl
reward(record) -> float
posterior(records, camera_id, bucket) -> dict[str, tuple[float, float]]
choose(records, camera_id, bucket) -> Strategy
class ScanMemory(path): records(), append(record), set_verdict(calibration_id, verdict) -> bool, clear()
class ScanAdvisor(memory): choose(camera_id, pre) -> Strategy; succeeded(...); failed(...)
learning_enabled() -> bool           # env FACADEKIT_LEARN=0 turns it off

# calib/pipeline.py
run_calibration(..., advisor: ScanAdvisor | None = None)
```

| Route | Purpose |
|---|---|
| `POST /api/v1/projects/{pid}/calibrations` | New body field `learn: bool = true`. Scan memory is used for `mode: "scan"` unless `learn` is false, `FACADEKIT_LEARN=0`, or `settings` explicitly sets `skip_lsb`, `average_frames` or `min_conf`. |
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

Corner error (map vs ground truth, px) for scenes `standard` can scan stays 0.3-1.1 px. Sim e2e: 1.40 px before and after (limit 1.5).

Kept: (1) failure sharing to same-or-finer strategies, which removes the wasted `low_light` scan for blurred cameras (the two wasted scans became one); (2) `very_soft` (skip_lsb 4): blur 5 px scenes had only a 58-66 % coverage option, now 100 % at 1-1.3 px.

Tried and rejected: `min_conf` 0.3 or 0.5 (every scan fails: confidence of blurred stripes is low); `skip_lsb=2, min_conf=0.3` (same); sharing failures without the same-or-finer rule (standard got retried a third time); very_soft prior 1.2 (standard was retried before it).

Not solved: coverage is 100 % even when the corner error is 9-18 px (scenes 1005, 1007, 1008 with `soft_focus`), so the reward cannot see that kind of inaccuracy; only the user's "looks wrong" does. Dim scenes where only more averaging helps did not occur in the synthetic set, so the `low` bucket exemption is untested against real data.

## Bug fixed on the way

`build_maps` used an 8 px grid for any `skip_lsb`. With `skip_lsb=3` every decoded block centre fell exactly halfway between two grid nodes and three nodes in four stayed empty (coverage 25 %). The grid step now defaults to `max(8, 2 × block)` and must be a multiple of two blocks.

## Limits

- Tested only in the simulator. Whether these four strategies are the right ones for real webcams and real walls is unknown until field scans (spike S-5).
- Coverage is a proxy for accuracy. A scan can cover everything and still be slightly off; only the user's "looks wrong" catches that.
- Pi-driven scans (`mode: "scan_pi"`) are not learned yet: the pattern video is rendered before the lighting is measured.
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

## Next steps (not built)

1. ~~Learn from alignment corrections~~ (built, see below).
2. Add `scan_pi` using a bucket remembered from the previous scan with that camera.
3. A "What FacadeKit has learned" panel in the wizard's Advanced section, using `GET /learning/scans`, with a Forget button.
