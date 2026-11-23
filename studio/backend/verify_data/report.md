# Verify surface: simulator evaluation

60 scenes (31 train, 14 held out), 15 skipped because their scan failed; 1530 checks run, 0 refused with an error (none).

## Thresholds chosen on the train scenes

- projector moved: smallest threshold with no false alarm on train: 1.35 px (shipped: 0.75 px)
- camera moved: 0.7 px (shipped: 0.7 px)
- surface pass: largest estimated worst edge with no false pass on train: 2.8 px (shipped: 2.5 px)

## Rig check, held-out scenes

Projector moved (camera still), by true shift in projector px:

| true shift | runs | reported as moved |
|---|---|---|
| 0 (none) | 280 | 0.0 % (0/280) |
| 1e-06–0.75 | 14 | 0.0 % (0/14) |
| 0.75–1.5 | 22 | 100.0 % (22/22) |
| 1.5–3 | 20 | 100.0 % (20/20) |
| 3–6 | 14 | 100.0 % (14/14) |
| >= 6 | 14 | 100.0 % (14/14) |

Camera moved (projector still), by true shift in camera px:

| true shift | runs | reported as moved |
|---|---|---|
| 0 (none) | 280 | 0.0 % (0/280) |
| 1e-06–0.75 | 14 | 0.0 % (0/14) |
| 0.75–1.5 | 22 | 100.0 % (22/22) |
| 1.5–3 | 20 | 100.0 % (20/20) |
| 3–6 | 14 | 100.0 % (14/14) |
| >= 6 | 14 | 100.0 % (14/14) |

Which device moved (moves of 1 px or more; rows: truth, columns: verdict):

| truth | stable | camera_moved | projector_moved | both_moved | moved | unknown |
|---|---|---|---|---|---|---|
| stable | 280 | 0 | 0 | 0 | 0 | 0 |
| camera | 0 | 65 | 0 | 2 | 0 | 0 |
| projector | 0 | 0 | 56 | 0 | 0 | 0 |
| both | 0 | 0 | 0 | 25 | 3 | 0 |

Shift estimate error: projector median 0.11 px, 95th pct 0.52 px (95 runs); camera median 0.02 px, 95th pct 0.10 px (95 runs). Camera motion unknown (too little wall texture): 1.5 % (7/476).

## Surface check, held-out scenes

Edge position error, |measured - true| spill per side (projector px):

| scenes | runs | sides measured | median | 90th pct | 95th pct |
|---|---|---|---|---|---|
| all | 476 | 94.8 % (1764/1861) | 0.53 | 2.49 | 3.17 |
| flat door | 306 | 97.9 % (1184/1210) | 0.39 | 0.78 | 0.89 |
| door standing out (parallax) | 170 | 89.1 % (580/651) | 1.84 | 3.40 | 3.58 |
| textured door | 170 | 98.3 % (653/664) | 0.56 | 2.76 | 3.22 |
| plain wall (no brick) | 34 | 97.8 % (133/136) | 1.83 | 2.40 | 2.46 |
| rig stable | 280 | 94.1 % (1021/1085) | 0.54 | 2.45 | 3.18 |
| projector moved | 112 | 95.0 % (418/440) | 0.53 | 2.51 | 3.11 |
| camera moved | 112 | 96.8 % (430/444) | 0.52 | 2.59 | 3.12 |
| auto-exposure camera | 28 | 92.5 % (98/106) | 0.49 | 2.53 | 3.25 |
| dim (ambient 0.35) | 14 | 92.0 % (46/50) | 0.52 | 2.69 | 3.29 |
| bright (gain 1.6) | 14 | 94.6 % (53/56) | 0.55 | 2.40 | 3.07 |

Verdict (rows: truth from the true worst edge, pass <= 2.5 px, fail > 6.0 px; columns: the check):

| truth | pass | warn | fail |
|---|---|---|---|
| pass | 103 | 155 | 0 |
| warn | 0 | 92 | 24 |
| fail | 0 | 6 | 96 |

False pass (truly off by more than 6.0 px, reported pass): 0.0 % (0/102). False fail (truly within 2.5 px, reported fail): 0.0 % (0/258). Agreement: 61.1 % (291/476).

Suggested fix offered in 41.7 % (91/218) of the runs that needed one; corner error after the fix: median 0.96 px, 90th pct 1.31 px, max 3.46 px; the fix was worse than the drawn outline in 0.0 % (0/91).

## Rig check, train scenes

Projector moved (camera still), by true shift in projector px:

| true shift | runs | reported as moved |
|---|---|---|
| 0 (none) | 620 | 0.0 % (0/620) |
| 1e-06–0.75 | 31 | 0.0 % (0/31) |
| 0.75–1.5 | 54 | 98.1 % (53/54) |
| 1.5–3 | 39 | 100.0 % (39/39) |
| 3–6 | 31 | 100.0 % (31/31) |
| >= 6 | 31 | 100.0 % (31/31) |

Camera moved (projector still), by true shift in camera px:

| true shift | runs | reported as moved |
|---|---|---|
| 0 (none) | 620 | 0.0 % (0/620) |
| 1e-06–0.75 | 31 | 0.0 % (0/31) |
| 0.75–1.5 | 47 | 100.0 % (47/47) |
| 1.5–3 | 46 | 100.0 % (46/46) |
| 3–6 | 31 | 100.0 % (31/31) |
| >= 6 | 31 | 100.0 % (31/31) |

Which device moved (moves of 1 px or more; rows: truth, columns: verdict):

| truth | stable | camera_moved | projector_moved | both_moved | moved | unknown |
|---|---|---|---|---|---|---|
| stable | 620 | 0 | 0 | 0 | 0 | 0 |
| camera | 0 | 129 | 0 | 7 | 0 | 0 |
| projector | 0 | 0 | 123 | 0 | 1 | 0 |
| both | 0 | 0 | 0 | 59 | 3 | 0 |

Shift estimate error: projector median 0.08 px, 95th pct 0.44 px (213 runs); camera median 0.02 px, 95th pct 0.11 px (214 runs). Camera motion unknown (too little wall texture): 0.5 % (5/1054).

## Surface check, train scenes

Edge position error, |measured - true| spill per side (projector px):

| scenes | runs | sides measured | median | 90th pct | 95th pct |
|---|---|---|---|---|---|
| all | 1054 | 98.5 % (4084/4145) | 0.37 | 0.94 | 1.86 |
| flat door | 952 | 98.8 % (3725/3769) | 0.33 | 0.79 | 0.92 |
| door standing out (parallax) | 102 | 95.5 % (359/376) | 1.88 | 4.61 | 4.89 |
| textured door | 442 | 97.8 % (1696/1735) | 0.41 | 2.05 | 2.95 |
| plain wall (no brick) | 170 | 100.0 % (679/679) | 0.24 | 0.65 | 0.77 |
| rig stable | 620 | 98.5 % (2400/2437) | 0.36 | 0.93 | 2.04 |
| projector moved | 248 | 98.2 % (964/982) | 0.36 | 0.95 | 1.51 |
| camera moved | 248 | 98.8 % (959/971) | 0.38 | 0.91 | 1.54 |
| auto-exposure camera | 62 | 98.3 % (237/241) | 0.36 | 0.87 | 1.32 |
| dim (ambient 0.35) | 31 | 98.3 % (118/120) | 0.40 | 0.81 | 1.59 |
| bright (gain 1.6) | 31 | 98.4 % (122/124) | 0.35 | 0.87 | 1.63 |

Verdict (rows: truth from the true worst edge, pass <= 2.5 px, fail > 6.0 px; columns: the check):

| truth | pass | warn | fail |
|---|---|---|---|
| pass | 405 | 178 | 3 |
| warn | 0 | 224 | 25 |
| fail | 0 | 3 | 216 |

False pass (truly off by more than 6.0 px, reported pass): 0.0 % (0/219). False fail (truly within 2.5 px, reported fail): 0.5 % (3/586). Agreement: 80.2 % (845/1054).

Suggested fix offered in 81.2 % (380/468) of the runs that needed one; corner error after the fix: median 0.90 px, 90th pct 1.49 px, max 5.42 px; the fix was worse than the drawn outline in 0.3 % (1/380).

## Learned model against the rule (held-out scenes)

- projector moved: held-out accuracy rule 99.6 %, logistic 99.1 %; difference -0.4 points (95 % CI -1.5 to +0.9), 462 runs in 14 scenes.
  - missed (truly positive, called negative): rule 0, logistic 4 of 98; false alarms: rule 2, logistic 0 of 364
  - flat doors only: rule 100.0 %, logistic 99.0 %; doors standing out: rule 98.8 %, logistic 99.4 %
- surface needs fixing (rule: verdict is not pass): held-out accuracy rule 67.4 %, logistic 95.2 %; difference +27.7 points (95 % CI +16.0 to +39.7), 476 runs in 14 scenes.
  - missed (truly positive, called negative): rule 0, logistic 8 of 218; false alarms: rule 155, logistic 15 of 258
  - flat doors only: rule 77.8 %, logistic 97.7 %; doors standing out: rule 48.8 %, logistic 90.6 %
- surface needs fixing (rule: worst edge > PASS_EDGE_PX): held-out accuracy rule 83.4 %, logistic 95.2 %; difference +11.8 points (95 % CI +4.4 to +20.2), 476 runs in 14 scenes.
  - missed (truly positive, called negative): rule 1, logistic 8 of 218; false alarms: rule 78, logistic 15 of 258
  - flat doors only: rule 95.1 %, logistic 97.7 %; doors standing out: rule 62.4 %, logistic 90.6 %

Wall-clock time per check in the simulator (rendering included): median 5.51 s, 95th pct 11.18 s.
