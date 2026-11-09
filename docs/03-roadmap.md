# 03 – Scope and roadmap

Working name: **FacadeKit**. Architecture is in `docs/02-architecture.md`. Tickets are in `tasks/` and indexed in `tasks/INDEX.md`.

## 1. MVP ("v0")

**Promise:** a first-time user with a laptop, a USB webcam, a projector and a Pi running FPP 10 gets their own video playing on their garage door, perspective-correct, started from FPP, in under 30 minutes after install.

### In the MVP

| Area | What is included |
|---|---|
| Install | `pipx install facadekit` then `facadekit serve`, which opens the browser |
| Wizard | Choose native projector resolution; open the projector window and verify pixel size; pick the camera with live view |
| Calibration | Gray-code scan with pre-checks, decode, projector-view photo, quality report, the failure messages of architecture §4.10 |
| Manual mode | Skip the scan and work on a blank canvas |
| Surfaces | Quads only. Draw on the projector view, drag corners, arrow-key nudge, live outlines on the house. Editable aspect. Feather. |
| Media | Import video (anything ffmpeg reads), images, one audio file |
| Arrange | A simple clip list: per clip choose surface, asset, start, duration, loop, fit, opacity, fades. Bars are drawn on a read-only timeline strip. No drag editing yet. |
| Preview | WebGL preview in the UI and live on the house; exact backend frame when paused |
| Render | `pi3-1080p30`, `pi3-720p30`, `preview-540p` presets with progress and cancel |
| Publish | Probe FPP, upload, create playlist, start, show status. Also "publish alignment check" (a 20 s outline video). |
| Test tooling | Simulator (virtual house, projector, camera) and fake FPP server; CI runs everything without hardware |
| CLI | `serve`, `calibrate`, `render`, `publish`, `doctor`, `sim` |

### Explicitly not in the MVP

Polygon masks and holes, mesh warp, click-to-segment, beat detection, drag-and-drop timeline with waveform, generators/effects, quick re-align, Pi-driven calibration, installers, `.fseq` pairing UI, multi-projector, anything using a cloud API, any FPP plugin.

### MVP exit criteria

1. `make test` passes on Linux, macOS and Windows CI with no hardware.
2. End-to-end simulated run: `facadekit sim e2e` scans the virtual house, places a test video on the virtual garage door, renders, publishes to the fake FPP, and the re-projected result in the simulator matches the door corners within 3 projector pixels.
3. Owner field test: real projector, real webcam, real Pi 3 with FPP 10.4, one surface, plays from an FPP playlist.

## 2. v1 – "usable by a non-technical neighbour"

| Feature | Notes |
|---|---|
| Real timeline | Tracks, drag/trim/snap, waveform, zoom, undo/redo |
| Polygon masks with holes | Draw and edit; renderer support already specified |
| Click-to-segment | MobileSAM ONNX on CPU, GrabCut fallback, quad fitting |
| Beat/onset markers | librosa; snapping; "cut on every N beats" helper |
| Generators | Solid colour, colour wash, outline trace, strobe-on-beat. Gives a show without any media. |
| Mesh warp | Per-surface control grid edited by dragging |
| Quick re-align | Re-scan + registration homography applied to all surfaces |
| Pi-driven calibration | Self-clocked pattern video played by FPP; cable stays in the Pi |
| `.fseq` pairing | Publish dialog can create a `both` entry with an existing sequence on the FPP |
| Phone remote | The fine-alignment page works on a phone on the same Wi-Fi |
| Installers | One-file builds for Windows and macOS; auto model download with checksums |
| Auto latency and exposure | Measure projector→camera latency instead of fixed settle time |
| Docs | Illustrated quick-start, troubleshooting, 3 video tutorials |

Exit: five outside testers complete a two-surface show with music from the docs alone.

## 3. v2 – "deeper Pi integration and bigger shows"

Ordered by value. Not ticketed yet: each depends on v1 field data.

1. **FPP script plugin** (`fpp-plugin-FacadeKit`, GPL-2.0-or-later): a page in FPP's Content menu listing published shows, an "Alignment check" FPP command, privacy block declaring no data leaves the device. This is the owner's requested Pi integration.
2. **Pi-attached webcam** through FPP 10 video input sources, for a nightly drift check that warns the user.
3. **Audience-view correction** using the dense map with the camera at the viewing position.
4. **Two projectors** with overlap blending; playback by FPP MultiSync or one Pi with two HDMI outputs using FPP 10 video output regions.
5. **Auto-detect surfaces** ("find windows and doors").
6. **Auto-sequence draft** (rules; optional BYO-key LLM show plan).
7. **Generative content (BYO key)** conditioned on the projector-view photo.
8. **Phone as the camera** via the browser.
9. **Projector power control** (PJLink / RS-232), optional, likely through existing FPP commands or plugins rather than Studio.
10. **HEVC / 1080p60 presets** for Pi 4 and 5.

## 4. Stretch

3D props with true per-pixel mapping; local diffusion on user GPUs; a shared community library of free, licence-clean content packs; live interactive effects (doorbell or motion triggers through FPP commands); E1.31-driven effect parameters.

## 5. Technical risks, ranked

Score = likelihood (1–5) × impact (1–5).

| # | Risk | L | I | Score | De-risking spike | When |
|---|---|---|---|---|---|---|
| R1 | Laptop HDMI output and Pi HDMI output do not land on the same projector pixels (different mode, overscan, scaler), so a show calibrated from the laptop is shifted on the Pi | 3 | 5 | 15 | **S-8**: same projector, 1-px grid image from laptop browser and from FPP; photograph both; compare. If they differ, promote Pi-driven calibration from v1 to MVP. | Before MVP build starts |
| R2 | Pi 3 cannot play 1080p30 H.264 smoothly under FPP 10 | 3 | 4 | 12 | **S-2**: play a 10-minute 8 Mbit/s test file on a Pi 3 with FPP 10.4; read Troubleshooting → Video for HVS underruns; repeat at 720p. Result picks the default preset. | Before MVP build starts |
| R3 | Webcam exposure cannot be locked through OpenCV on some OS/camera pairs, making thresholds unstable | 4 | 3 | 12 | **S-3**: script that sets and reads back exposure, white balance and focus on Windows, macOS, Linux with 3 webcams; table of results. Inverse patterns already tolerate this partially. | During MVP |
| R4 | FPP REST details assumed from memory are wrong (media directory name, playlist entry fields) | 3 | 3 | 9 | **S-1**: against a real FPP 10.4, upload a file, create a media playlist in FPP's UI, `GET` it back, save the JSON as fixtures for the fake FPP. | First ticket of the FPP track |
| R5 | Real facades at night (brick, dark trim, glass) give low scan coverage | 3 | 3 | 9 | **S-5**: owner scans their own house; keep the captured frames as a regression fixture. Manual mode keeps the product usable regardless. | End of MVP calibration track |
| R6 | Browser projector window is not pixel-exact (OS scaling, browser zoom) | 2 | 4 | 8 | **S-4**: 1-px checkerboard page on 3 OSes, inspect with a phone macro photo or the webcam. | During MVP |
| R7 | WebGL preview and Python render visibly disagree | 3 | 2 | 6 | Golden-scene comparison test (ticketed, not a spike) | MVP |
| R8 | Packaging Python + OpenCV + onnxruntime into installers is painful | 3 | 2 | 6 | **S-7**: PyInstaller hello-world with all heavy deps on Windows and macOS | Start of v1 |
| R9 | MobileSAM ONNX export or CPU latency is unacceptable | 2 | 2 | 4 | **S-6**: export, run on a 4-core laptop CPU, measure | Start of v1 |
| R10 | Wind and prop motion ruin alignment over a season | 3 | 1 | 3 | None; documented guidance and feathered masks | – |

### Spike definitions

Spikes are short investigations whose output is a markdown report in `spikes/` plus any fixtures. They need real hardware and so are assigned to the **owner (human)**, except where noted.

| Spike | Needs | Output file | Decision it feeds |
|---|---|---|---|
| S-1 FPP contract | FPP 10.4 on any device | `spikes/S-1-fpp-contract.md`, `studio/backend/tests/fixtures/fpp/*.json` | Corrects `docs/05-interfaces.md` §7 if needed; ticket T-020 (fake FPP) consumes fixtures |
| S-2 Pi 3 playback | Pi 3 + FPP 10.4 + projector or monitor | `spikes/S-2-pi3-playback.md` | Default preset: `pi3-1080p30` or `pi3-720p30` |
| S-3 Webcam controls | 3 webcams, 3 OSes | `spikes/S-3-webcam-controls.md` | Defaults in `devices/camera_opencv.py` |
| S-4 Projector window exactness | Projector, 3 OSes | `spikes/S-4-projector-window.md` | Keep ADR-006 or add a native window |
| S-5 Field scan | Owner's house at night | `spikes/S-5-field-scan.md`, capture set under `fixtures/field/` | Threshold defaults |
| S-6 MobileSAM (Sonnet can do this) | Laptop | `spikes/S-6-mobilesam.md` | ADR-014 |
| S-7 Packaging (Sonnet can do this) | Windows + macOS CI | `spikes/S-7-packaging.md` | ADR-019 |
| S-8 HDMI pixel identity | Laptop, Pi, projector | `spikes/S-8-hdmi-identity.md` | Whether Pi-driven calibration moves into the MVP |

The build does not wait for spikes. Every [A] has a default in the interfaces, and the code isolates each one behind a single module (`fpp/client.py`, `render/presets.py`, `devices/camera_opencv.py`), so a spike result changes one file.

## 6. Suggested order of work

1. Owner runs S-8, S-2, S-1 (one evening with the hardware).
2. Builders do MVP tickets in the order of `tasks/INDEX.md` (four parallel tracks after the foundation).
3. Owner field test (MVP exit criterion 3) and S-5.
4. v1 tickets.
5. Re-plan v2 with field data; write v2 tickets then.
