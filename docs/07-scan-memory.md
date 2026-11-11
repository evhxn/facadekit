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

`CalibSettings.min_conf` is new (default 0.10, the value `decode` always used). `Calibration.strategy` records which strategy was used (`null` when scan memory was not involved).

## Context

`(camera_id, lighting bucket)`. The bucket comes from the median white-minus-black contrast measured in the pre-check, before any stripes are shown: below 35 = `low`, below 80 = `medium`, otherwise `high`.

Evidence from other contexts counts less: same lighting but another camera ×0.5, same camera but other lighting ×0.25, neither ×0.

## Reward

- Failed scan (`SCAN_FAILED`) or user verdict "bad": 0, and it counts double (`MISTAKE_WEIGHT = 2`).
- Otherwise `clip((coverage − 0.5) / 0.45, 0, 1) − 0.01 × (average_frames − 1)`, plus 0.1 when the user confirmed it.

Each record adds `weight × reward` to `a` and `weight × (1 − reward)` to `b` of its strategy. The policy picks the highest `a / (a + b)`; ties go to the earlier row of the table. There is no random exploration: the optimistic priors make each untried strategy get its turn after failures, and the same history always gives the same choice.

Worked example, out-of-focus camera (simulator, `SimScene(blur_sigma=4.0)`; this is the test `test_out_of_focus_camera_is_learned`): `standard` fails, `low_light` fails, `soft_focus` measures 71 %, and after one more mediocre result `low_light_soft` is tried and measures 99 %.

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

## Bug fixed on the way

`build_maps` used an 8 px grid for any `skip_lsb`. With `skip_lsb=3` every decoded block centre fell exactly halfway between two grid nodes and three nodes in four stayed empty (coverage 25 %). The grid step now defaults to `max(8, 2 × block)` and must be a multiple of two blocks.

## Limits

- Tested only in the simulator. Whether these four strategies are the right ones for real webcams and real walls is unknown until field scans (spike S-5).
- Coverage is a proxy for accuracy. A scan can cover everything and still be slightly off; only the user's "looks wrong" catches that.
- Pi-driven scans (`mode: "scan_pi"`) are not learned yet: the pattern video is rendered before the lighting is measured.
- Learning is per computer. Sharing results between users would need consent and a server, and is out of scope.

## Next steps (not built)

1. Learn from alignment corrections: record how far the user drags surface corners after Quick re-align (`calib/realign.py`) and use the median as a warning threshold, or to tune `MAX_REPROJ_ERROR_PX`.
2. Add `scan_pi` using a bucket remembered from the previous scan with that camera.
3. A "What FacadeKit has learned" panel in the wizard's Advanced section, using `GET /learning/scans`, with a Forget button.
