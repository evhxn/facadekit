# Blockers

## T-037: preview/render parity failures (real backend compositor bug, thresholds NOT loosened)

Scenes `stretch-trapezoid`, `fit-wide`, `canvas` fail the metric (< 0.5 % of pixels differing by > 24); the other four pass.
Diff images are written by `cd studio/web && pnpm e2e` to:

- `studio/web/test-results/stretch-trapezoid/diff.png` (mad 1.75, 1.99 % pixels > 24)
- `studio/web/test-results/fit-wide/diff.png` (mad 1.02, 0.96 %)
- `studio/web/test-results/canvas/diff.png` (mad 1.01, 1.27 %)

(`preview.png` and `backend.png` sit next to each diff.) The diffs are thin lines along edges *inside* the image content (checker square borders, letterbox border, split line), not along surface edges.
Cause: the WebGL preview places image edges exactly (canvas scene: edges at x = 105, 240, 375), while `facadekit/render/compositor.py` (`cv2.warpPerspective`, lines ~130) places them about 0.5-1 px to the left/up (edge near 104.5, 238.7, 373.7). That is a half-pixel sampling-convention mismatch (pixel index vs pixel centre) in the backend homography/warp; fix it in the backend compositor (e.g. conjugate the homography with a 0.5 px translation) rather than relaxing thresholds.
