# Blockers

None open.

## Resolved

### T-037: preview/render parity (resolved)

Cause was two backend issues that masked each other (so the earlier half-pixel experiment alone looked useless):

1. `GET /frame` encoded JPEG with OpenCV's default 4:2:0 chroma subsampling, which smears colour across 2x2 blocks at hard edges (e.g. black next to red at x = 104|105 gave R = 62 in a black pixel). Now 4:4:4.
2. `render/compositor.py` used `cv2.warpPerspective` with pixel-index coordinates (integer = pixel centre) while the homography and WebGL use pixel-corner coordinates, and an alpha rect warped bilinearly against a constant black border (AA ramp at letterbox edges, darkened image border). Now the homography is conjugated by a half-pixel shift, colour uses BORDER_REPLICATE (CLAMP_TO_EDGE), and rect coverage is a hard nearest-sampled mask (matches the shader's discard).

All seven scenes pass with thresholds unchanged.
