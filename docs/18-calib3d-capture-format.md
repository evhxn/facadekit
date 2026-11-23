# 18 – calib3d capture format (version 1) and offline pipeline

Added 2026-11-23 for spike S-11 Part A (`docs/15-lidar-spike.md`). Code:
`studio/backend/src/facadekit/calib3d/` (`capture_format.py`, `fusion.py`, `planes.py`,
`projector.py`, `refine.py`, `solve.py`), CLI `facadekit calib3d solve`, simulator
`sim/scene3d.py`, evaluation `sim/calib3d_eval.py` and `sim/calib3d_report.py`.

**Status: offline only, tested on simulated data only.** No phone, depth sensor or real capture
has been through it. Every depth-sensor property used in the simulator is an assumption.

## 1. What a capture is

One folder: `manifest.json` plus the files it names (paths relative to the folder, `/`
separated, no `..`, no absolute paths). A capture is one session: the camera that photographed
the Gray-code patterns did not move during the patterns, and every shot's pose is in the same
world frame.

```
capture/
  manifest.json
  shots/s000.png            colour image of each shot
  shots/s000_depth.npy      optional depth map (float32 metres)
  shots/s000_confidence.png optional per-pixel confidence (uint8, 0/1/2)
  graycode/black.png …      the Gray-code frames (or a FacadeKit calibration folder)
```

## 2. `manifest.json`

```json
{
  "format": "facadekit-calib3d-capture",
  "version": 1,
  "units": "metres",
  "pose_convention": "opencv",
  "created": "2026-11-23T10:00:00Z",
  "device": {"model": "…", "software": "…"},
  "shots": [
    {
      "id": "s000",
      "timestamp_s": 10.0,
      "image": "shots/s000.png",
      "intrinsics": {"fx": 980.1, "fy": 980.1, "cx": 639.5, "cy": 479.5, "width": 1280, "height": 960},
      "pose": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
      "depth": {
        "file": "shots/s000_depth.npy",
        "confidence": "shots/s000_confidence.png",
        "intrinsics": {"fx": 196.0, "fy": 196.0, "cx": 127.5, "cy": 95.5, "width": 256, "height": 192},
        "scale_m": 0.001,
        "timestamp_s": 10.0
      }
    }
  ],
  "graycode": {
    "shot": "s000",
    "kind": "frames",
    "pattern": {"width": 1920, "height": 1080, "skip_lsb": 2},
    "timestamp_s": 5.0,
    "frames": [
      {"kind": "black", "file": "graycode/black.png"},
      {"kind": "white", "file": "graycode/white.png"},
      {"kind": "pattern", "axis": "x", "bit": 10, "inverse": false, "file": "graycode/x10.png"}
    ]
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `format`, `version` | yes | Exactly `"facadekit-calib3d-capture"` and `1`. A higher version is refused with "newer than this FacadeKit reads". |
| `units` | no | `"metres"` (also accepted: `"meters"`). Nothing else. |
| `pose_convention` | yes | `"opencv"` (camera x right, y down, z forward) or `"arkit"` (x right, y up, z backward; ARKit's `camera.transform`). ARKit poses are converted on load: `T_opencv = T_arkit · diag(1, −1, −1, 1)`. |
| `created`, `device`, `notes` | no | Free text / object, kept for the report. Unknown top-level keys are ignored with a warning. |
| `shots[].id` | yes | `[A-Za-z0-9_.-]{1,64}`, unique. |
| `shots[].timestamp_s` | yes | Seconds, ≥ 0, the device's monotonic clock (ARKit `ARFrame.timestamp`). |
| `shots[].image` | yes | PNG or JPEG, colour. |
| `shots[].intrinsics` | yes | Pinhole of `image`: `fx, fy, cx, cy` in pixels, `width, height` of the image. **Pixel convention: OpenCV, integer coordinates are pixel centres.** (FacadeKit's scan maps use pixel corners; `calib3d.geometry` converts.) No lens distortion term in v1: images must be undistorted (ARKit's are, to within its own model). |
| `shots[].pose` | yes | 4x4 `world_from_camera`, row-major nested lists (or 16 numbers). Rotation part orthonormal with det +1 (no scale), last row `0 0 0 1`. Metres. |
| `shots[].depth.file` | if depth | `.npy`: 2-D float32/float64 metres, `0` or `NaN` = no data. `.png`: 16-bit single channel, value × `scale_m` metres (default 0.001). Depth is **z-depth along the optical axis**, not distance along the ray. |
| `shots[].depth.confidence` | no | `.png` or `.npy`, same size as the depth map, integers 0 (low), 1 (medium), 2 (high) as ARKit's `ARConfidenceLevel`. |
| `shots[].depth.intrinsics` | no | Of the depth map. Default: the image intrinsics scaled to the depth map's size (same field of view, as ARKit's `sceneDepth` is to `capturedImage`). |
| `shots[].depth.timestamp_s` | no | If the depth was not sampled with the image. |
| `graycode.shot` | yes | The shot whose intrinsics and pose are the Gray-code camera's (its image is usually the white frame). |
| `graycode.kind` | yes | `"frames"` or `"calibration"`. |
| `graycode.pattern` | frames | `width, height` of the projector, `skip_lsb` as `calib.patterns.PatternSpec`. |
| `graycode.frames[]` | frames | Every frame of `pattern_sequence(pattern)` exactly once: `kind` `black`/`white`/`pattern`, `axis` `x`/`y` and integer `bit` and `inverse` for patterns, `file` PNG/JPEG (8- or 16-bit, grey or colour; the white frame's colour is kept as the photo). Same size as the Gray-code shot's image. |
| `graycode.path` | calibration | A FacadeKit calibration folder inside the capture (`p2c.npy`, optional `measured_mask.png`), made by the same camera at the Gray-code shot's pose. |
| `ground_truth` | no | Written only by the simulator (`"source": "simulator"`); never used to solve. Any other `ground_truth` is refused. |

At least one shot must have depth. `load_capture` checks all of the above before any array is
used and reports every problem at once (`CaptureFormatError`, code `BAD_REQUEST`, one line per
problem, for example `shots[s001].pose: top-left 3x3 must be a rotation (orthonormal, det +1);
no scale`). Array shapes, dtypes and value ranges are checked when the arrays are loaded.
`CaptureWriter` writes the format (used by the simulator and the round-trip tests).

## 3. Processing (`facadekit calib3d solve`)

```
facadekit calib3d solve CAPTURE_DIR [--json] [--report DIR] [--min-confidence 0|1|2] [--max-range M]
```

1. **Decode** the Gray-code frames with the existing `calib.decode` and pool the decoded camera
   pixels by projector block (camera centroid ↔ block centre), as `calib.maps` does.
2. **Fuse depth** (`fusion.py`): drop pixels below the confidence floor (default 2, high),
   beyond the range limit (default 5 m) and flying pixels at depth edges; back-project every
   depth map with its pose; splat into the Gray-code camera on 8 px cells, keeping the nearest
   surface per cell; edge-aware smoothing.
3. **Planes vs non-planar** (`fusion._segment_grid`): local planes on 5x5 cells give normals
   and roughness; smooth neighbours with agreeing normals form surfaces; a surface is a plane
   when RANSAC puts at least 70 % of it within 2 cm of one plane. Planes are refitted on the raw
   points; plane pixels get the exact ray-plane depth.
4. **Projector self-calibration** (`projector.py`): 3D points from the fused depth at the decoded
   pixels ↔ decoded projector pixels; RANSAC over normalised DLT, RQ decomposition, then
   Levenberg-Marquardt over fx, fy, cx, cy and the pose (our own; `cv2.calibrateCamera` refuses a
   principal point outside the image, which lens-shifted projectors have). Reported: status,
   intrinsics, pose, 1-sigma of fx, fy, cx, cy, reprojection error median and p95 in projector
   pixels, inlier fraction.
5. **Scan-corrected surface** (`refine.py`): with the projector solved, every decoded block is
   triangulated (far more precise than the depth sensor); planes are refitted on the triangulated
   points; the sensor depth gets a smooth per-surface correction; pixels the scan puts off their
   plane lose the plane label; near decoded blocks the interpolated triangulated depth is used.
6. **3D-aware mapping** (`solve.Mapping3d`, mode `hybrid`): inside each depth plane the scan is
   split into planar groups (sequential RANSAC homographies, 1.5 px), each pixel uses its
   group's homography; elsewhere the solved projector applied to the scan-corrected surface,
   plus the scan's residual smoothed inside the same surface; the global plane mapping where
   neither exists.
7. **Report**: a table (or `--json`), and with `--report DIR`: `report.json`, `report.md`,
   `overlay.png` (planes, curved parts, scan residual dots on the Gray-code photo) and
   `proj_view_3d.png` (the photo seen from the projector through the 3D-aware warp,
   `solve.projector_warp`). Without ground truth the comparison is against the scan itself:
   15 % of 96 px tiles are left out of the fit and every mapping is scored on them (this
   includes the scan's own error).

### Degenerate case: one plane

A plane fixes a homography (8 numbers); a projector has 10 (fx, fy, cx, cy, 3 rotation,
3 position). So when the lit 3D points lie on one plane, the solve reports status `planar` with
that explanation and the plane mapping is used (it is exact there). The test is noise-aware:
"on one plane" means within 2 cm or 3 robust sigmas of the points' scatter, whichever is
larger, and it is repeated on the inliers after outlier rejection. Depth noise larger than the
relief makes a surface indistinguishable from a plane: the solve then converges to a wrong
projector that still fits the plane, which the first simulator run showed (`docs/15` §7).

Other statuses: `ok`; `weak` (solved, but fx/fy 1-sigma above 2 % or cx/cy above 20 px: little
depth relief); `failed` (fewer than 60 % of pairs agree with one projector, or no DLT);
`too_few` (under 30 pairs). Only `ok` and `weak` are used; otherwise the mapping falls back to
the plane mapping and the per-plane scan homographies.

## 4. What the iPhone capture app (Part B) must record

Per shot, from one `ARSession` with `.sceneDepth` (and `.smoothedSceneDepth` if used, say which):

- `capturedImage` converted to PNG or JPEG (or the white Gray-code frame for the Gray-code shot);
- `camera.intrinsics` (3x3) and `camera.imageResolution` → `intrinsics` (check that ARKit's
  principal point uses pixel centres at integers; if it uses corners, subtract 0.5 — open);
- `camera.transform` → `pose` with `"pose_convention": "arkit"`;
- `frame.timestamp` → `timestamp_s`;
- `sceneDepth.depthMap` (Float32 metres) → `.npy` or 16-bit PNG millimetres, and
  `sceneDepth.confidenceMap` → confidence PNG;
- exposure and focus locked for the Gray-code frames; the Gray-code frames captured at one pose
  (phone on a tripod), the pose and intrinsics of that moment in the Gray-code shot.

Open questions: whether `capturedImage` is distortion-free to sub-pixel level; whether ARKit's
intrinsics change with focus (lock focus); how far ARKit's world frame drifts within a session;
whether `smoothedSceneDepth` is better or worse here; the depth sensor's real noise, range and
failure surfaces (the simulator's are guesses).
