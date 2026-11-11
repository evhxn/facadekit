# Blockers

None open.

## Resolved

### T-202 / R-005: sim e2e door-quad error at the 1.5 px limit (resolved 2026-11-11)

T-202's precheck reads two extra frames for the noise estimate, which shifts the simulator camera's noise stream; `facadekit sim e2e` (limit 1.5 px, unchanged) then reported 1.51 px at seed 0. Over scene seeds 0 to 5 the error had always been about 1.50 px (before T-202: 1.403, 1.44, 1.606, 1.524, 1.536, 1.502; with T-202: 1.51, 1.494, 1.479, 1.536, 1.481, 1.488), so the R-005 margin was never real.

Cause: not the door detector (true map: <= 0.47 px) and not hole filling (99.8 % of projector pixels measured) but a decode bias at reflectance edges. The camera blurs albedo x stripe, so every stripe edge's zero crossing moves towards the darker side (about 1 projector px next to the door's dark frame). The node averaging in `build_maps` turned that into a dipole of map error across every door edge (+0.31 camera px just inside, -0.16 just outside), which pulled all four detected corners inward. Confirmed by noise-free scans: the bias vanishes with `blur_sigma=0` and with a flat albedo.

Fix (`calib/maps.py`, commit "Decode: remove albedo-edge bias from the projector map"): pixels are weighted by `1 / (1 + (|grad log(white - black)| / 0.05)^2)`, weights are pooled per decoded block, and each node is a weighted local affine fit over 5x5 nodes. Door-quad error over seeds 0 to 5 with T-202: 0.725, 0.749, 0.775, 0.767, 0.698, 0.729 (max 0.775, mean 0.740); the remaining ~0.5 px is mostly the door detector's own inward bias. Full write-up, rejected ideas and hardware risks: `docs/07-scan-memory.md`, "Albedo-edge bias in the projector map".

Also part of T-202: `tests/test_calib_learn.py::test_cancelled_and_precheck_failures_are_not_logged` now expects `CAMERA_NO_PROJECTION` for its dim noisy scene (the new projection gate fires first); the test's purpose, that pre-strategy failures are not logged, is unchanged.

### T-037: preview/render parity (resolved)

Cause was two backend issues that masked each other (so the earlier half-pixel experiment alone looked useless):

1. `GET /frame` encoded JPEG with OpenCV's default 4:2:0 chroma subsampling, which smears colour across 2x2 blocks at hard edges (e.g. black next to red at x = 104|105 gave R = 62 in a black pixel). Now 4:4:4.
2. `render/compositor.py` used `cv2.warpPerspective` with pixel-index coordinates (integer = pixel centre) while the homography and WebGL use pixel-corner coordinates, and an alpha rect warped bilinearly against a constant black border (AA ramp at letterbox edges, darkened image border). Now the homography is conjugated by a half-pixel shift, colour uses BORDER_REPLICATE (CLAMP_TO_EDGE), and rect coverage is a hard nearest-sampled mask (matches the shader's discard).

All seven scenes pass with thresholds unchanged.
