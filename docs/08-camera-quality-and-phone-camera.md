# 08 – Camera crop, camera quality verdict, and the phone as camera

Added 2026-11-11. Status: **decided, not built**. Tickets T-201 to T-208 (index at the end).

The scenario: the webcam is far from the house, so the projection is small in the camera picture. Two questions: should the user be able to crop to the lit area, and can FacadeKit tell that the camera is too coarse for the job and suggest a phone instead?

Short answer: **yes to both.** Crop is applied at capture time by a camera wrapper, and results are stored in full-frame coordinates, so nothing downstream changes. Quality is judged by one number derived from the Gray code: the finest stripe the camera can read. A phone is used by **recording a video with its own camera app and uploading it**. Phone frames are not streamed through a browser. The recording reuses the self-clocked pattern sequence that the Pi-driven scan (T-113) already analyses.

Nothing here changes `docs/05-interfaces.md`. The contract additions are listed in §7 and flagged ⚑ in the tickets; the owner applies them to 05 when the tickets are accepted.

---

## 1. What the code does today (facts this design builds on)

| Fact | Where | Consequence for this scenario |
|---|---|---|
| `precheck` fails `CAMERA_NO_PROJECTION` when the lit area is below 5 % of the frame | `calib/capture.py`, and again in `pipeline.run_calibration_from_capture` for recordings | A far camera is refused outright before any quality advice can be given. A 1280×720 webcam whose projection is 20 % of the frame width and 20 % of its height has 4 % lit, so it fails today. |
| `OpenCvCamera` requests 1280×720 | `api/routes_devices.py` builds `OpenCvCamera(int(id))` with the default size | Many webcams that can do 1080p are used at 720p. |
| Exposure search uses the 99th percentile of the whole frame; the lit threshold uses the 95th percentile of the whole frame | `calib/tuning.py`, `capture.precheck`, `decode.decode` | A porch light or street lamp elsewhere in the frame steers the exposure and the thresholds. |
| `CaptureSet.images` is float32 `(N, Hc, Wc)` | `calib/capture.py` | 38 frames at 1080p = 315 MB, at 4K = 1.26 GB. Without cropping, a high-resolution camera is too heavy. |
| Camera pixel `(u, v)` is at `(u + 0.5, v + 0.5)` (pixel-corner coordinates); `p2c` stores pixel-corner camera coordinates; `make_proj_view` subtracts 0.5 for `cv2.remap` | `calib/maps.py`, BLOCKERS T-037 note | Integer crop offsets and integer bin factors map back exactly. No half-pixel terms appear. |
| The Pi scan already cuts a continuous recording into pattern images from a flash marker (`segment_recording`) | `calib/selfclock.py` | A phone video can be analysed the same way. The only change is that the marker search must scan the whole recording, because the user starts recording at an arbitrary time. |
| Scan memory context is `(camera_id, lighting bucket)`; with an empty log the "standard" strategy equals the fixed defaults | `calib/learn.py`, docs/07 | Any new context dimension must leave old records and the empty log unchanged. |
| A paired LAN phone (T-117) may only call a short allowlist; uploads are excluded on purpose | `api/remote_auth.py` `_REMOTE_ROUTES` | A phone upload needs one deliberate, flagged addition to that list. |

---

## 2. A. Crop (region of interest)

**Decision.** A crop is an integer rectangle in full-frame camera pixels. It is applied **at capture time** by a `CroppedCamera` wrapper that implements the `Camera` protocol. Every stored output (`p2c.npy`, `h_c2p`, `camera_width/height`) stays in **full-frame** camera coordinates, and the crop is recorded in `CalibSettings.roi`.

### Where it applies

| Stage | With crop |
|---|---|
| Live view (wizard) | The full frame is shown with the crop box drawn over it. An optional cropped snapshot (`?crop=`) shows a zoomed view. |
| Latency tuning, exposure search, precheck, capture | They receive `CroppedCamera(camera, roi)`. `read()` returns `frame[y:y+h, x:x+w]` and `resolution` returns `(w, h)`. These stages need no code changes: they already only use the protocol. |
| Decode, `build_maps`, `make_proj_view` | Unchanged. They work in crop coordinates on the cropped images and the cropped white photo. |
| Storing | Right after `make_proj_view`, convert to full frame (below), then write. |
| Projector view, surfaces, render, re-align | Unchanged. They live in projector space. |

### Coordinates (exact)

With crop `(x, y, w, h)` and bin factor `b` (1 for webcams; 2 is allowed for large phone videos, see §4), a crop-image pixel `i` covers full-frame pixels `[x + b·i, x + b·i + b)`. In pixel-corner coordinates the map from crop to full frame is the affine

```
A = [[b, 0, x],
     [0, b, y],
     [0, 0, 1]]
p2c_full  = b · p2c_crop + (x, y)          # element-wise on the (H, W, 2) array
h_c2p_full = h_c2p_crop @ inv(A)            # full-frame camera -> crop camera -> projector
```

Check: the centre of crop pixel `i` is `i + 0.5`, and `b·(i + 0.5) + x` is the centre of the `b`-pixel block it averages. For `b = 1` this is `i + x + 0.5`, the full-frame pixel centre. Because `x`, `y` and `b` are integers, there is no half-pixel correction. That is the reason crop values must be integers: the backend rounds a box drawn in the UI **outwards** (`floor` the left/top edges, `ceil` the right/bottom edges) to even numbers, and clamps it to the frame. In float32, `p2c_full` is exact to about 2.5e-4 px at 4K (24-bit mantissa), so tests use `atol = 1e-3`.

### Capture time, not decode time

- **Memory and speed:** a crop to a projection that is 25 % of the frame in each direction keeps 1/16 of the pixels. At 1080p, `images` shrinks from 315 MB to 20 MB. This is what makes 1080p webcams and 4K phone videos affordable.
- **Better statistics, same noise:** cropping does not change per-pixel sensor noise. What it changes is that exposure (p99), the lit threshold (p95) and the latency step detection (frame mean) are computed on the house, not on a street lamp or the sky.
- **Less code:** with a decode-time crop, decode, maps and capture would each need offset handling. With the wrapper, one function maps the result back.

### Rules

- `CameraRoi` stores `frame_width/frame_height`. If the opened camera resolution differs, the scan fails with `BAD_REQUEST`: "The crop was drawn for a different camera picture size. Check the camera again."
- Minimum size is 64×64 px. Width and height must be multiples of `b`.
- After the precheck in a cropped scan: if more than 2 % of the lit blob's pixels lie within 2 px of the crop border, add the warning `CROP_TOO_TIGHT` ⚑. This is a warning, not a failure.
- `CalibQuality.lit_fraction` keeps its meaning (fraction of the **full** frame): `lit_px / (frame_width · frame_height)`.
- Unmeasured projector areas that are filled from `h_c2p` may point outside the crop. There, `proj_view` is black instead of showing the uncropped photo. These areas are already tinted red as unmeasured, so this is acceptable.

### Auto-suggest

From the precheck contrast image (WHITE − BLACK), take the largest connected lit blob after a 3×3 morphological opening. Use its bounding box, add a margin of `max(16, 4 % of the box's longer side)` px on every side, round outwards to even numbers and clamp. Suggest the crop only if it is at most 60 % of the frame area; otherwise cropping gains little.

### Existing calibrations

Nothing changes. `settings.roi` is absent, which means the full frame, and files keep the same format and coordinates. No migration is needed. Quick re-align must copy `settings.roi` from the calibration it compares against, so the new scan uses the same crop (wizard ticket). One caveat: models use `extra="forbid"`, so an **older** FacadeKit cannot read a new `calibration.json` that has `roi`. Downgrades are not supported anyway.

---

## 3. B. Quality verdict

### The governing number: camera pixels per projector pixel

`s` = how many camera pixels one projector pixel spans (linear).

- Before the scan, it is estimated from the precheck: `s = sqrt(hull_area / (W · H))`, where `hull_area` is the convex-hull area in camera pixels of the largest lit blob. The convex hull ignores dark windows inside the projection. If part of the projection misses the house, `s` comes out too low, which errs on the cautious side.
- After the scan, it is measured from `h_c2p` at the projector centre: `s = 1 / sqrt(|det J|)`, where `J` is the Jacobian of `h_c2p` there. This value is stored in `CalibQuality`.

### Gray code arithmetic

The decoded cell is `B = 2^skip_lsb` projector px. The finest stripe actually projected is twice that: Gray bit `b` forms runs of `2^(b+1)` px (checked: bit 2 gives runs of 8, bit 3 runs of 16). So the camera must resolve stripes `2^(skip_lsb+1)` projector px wide, which is `2^(skip_lsb+1) · s` camera px.

**Rule: the finest stripe must span at least 3 camera pixels.** That is 2 pixels for sampling one stripe of the two-stripe period, plus 1 pixel of slack for lens blur (σ ≈ 1 px), Bayer demosaicing and compression. Calibration point: the default simulator scene has `s ≈ 0.48`, so its 8 px stripes span 3.8 camera px, and it decodes with a 1.40 px corner error.

```
finest_readable_skip(s) = ceil(log2(3 / s)) - 1        (clamped to 0..6; compute with a 1e-9 tolerance)
skip_lsb 2 (8 px stripes)  needs s >= 3/8  = 0.375
skip_lsb 3 (16 px stripes) needs s >= 3/16 = 0.1875
skip_lsb 4 (32 px stripes) needs s >= 3/32 = 0.094
```

| Setup (1920×1080 projector) | Camera frame | Projection width in frame | `s` | Finest readable `skip_lsb` | Verdict |
|---|---|---|---|---|---|
| Default sim scene | 1280×720 | ≈ 920 px | 0.48 | 2 | good |
| Webcam far away, at 720p | 1280×720 | 320 px (25 %) | 0.167 | 4 | too coarse |
| Same spot, same webcam at 1080p | 1920×1080 | 480 px | 0.25 | 3 | marginal |
| Phone 4K, no zoom, same spot | 3840×2160 | 960 px | 0.50 | 2 | good |
| Phone 4K, 2× zoom, same spot | 3840×2160 | 1920 px | 1.00 | 1 (scan still uses 2) | good |

(Width ÷ 1920 approximates `s` for a front-on view; the code uses the area formula.)

### Sharpness: stripe modulation, not Laplacian variance

The variance of the Laplacian on the white frame measures the **texture of the wall** (brick vs. smooth render) as much as the focus. It cannot be compared between houses, so it is rejected.

Instead, the camera check projects the actual finest stripes and measures how much of them survives:

```
M_b = median over lit pixels of |I(bit b, x axis) - I(bit b inverse)| / max(WHITE - BLACK, 1)
```

This is the decode confidence itself, with 1.0 meaning a perfectly sharp camera. The probe uses bits 2, 3 and 4 of the x axis (6 frames, about 4 s). `skip_blur` is the smallest probed bit with `M_b ≥ MOD_OK`, or 5 if none qualifies. `MOD_OK = 0.25` is the initial value. Decode accepts single pixels at 0.10, and a median must sit well above that. T-202 tunes it on the 18 `learn_eval` scenes (§6).

### Other signals

| Signal | How | Used for |
|---|---|---|
| Noise | `σ = std(F1 − F2) / √2` over lit pixels, from two single (not averaged) WHITE frames | `snr = median_contrast / σ`. `snr < 8` adds `NOISY` and makes the verdict at least `marginal`. |
| Contrast | median WHITE − BLACK over lit pixels (existing) | Lighting bucket for scan memory (unchanged). |
| Clipping | p99 of WHITE ≥ 250 before the exposure search | `CLIPPED`, information only (the scan's exposure search handles it). |
| Exposure control | `set_controls` result | `NO_EXPOSURE_CONTROL`, information only. |

### Verdict

```
skip_scale = finest_readable_skip(s)
skip_blur  = from the probe (camera check only; None inside a normal scan's precheck)
need       = max(skip_scale, skip_blur or 0)
verdict    = "good" if need <= 2 else "marginal" if need == 3 else "too_coarse"
reasons    = SMALL_IN_FRAME if skip_scale >= 3; BLURRY if skip_blur >= 3; NOISY if snr < 8;
             CLIPPED, NO_EXPOSURE_CONTROL as information
```

The "too coarse" line is at `need ≥ 4` for these reasons:

- `very_soft` (skip 4) can still produce a map, but each decoded cell is 16 projector px.
- One camera pixel covers more than 5 projector pixels, so the projector-view photo the user draws surfaces on is magnified more than 5× and visibly blurry.
- That is the point where getting the phone out is worth the trouble.

### Precheck gate change

`CAMERA_NO_PROJECTION` now fires only when the largest lit blob is below `MIN_LIT_PX = 4000` camera pixels, or holds less than 50 % of all lit pixels (scattered flicker, not a projection). The 5 % rule is removed, in both the laptop precheck and the recording path. A small projection becomes a scan with an honest verdict instead of a refusal. A blob of 4000 px leaves headroom above `MIN_VALID_PIXELS = 1000` in `build_maps`.

### Scan memory (docs/07)

- **Logged:** `ScanRecord` gains `scale: "fine" | "coarse" | None` ("fine" when `finest_readable_skip(s) ≤ 2`). It is computed from the precheck `s`, which is available before the strategy is chosen, and for recordings from the capture.
- **Weighting:** a record from the other scale class counts ×0.5 (`DIFFERENT_SCALE`), multiplied with the existing camera/lighting similarity. Records with `scale = None` (all existing logs) and calls without a scale count ×1.
- **Empty log:** an empty log still gives "standard", so the empty-log rule holds.
- **Why:** when a user moves the camera closer after a "too coarse" verdict, the earlier lessons ("this camera needs wide stripes") lose half their weight instead of steering the next scan.
- **Rejected:** letting a non-good verdict pre-select a coarser strategy. That would change empty-log behaviour. The verdict tells the **user** to fix the setup; the bandit's existing failure sharing handles the rest after at most one failed scan.

---

## 4. C. Phone as camera

### Options

| Option | Quality and zoom | Cost and UX | Verdict |
|---|---|---|---|
| **Record a video with the phone's own camera app, upload it; the backend syncs with the flash marker** | Best: native sensor, optical lenses, AE/AF lock, 4K | No secure context needed (file upload works over plain `http://192.168.x.x`), no installs, no new dependencies, iOS and Android. User does about 4 steps. | **MVP** |
| Phone browser page streaming frames (`getUserMedia`) | Worse: no exposure lock on iOS Safari, lens and zoom choice not dependable, compressed frames | Needs HTTPS on the LAN: a self-signed certificate shows full-page warnings on phones and needs a certificate library (new dependency). The phone must stay awake and in the foreground for a minute. | Rejected for now. Roadmap v2 item 8 ("phone as camera via the browser") is **replaced** by this decision. |
| Phone-as-webcam apps (macOS Continuity Camera, DroidCam, Iriun, Camo) | Good sensor; zoom and exposure lock usually unavailable through the virtual camera | Already works today with no code: they appear in `list_cameras`. Third-party installs on Windows and Linux. | Mention in user docs as a free alternative. No ticket. |
| RTSP/IP-camera apps opened by OpenCV from a URL | Medium | User-typed URLs raise server-side request concerns and need validation; mostly Android only | Defer. |
| Upload a photo set | Best stills | 46 photos taken in sync by hand | Rejected. |

### MVP design: record and upload

**Playback.** The laptop's projector window plays the self-clocked sequence. It uses the same frames and holds as `selfclock.py`:

- `LEAD_IN` (2 s black, then three white/black flashes);
- every pattern held `HOLD_FRAMES / VIDEO_FPS = 1.0 s`;
- 2 s of black at the end;
- before all of that, a countdown of black (default 15 s, range 5–60 s) while the laptop shows the remaining seconds in large type.

Frames are shown on **absolute deadlines** `t0 + k · 1.0 s`, not with `sleep(1.0)` after each `show()`. That way, latency from the projector acknowledgement shifts the whole sequence but does not accumulate. `segment_recording` assumes equal segment lengths, and its central-50 % windows tolerate ±250 ms. Length for 1080p at skip 2: 15 + 8 + 38 + 2 = 63 s.

**Session.** "Start light show" creates a phone-scan session: `<project>/calibration/.phone/<sid>.json` holding `{skip_lsb, strategy, created_at}`. The upload names the session, so the analysis knows which sequence was played even if the server restarted in between. The strategy comes from `advisor.remembered_strategy("phone") or STRATEGIES[0]`, as for Pi scans. `camera_id` is the constant `"phone"`.

**Sync.** No clock sharing is needed: the flashes are the clock.

- Pass 1 decodes the video at 30 fps (ffmpeg `fps=30` filter, so phone variable frame rate becomes a uniform timeline) and 160 px wide, then runs `segment_recording(times, small, n, marker_search_s=None)`. With `None`, thresholds come from the whole recording, because recording may start long before the flashes.
- Pass 2a decodes the BLACK and WHITE windows at full resolution, builds the lit blob, and derives the auto-crop (margin rule from §2). Bin `b = 2` is used when the crop is over 6 MP.
- Pass 2b decodes the whole video once at full resolution through an ffmpeg `crop` (and `scale` for `b = 2`) filter as 8-bit gray. Each frame inside a window is added to that pattern's float32 sum, then sums are divided by counts.
- The WHITE colour photo is the median frame of the WHITE window, decoded as BGR with the same crop.

**Mapping of scan settings to video.**

| Laptop setting | Video equivalent |
|---|---|
| `settle_ms` | the 25 % skipped at the start of each 1 s hold (250 ms) |
| `discard_frames` | the same skipped frames |
| `average_frames` | all frames in the central 50 % (about 15 at 30 fps) |

`skip_lsb` and `min_conf` come from the strategy. `settle_ms`, `discard_frames` and `average_frames` are stored at their defaults and ignored.

**Motion check.** `max(P_last, I_last)` (the last stripe pair and its inverse) is as bright as WHITE wherever the projector reaches. Compare `WHITE − BLACK` (start of the sequence) with `max(P_last, I_last) − BLACK` (end) using `cv2.phaseCorrelate` with a Hanning window on the crop. A shift above 2.0 full-frame px fails with `PHONE_MOVED` ⚑. Handheld jitter within holds is not detected separately: it blurs the averages, coverage drops, and the `SCAN_FAILED` message for phone scans adds "Did the phone move?".

**Failure modes.**

| Failure | Detected by | Message |
|---|---|---|
| Recording missed part of the sequence | `segment_recording` cannot find the marker or windows run past the end | `PHONE_VIDEO_NO_PATTERN` ⚑: "The video does not contain the whole light show. Start recording before the projector flashes and stop after it goes dark." |
| Unreadable file (codec, HDR, corrupt) | `probe` or ffmpeg error, or duration below the sequence length | `PHONE_VIDEO_UNREADABLE` ⚑: "FacadeKit cannot read this video. Record at 1080p or 4K in the 'Most Compatible' format and try again." |
| Phone moved | motion check | `PHONE_MOVED` |
| Projection too small even for the phone | verdict from the capture | The same result-screen text as for webcams. |
| Auto-exposure pumping | Mostly harmless: each stripe pair and its inverse have the same mean brightness. BLACK/WHITE contrast shrinks. | Instructions: lock AE/AF. |
| Phone stabilisation shifts the frame | Motion check or low coverage | Instructions: turn off Action mode / enhanced stabilisation. Spike S-9. |
| Portrait video | `probe` reports rotation; ffmpeg auto-rotates on decode | The builder must use post-rotation dimensions (T-207 pitfall). |

Errors before decoding (marker, unreadable, moved) are not logged to scan memory, matching the Pi path. Only `SCAN_FAILED` from decode or maps counts against the strategy.

**Transport.** There are two ways to get the file onto the laptop. Both use the same route.

1. **Laptop upload (primary, works without LAN mode).** The user moves the file with AirDrop, a cable or a memory card and picks it on the laptop's wizard. This needs no pairing.
2. **Phone page over the existing LAN pairing (T-117).** `facadekit serve --lan`, QR code, 6-digit code, cookie. The phone page `/phone/:projectId` has **Start light show** (with countdown, so the user can start it, switch to the camera app and press record) and **Send video** (`<input type="file" accept="video/*">`, with no `capture` attribute so the native camera app's recording can be picked; `XMLHttpRequest` for upload progress). This requires adding three routes to the remote allowlist ⚑. T-117 excluded uploads on purpose; this is the single, deliberate exception, and it needs owner approval.

**Privacy.**

- The video goes only from the phone to the laptop over the home network.
- No cloud, no accounts, no third-party app.
- The uploaded file is stored in the project folder only during analysis and **deleted afterwards** (on success or failure) unless `FACADEKIT_KEEP_PHONE_VIDEO=1` (debugging).
- Like webcam scans, the calibration keeps only the projector-view photo and maps.

**Upload limits.** The route rejects files over 4 GB (`Content-Length` checked first) and files that would leave less than 1 GB of free disk. Typical size: 63 s of 4K HEVC ≈ 400 MB, which takes 1–2 minutes on home Wi-Fi.

---

## 5. D. Wizard guidance (plain language)

**Camera step.** After the camera is chosen, a primary button **Check the camera view** runs the camera check (about 6 s: black, white, stripes). A result card then shows:

| Result | Text | Buttons |
|---|---|---|
| good | "Your camera sees the projection clearly." With a suggested crop, add: "Tip: crop to the lit area to make the scan faster." | **Continue** · Crop to the lit area |
| marginal, small | "Your camera sees the projection quite small. The scan should work, but lines may land a few pixels off. For a sharper scan, move the camera closer, zoom in, or use your phone." | **Crop to the lit area** · Scan with this camera · Use my phone instead |
| too coarse, small | "Your camera sees the projection very small. Crop to the lit area, or use your phone for a sharper scan." Details line: "Each camera pixel covers about 6 projector pixels. Cropping makes the scan faster, but only a closer camera, zoom or your phone adds detail." | **Use my phone instead** · Crop to the lit area · Scan anyway |
| blurry | "The picture is soft. Turn the focus ring (or tap the house on your phone) until the brick lines look crisp, then check again." | **Check again** · Scan anyway |
| noisy (adds) | "The picture is grainy. Scanning later in the evening or switching off porch lights will help." | — |
| clipped (adds) | "Parts of the picture are very bright. FacadeKit will turn the camera down during the scan." | — |

**Cropping.** The live view shows an amber box set to the suggestion, with four corner handles and draggable edges. Caption: "Only the inside of the box is used. Keep the whole lit area inside the box." Buttons: **Use this crop** · Reset · Use the whole picture. The crop goes with the scan request. The next scan with the same camera and picture size pre-fills it from the latest calibration's `settings.roi`.

**Scan result screen.** One added line when the scan's verdict is not good: "Your camera saw the projection small, so check the outlines carefully on the house."

**Phone panel** (from **Use my phone instead**, or "Scan with your phone" under the scan button):

1. "Put your phone on a tripod or lean it on something steady, near the projector. Zoom in until the house fills the screen."
2. "If your phone has Action mode or extra stabilisation, turn it off. Press and hold on the house to lock focus and brightness."
3. "Start recording a video, then press **Start light show** (here or on your phone). The projector stays dark for 15 seconds, flashes three times, then shows stripes for about a minute."
4. "Stop recording when the projector goes dark. Send the video to this computer, or use the phone page, and choose it here: **Choose video…**"

Privacy note under the panel: "The video goes straight from your phone to this computer. It is deleted after the scan."

**Never say:** ROI, region of interest, Gray code, LSB, `skip_lsb`, bit plane, homography, Laplacian, SNR, modulation, verdict, bucket, strategy, bandit, decode, confidence, p2c, "low-quality webcam" or any brand judgement. Describe what the picture lacks, not what the hardware is. Pixel counts are fine: hobbyists know pixels. Never promise an accuracy in pixels.

---

## 6. E. Testing without hardware

**Simulator (T-201).**

- `far_scene(k, *, cam_size, centre, world_scale)` places the default layout scaled by `k · cam_w / 1280` about `centre`, so the projection is small and off-centre. Precisely: `corner' = (centre_x · cam_w, centre_y · cam_h) + k · (cam_w / 1280) · (corner − mean of the default world_corners_cam)`.
- `scale_world(scene, f)` multiplies the world plane, so that a high-resolution camera is not limited by the 1600×900 world texture (1 world px ≈ 1.44 projector px today).
- `true_px_per_proj_px(scene)` gives the ground-truth `s` from `h_p2c`.
- The default scene and `default_scene()` stay byte-identical.

| Scene | Definition | True `s` | Expected |
|---|---|---|---|
| `SCENE_DEFAULT` | `default_scene()` | 0.40–0.55 | good; e2e corner error < 1.5 px with and without crop |
| `SCENE_FAR_MARGINAL` | `far_scene(0.58)` at 1280×720 | 0.22–0.34 | verdict `marginal` + `SMALL_IN_FRAME`; scan with `soft_focus` (skip 3) and suggested crop: coverage ≥ 0.85, map corner error < 3.0 px |
| `SCENE_FAR_TOO_COARSE` | `far_scene(0.28)` at 1280×720 | 0.11–0.16 | lit fraction < 5 % (the old gate refused it); passes the new gate; verdict `too_coarse` |
| `SCENE_PHONE` | `far_scene(0.7, cam_size=(2560, 1440), world_scale=2)` | ≥ 0.55 | verdict `good`; scan with suggested crop: corner error < 1.5 px |
| `SCENE_BLURRY` | default with `blur_sigma=4` | ≈ 0.48 | verdict ≥ `marginal` + `BLURRY` (scale fine) |

**Acceptance numbers.**

- **Crop equivalence on the default scene:** the scan with the suggested crop and the scan without one differ by at most 0.25 px in median `p2c` over measured pixels, both e2e corner errors stay < 1.5 px, and `h_c2p_full` agrees within 0.1 px at the four door corners.
- **Exactness:** `CroppedCamera` images equal the full capture sliced (`np.array_equal`). The sim's random-number sequence does not depend on the crop.
- **Blur rule (T-202):** on the 18 `learn_eval` scenes, `skip_blur ≤ 2` predicts "standard succeeds" on at least 15 of 18. If not, tune `MOD_OK` within 0.15–0.40 and record the table in this doc.
- **Tuning result (T-202, 2026-11-11):** on the 18 `make_scenes(18)` scenes, "standard succeeds" (`scan_once`, record ok) against `modulation[2] >= MOD_OK` (the probe's bit-2 stripe modulation; `skip_blur <= 2` is equivalent):

  | `MOD_OK` | 0.15 | 0.20 | 0.25 | 0.30 | 0.35 | 0.40 |
  |---|---|---|---|---|---|---|
  | agreement | 17/18 | 18/18 | 18/18 | 18/18 | 18/18 | 18/18 |

  Bit-2 modulation of the scans that failed with "standard" is at most 0.181 (blur 3.5 to 5.0); of the ones that succeeded at least 0.415 (blur 0.5 to 1.5). Every value from 0.20 to 0.40 separates them, so `MOD_OK` is set to **0.30**, the middle of that gap (margin 0.12 below, 0.11 above), instead of the initial 0.25. Simulator only; spike S-9 re-checks it on real cameras.
- **Scan memory:** `learn_eval` regret does not regress (tuning ≤ 0.490, held-out ≤ 1.143).
- **Phone video (T-207):** `make_phone_video(scene, …)` renders the playback sequence through the simulator into an H.264 file with a random 3–25 s pre-roll and a 3 s tail. Analysis gives map corner error < 1.5 px on a 1920×1080 phone-like scene. A 4 px shift halfway through gives `PHONE_MOVED`. A cut-short file gives `PHONE_VIDEO_NO_PATTERN`. A 90°-rotated file gives the same result as the unrotated one.

---

## 7. Contract additions needed in docs/05 (⚑, not applied)

1. §4 `calibration.json`:
   - `CameraRoi {x, y, w, h: int, frame_width, frame_height: int, bin: Literal[1, 2] = 1}`;
   - `CalibSettings.roi: CameraRoi | None = None`;
   - `CalibQuality.px_per_proj_px: float | None = None`, `snr: float | None = None`, `verdict: Literal["good", "marginal", "too_coarse"] | None = None`.
2. §6.4 `devices/base.py`: `CroppedCamera(camera, roi)` implements `Camera`. `CameraManager` opens OpenCV cameras at `FACADEKIT_CAMERA_SIZE`. The default stays `1280x720`; 1080p is opt-in with `FACADEKIT_CAMERA_SIZE=1920x1080` (owner decision, so existing setups behave as before). The wizard only hints at it (`CameraCheck.suggest_1080p`); there is no settings UI for it.
3. §6.6 `calib/*`:
   - new `calib/quality.py` (§3 functions);
   - `precheck` returns extra keys `lit_px`, `px_per_proj_px`, `snr`, `p99_white`, `lit_x0`, `lit_y0`, `lit_x1`, `lit_y1`;
   - `CAMERA_NO_PROJECTION` uses the blob rule;
   - `selfclock.segment_recording(..., marker_search_s: float | None = 15.0)`;
   - new `calib/phone.py`.
4. §8 HTTP:
   - `POST /projects/{pid}/camera-check` `{camera_id, roi?}` → `{job_id}`; job result `{"check": CameraCheck}`;
   - `GET /devices/cameras/{cid}/snapshot.jpg?crop=x,y,w,h`;
   - `POST /projects/{pid}/phone-scans` `{countdown_s?, learn?}` → `{session_id, job_id}`;
   - `POST /projects/{pid}/phone-scans/{sid}/video` (multipart `file`) → `{job_id}`; job result `{"calibration_id"}`.
5. §10 codes: errors `PHONE_VIDEO_UNREADABLE`, `PHONE_VIDEO_NO_PATTERN`, `PHONE_MOVED`; warning `CROP_TOO_TIGHT`.
6. T-117 remote allowlist: add `POST /api/v1/projects/{pid}/phone-scans`, `POST /api/v1/projects/{pid}/phone-scans/{sid}/video`, `GET /api/v1/jobs/{jid}`.
7. docs/07: `ScanRecord.scale`, `DIFFERENT_SCALE = 0.5`, `choose(..., scale=None)`.

---

## 8. F. Risks, spikes, deferrals

| Risk | Mitigation |
|---|---|
| The 3-camera-pixel stripe rule and `MOD_OK` are simulator-derived | Spike **S-9** (owner): a far-webcam field scan at three distances, comparing the verdict with scan success; adjust two constants in `calib/quality.py`. |
| Webcams at 1080p may drop to 5 fps (uncompressed YUYV over USB 2) | Latency tuning already measures the frame interval. The default is 720p, so nothing changes until the owner sets `FACADEKIT_CAMERA_SIZE=1920x1080`; the wizard hint tells them how. Extend spike S-3 with "does 1080p open, at what fps". |
| Phone recordings: does AE/AF lock hold for 60 s; HEVC/HDR (iPhone HLG, Dolby Vision) through the bundled imageio-ffmpeg; stabilisation drift; rotation metadata; whether iOS Safari transcodes videos picked in a file input | Spike **S-10** (owner): one iPhone and one Android, 4 recordings each, run through the T-207 analysis CLI. Fallbacks: "Most Compatible" format; AirDrop/cable then laptop upload. |
| The marker search over a long recording finds a false flash train (for example the wizard's white/grid toggles) | Segments must be 0.6–1.4 s long, and the projector is black during the countdown. Recording that starts during the wizard's white toggling is harmless because those toggles are not 1 s periodic. Tested with 25 s pre-roll. |
| Upload of large files over Wi-Fi fails midway | Clear error; retry. Laptop upload as fallback. No resumable upload in the MVP. |

**Defer:**

- phone + Pi playback (record the FPP pattern video with the phone; works in principle via the same analysis, add after S-10);
- browser streaming over HTTPS;
- RTSP/URL cameras;
- crop for Pi-driven webcam scans (`record_and_capture` can take a `CroppedCamera` later);
- using the verdict to pre-select a strategy;
- a resumable upload;
- motion detection within each hold.

---

## 9. Ticket index

MVP of this feature: T-201 to T-206. Phone camera: T-207, T-208.

| Order | Ticket | Title | Model | Depends on |
|---|---|---|---|---|
| 1 | T-201 | Simulator scenes: far camera, phone-like camera | H | — |
| 2 | T-202 | Camera quality measurements and verdict | S | 201 |
| 3 | T-203 | Camera crop in the scan pipeline | S | 202 |
| 4 | T-204 | Camera check API, cropped snapshot, 1080p capture | S | 202, 203 |
| 4 | T-205 | Scan memory: picture scale as context | H | 202 |
| 5 | T-206 | Wizard: camera check, crop box, plain-language guidance | S | 204 |
| 6 | T-207 | Phone video analysis and light-show playback | S⚑ | 113, 202, 203 |
| 7 | T-208 | Phone scan flow: routes, wizard panel, phone page | S | 117, 206, 207 |

Conflict notes:

- T-202, T-203 and T-205 all touch `calib/pipeline.py` or `calib/learn.py`, which had uncommitted edits on branch `scan-memory-tuning` when this was written. Builders rebase on whatever is committed and run them one at a time.
- T-206 and T-208 both edit `StepScan.tsx`: do them in order.
