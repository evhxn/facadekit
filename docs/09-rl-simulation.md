# Scan-strategy selection as a reinforcement-learning problem

Added 2026-11-13. Code: `studio/backend/src/facadekit/sim/rl/` (a development tool; the product does not import it). Data: `studio/backend/rl_data/`. Everything here comes from the simulator. Nothing was measured on a real camera, projector or wall.

## 1. Result in one page

The question was whether a better learner than the shipped one (a Beta-posterior bandit, `calib/learn.py`) can be found by simulating thousands of scan sessions, and whether it should ship.

1. **The shipped bandit is not near-optimal, but the fix is not a better learner.** On scenes held out from all tuning it fails the first scan at 67 % of sites. The best any policy could do is 15 % (those scenes cannot be scanned by any strategy).
2. **Two measurements taken before the scan remove almost all of the gap.** Neither has a learned parameter.
   - *Scale floor:* the pre-check already measures how many camera pixels one projector pixel covers. Strategies whose stripes that number says are unreadable are skipped. Held-out regret per 8 scans 0.992 → 0.382 (difference −0.610, 95 % interval −0.766 to −0.461); first-scan failures 67 % → 39 %.
   - *Stripe probe:* six extra shots show which stripe widths survive the camera's blur. Used together with the scale floor: regret 0.003 (difference −0.989, interval −1.169 to −0.794); first-scan failures 15 %, the same as the oracle.
3. **The reinforcement-learning candidates do not earn their complexity.** Thompson sampling is worse than the shipped policy. Tabular Q-learning over the retry problem is better than shipped, but its result changes from one training run to the next (held-out regret 0.29 to 0.49; the scale floor alone gives 0.38), its maps are the least accurate of the candidates, and it is far behind floor plus probe. A policy tuned over eleven knobs on the train scenes ties the scale floor at one site and is better across several sites, at the cost of constants nobody can explain.
4. **Policies that only maximise the product's reward are a trap.** The reward is coverage minus a time cost. In the simulator a coarser strategy never has lower coverage, so "always use the coarsest strategy" has zero regret, and so do two learned policies that rediscovered it. Their final maps are 1.37 px off (95th percentile, median over scenes) against 0.82 px for the shipped policy.
5. **Recommendation:** ship the scale floor and the stripe probe as a floor under the existing bandit; keep the bandit unchanged for what is left. Do not ship a new learner. Section 7 lists what each choice costs.

Section 8 lists what this simulation cannot tell you.

## 2. The problem as an RL problem

| Element | Definition |
|---|---|
| Step | One scan |
| Observation | What the wizard's pre-check measures before any stripes: camera id, lighting bucket (from white-minus-black contrast), picture scale `s` (camera px per projector px), noise (SNR). Optionally the stripe probe's result. |
| Action | One of the five shipped strategies (`standard`, `low_light`, `soft_focus`, `low_light_soft`, `very_soft`), i.e. a choice of `skip_lsb`, `average_frames`, `min_conf` |
| Outcome | Scan failed, or coverage of the projector map |
| Reward | `calib.learn.reward` without a user verdict: 0 for a failed scan, otherwise `clip((coverage − 0.5) / 0.45) − 0.01 × (average_frames − 1)` |
| State | The scan log so far (the policy is a function of history and observation) |
| Episode, one site | The same scene scanned 8 times with an empty log at the start (as in `sim/learn_eval.py`) |
| Episode, season | Three scenes in a row seen by the same camera, 4 scans each: the camera was moved or the light changed, and what was learned may or may not carry over |

Regret of a scan is the reward of the best strategy for that scene minus the reward of the one chosen; an episode's regret is the sum. One site alone is a contextual bandit; a season makes it a small Markov decision problem, because an early scan changes what the log teaches later.

## 3. Simulator

- **Scenes** (`sim/rl/scenes.py`). Each scene is a small parameter set fed to the existing `SimScene`: camera 1280×720 (about 3 scenes in 4) or 1920×1080 (1 in 4), picture size in the frame (factor 0.22 to 1.0, log-uniform), position, camera blur 0.5 to 5 px, noise 1 to 12, ambient light 0.02 to 0.30, projector gain 0.35 to 1.0, gamma 1.8 to 2.6, door position.
- **Three splits, never mixed.**

| Split | Scenes | Use |
|---|---|---|
| `train` | 148 | Everything fitted or tuned |
| `heldout` | 89 | Same distribution, different seeds. Scored once. |
| `shifted` | 85 | Different distribution: keystoned camera (corners moved up to 7 %), 0 to 2 occluders, ambient 0.10 to 0.45, gain 0.25 to 0.80, picture factor 0.20 to 0.90. Scored once. |

- **Outcome tables** (`sim/rl/outcomes.py`). Every scene is scanned with every strategy through the real `run_calibration`, twice with different camera noise: 3,220 scans, about 14 CPU-hours, run on 16 of the laptop's 18 cores at `nice 5` (about two hours of wall-clock time, shared with other work on the machine). A policy is then replayed against the table in microseconds. Outcomes hardly depend on the noise draw (0.1 % of scans flip between success and failure).
- **Accuracy** is measured against the simulator's ground truth on a 24 px grid of points that are inside the projector frame and were really measured; the 95th percentile, in projector pixels, is stored per scan. Door corners are not used (see section 6).
- **Environment** (`sim/rl/env.py`): `run_episodes(table, policy, episodes)`; a policy is any object with `choose(history, observation, rng) -> arm`.
- **The shipped policy in the comparison is the real one.** `BetaPolicy` with default parameters is checked against `calib.learn.choose` on 400 random histories (`tests/test_sim_rl.py`).
- **Statistics.** Every policy plays the same episodes, so differences are paired. Intervals are 95 % bootstrap intervals; for one-site episodes whole scenes are resampled, because the ten episodes of a scene are not independent.

Scale of the runs: 320 random settings of the Beta family and 27 of the nearest-scene policy were each scored on 444 one-site and 1,500 season episodes of train scenes; Q-learning ran 200,000 episodes; final scoring is ten one-site episodes per scene (890 on held-out) and 3,000 season episodes, per split and per policy.

No GPU and no PyTorch were used. Scanning is OpenCV and NumPy on the CPU, which is where all the time goes; the learners here are tables and a handful of numbers, so there is nothing a GPU would speed up.

## 4. Policies compared

| Name | What it is | Tuned on train? |
|---|---|---|
| shipped | `calib.learn.choose`: Beta posterior per strategy, optimistic priors, greedy, evidence shared across lighting and scale classes | no |
| shipped + Thompson | Same posterior, sample instead of taking the mean | no |
| shipped + UCB(1) | Mean plus one posterior standard deviation | no |
| shipped + discount(0.85) | Older scans count less | no |
| shipped + scale kernel(0.3) | Evidence weighted by distance in log scale instead of two scale classes | no |
| shipped + floor | Skip strategies with `skip_lsb` below `finest_readable_skip(s)` | no |
| shipped + floor + probe | Floor is the larger of the scale floor and the stripe probe's | no |
| ... (slack 1) | Allow one step finer than the floor | no |
| tuned Beta | Best of 320 random settings of eleven knobs (prior strength, mistake weight, sharing weights, discount, selection rule, floor, coarseness cost) | yes |
| fitted priors | Priors set from the mean train reward per lighting bucket (and scale floor) | yes |
| nearest scene | Weighs train scenes by similarity to what was observed, picks the strategy with the best weighted train reward; needs the train table at run time | yes |
| Q-learning | Tabular, state = (lighting bucket, scale floor, per-strategy status at this site), epsilon-greedy, 200,000 train episodes | yes |
| always very_soft | The strategy with the best mean train reward | chosen on train |
| oracle | Knows the table. Not a policy; the bound. | – |

A neural policy (DQN or policy gradient) was not built. The state that matters has a few thousand discrete values, a table covers it, and the table already loses to a rule with no parameters.

## 5. Results

Regret is per episode (8 scans at one site; 12 scans in a season). "vs shipped" is the paired difference with its 95 % interval; negative is better. "Map error" is the 95th-percentile error of the map from the 8th scan in projector px: median over episodes, then mean.

### Held-out scenes (89)

| Policy | One site: regret | vs shipped | First scan fails | Map error (median / mean) | Season: regret | vs shipped |
|---|---|---|---|---|---|---|
| shipped | 0.992 | – | 67 % | 0.82 / 1.33 | 2.147 | – |
| shipped + Thompson | 1.451 | +0.460 [+0.307, +0.608] | 52 % | 0.99 / 1.33 | 2.563 | +0.416 [+0.367, +0.466] |
| shipped + UCB(1) | 0.923 | −0.069 [−0.139, −0.016] | 67 % | 0.83 / 1.29 | 1.722 | −0.425 [−0.450, −0.401] |
| shipped + discount(0.85) | 1.021 | +0.029 [+0.007, +0.057] | 67 % | 0.82 / 1.30 | 2.179 | +0.032 [+0.013, +0.051] |
| shipped + scale kernel(0.3) | 0.992 | 0 (identical at one site) | 67 % | 0.82 / 1.33 | 2.370 | +0.223 [+0.194, +0.251] |
| shipped + floor (slack 1) | 0.737 | −0.255 [−0.381, −0.152] | 60 % | 0.82 / 1.35 | 1.862 | −0.285 [−0.321, −0.249] |
| **shipped + floor** | 0.382 | −0.610 [−0.766, −0.461] | 39 % | 1.03 / 1.56 | 1.033 | −1.114 [−1.159, −1.067] |
| shipped + floor + probe (slack 1) | 0.497 | −0.495 [−0.621, −0.376] | 52 % | 0.83 / 1.43 | 1.424 | −0.723 [−0.767, −0.681] |
| **shipped + floor + probe** | 0.003 | −0.989 [−1.169, −0.794] | 15 % | 1.02 / 1.56 | 0.003 | −2.145 [−2.184, −2.107] |
| tuned Beta | 0.375 | −0.617 [−0.775, −0.468] | 39 % | 1.03 / 1.56 | 0.805 | −1.342 [−1.380, −1.301] |
| Q-learning (three trainings) | 0.287 to 0.494 | −0.705 to −0.498, every interval below 0 | 31 to 49 % | 1.33 to 1.37 | 0.858 to 1.354 | −1.289 to −0.793 |
| nearest scene | 0.000 | −0.992 [−1.179, −0.814] | 15 % | 1.37 / 1.89 | 0.000 | −2.147 [−2.187, −2.110] |
| always very_soft | 0.000 | −0.992 [−1.177, −0.816] | 15 % | 1.37 / 1.89 | 0.000 | −2.147 [−2.185, −2.108] |
| oracle | 0.000 | −0.992 | 15 % | 0.87 / 1.43 | 0.000 | −2.147 |

The fitted-prior policies behave like "always very_soft" (regret 0.005 to 0.017, map error 1.37) and are left out of the table.

### Shifted scenes (85)

| Policy | One site: regret | vs shipped | First scan fails | Map error (median / mean) | Season: regret | vs shipped |
|---|---|---|---|---|---|---|
| shipped | 1.357 | – | 74 % | 0.98 / 1.65 | 2.557 | – |
| shipped + Thompson | 1.640 | +0.283 [+0.113, +0.441] | 54 % | 1.14 / 1.51 | 2.714 | +0.157 [+0.109, +0.206] |
| shipped + UCB(1) | 1.201 | −0.156 [−0.247, −0.077] | 74 % | 0.93 / 1.56 | 2.053 | −0.504 [−0.530, −0.477] |
| shipped + discount(0.85) | 1.500 | +0.144 [+0.077, +0.216] | 74 % | 0.99 / 1.56 | 2.726 | +0.170 [+0.148, +0.190] |
| shipped + scale kernel(0.3) | 1.357 | 0 (identical at one site) | 74 % | 0.98 / 1.65 | 2.871 | +0.314 [+0.287, +0.343] |
| **shipped + floor** | 0.417 | −0.939 [−1.161, −0.733] | 40 % | 1.27 / 1.76 | 0.962 | −1.594 [−1.650, −1.538] |
| **shipped + floor + probe** | 0.044 | −1.313 [−1.555, −1.084] | 16 % | 1.46 / 1.78 | 0.048 | −2.509 [−2.556, −2.465] |
| tuned Beta | 0.430 | −0.927 [−1.150, −0.713] | 40 % | 1.27 / 1.71 | 0.799 | −1.758 [−1.811, −1.710] |
| Q-learning (three trainings) | 0.459 to 0.753 | −0.898 to −0.604, every interval below 0 | 40 to 52 % | 1.62 to 1.81 | 1.050 to 1.835 | −1.507 to −0.722 |
| always very_soft | 0.000 | −1.356 [−1.595, −1.132] | 16 % | 1.94 / 2.24 | 0.000 | −2.556 [−2.602, −2.509] |
| oracle | 0.000 | −1.357 | 16 % | 1.84 / 2.01 | 0.000 | −2.557 |

### By picture scale (held-out, one site; regret, first scan fails)

| Policy | Fine stripes readable (23 scenes) | Needs `skip_lsb` 3 (35) | Needs 4 or more (31) |
|---|---|---|---|
| shipped | 0.887, 39 % | 1.040, 69 % | 1.014, 87 % |
| shipped + floor | 0.887, 39 % | 0.388, 43 % | 0.000, 35 % |
| shipped + floor + probe | 0.000, 0 % | 0.006, 6 % | 0.000, 35 % |
| oracle | 0.000, 0 % | 0.000, 6 % | 0.000, 35 % |

The scale floor does nothing where the picture is large enough (the failures there come from blur, which only the probe sees). Where the picture is small it removes most of the wasted scans.

### Accuracy-priced regret

Because the reward cannot see accuracy, a second measure charges 0.03 of reward per `skip_lsb` step above 2 for scans that worked (`env.ACCURACY_PRICE`; a judgement, not a measurement). On held-out scenes, one site: shipped 0.907; floor 0.395; floor + probe 0.049; always very_soft 0.170; oracle 0. The order of the candidates does not change, except that "always very_soft" and its imitators fall behind floor + probe.

The full tables for all three splits and every policy are in `studio/backend/rl_data/results-2026-11-13.json`. That file is a second run, made from the committed tables. Every policy except Q-learning agrees with the run quoted above to within 0.001; Q-learning does not (0.494 in the quoted run, 0.287 in the file, 0.371 in an earlier run with three more train scenes), which is itself a finding: the table it learns depends on rounding-level differences in its training data.

## 6. Other findings

- **The scale context as built (two classes, evidence from the other class counts half) and its proposed replacement (a distance-weighted discount) do not help.** The kernel variant is identical to the shipped policy at one site and worse across sites on both held-out (+0.223) and shifted scenes (+0.314). This confirms the caution in `docs/07`: the earlier gain was tuned on the scenes it was measured on.
- **A measurement artifact in `sim/learn_eval.py`.** It scored the map at door corners that lie outside the projector frame in some scenes, which made coarse strategies look 9 to 18 px inaccurate. Fixed separately (pull request #22). The first version of this simulation had the same mistake and was rescanned.
- **Coarser stripes are not free.** Where the fine strategy works, its map is about 0.35 px off; `very_soft` is about 1.15 px off in the same scenes (train scenes with a large picture, 95th percentile, median over scenes).
- **`low_light` and `low_light_soft` were never the best strategy** in any of the 322 scenes. Their extra frames cost 0.03 of reward and noise up to 12 grey levels did not make averaging decisive. Dim scenes are few in the train and held-out splits (5 of 148 and 0 of 89 fall in the "low" bucket) but not in the shifted one (17 of 85), and averaging did not win there either. The simulator's noise is plain Gaussian, so this says little about a real dim wall.

## 7. What to ship, and what each choice costs

| Change | Evidence | Cost and risk |
|---|---|---|
| **Scale floor** under the bandit | Better than shipped with confidence on held-out and shifted scenes, one site and season. No new measurement, no parameter beyond the existing `STRIPE_MIN_CAM_PX = 3`. | The final map is a little less accurate (held-out median 1.03 px against 0.82): the floor skips `standard` in scenes where it would have worked at about 92 % coverage with a finer map. Allowing one step finer ("slack 1") keeps the accuracy and gives up more than half of the gain. |
| **Stripe probe** feeding the same floor | First-scan failures at the oracle's level on both splits. Uses the existing `quality.probe_stripes` idea and the existing threshold `MOD_OK = 0.30`. | Six more shots per scan (a few seconds). `MOD_OK` was itself tuned in this simulator (`docs/08` §6), whose blur is a plain Gaussian; on a real lens the threshold may be wrong. If the probe is too pessimistic the scan starts coarser than needed (less accurate, still works); if too optimistic the bandit takes over as today. I thought of this candidate after seeing held-out results, so it is not as clean a test as the others; it has no tuned parameter. |
| UCB or the tuned Beta settings | UCB(1) is better than shipped with confidence, mostly in seasons (−0.425 held-out). The tuned settings add the same on top of the floor in seasons (0.805 against 1.033) and nothing at one site. | Not recommended now. The gain exists only in season episodes, whose shape (3 sites, 4 scans each, a random new scene each time) is my assumption about how people use the tool. The constants would be fitted to a simulator. Revisit with real scan logs. |
| Q-learning, nearest scene, fitted priors, a neural policy | See above. | Not recommended. |

The published `learn_eval` numbers (tuning regret 0.490, held-out 1.143) are not changed by the floor, because that harness calls the policy without a picture scale. Whether they improve when the scale is passed is reported with the change itself (`docs/07`).

## 8. What this cannot tell you

- **It is a simulator.** Blur is Gaussian, the wall is flat, the projector is uniform, there is no moiré, rolling shutter, auto-exposure drift or lens distortion. Every number here is a statement about `SimScene`.
- **The scene distribution is mine.** Two thirds of the scenes have a picture too small for the default strategy and blur goes up to 5 px, which is why the shipped policy fails so often. With a camera close to the wall and in focus, the shipped policy already works on the first scan and none of this matters. The by-scale table is the honest way to read the results.
- **The reward is a proxy.** It cannot see accuracy; the map-error column is there to compensate, and the accuracy price is a judgement.
- **No user feedback.** The simulation never presses "Picture looks wrong".
- **Season episodes are an assumption** about usage.
- **Phone-sized cameras (2560×1440) were left out** of the runs to keep them to a few hours.

## 9. Reproduce

```
cd studio/backend
# score every policy on the committed tables (about 10 minutes)
uv run python -m facadekit.sim.rl evaluate --cache rl_data --out /tmp/results.json
# rebuild a table from scratch (about 1 hour per split on 16 cores)
nice -n 5 uv run python -m facadekit.sim.rl scan --split heldout --n 100 --cache /tmp/rl
```

`rl_data/tuned.json` holds the settings chosen on the train split; delete it to tune again (about 10 more minutes).
