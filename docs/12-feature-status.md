# 12. What is real, what is thin, what is missing

Checked against the code on 2026-11-23, not against the plans. "Real" means it is built and has
tests. It does not mean it was tried on hardware. Hardware evidence so far: the owner's first run
(2026-11-14, MacBook camera and a projector, Setup only), a scan with a real Logitech C920
(2026-11-20), and one real reply from the owner's FPP 10.4 to `GET /api/fppd/version`
(`docs/17`). Nothing on Surfaces, Arrange or Publish has run on hardware.

## Setup

| Feature | State | Notes |
|---|---|---|
| Projector window, resolution check | Real | Tried on hardware |
| Camera listing by name, live preview | Real | Rewritten after the hardware run (#28); the new version is untested on hardware. On a Mac the camera permission belongs to the app that started FacadeKit (see `docs/user/troubleshooting.md`) |
| Camera check (verdict, crop suggestion) | Real | |
| Webcam scan (Gray code) | Real | Worked on hardware (78 % measured on 2026-11-14). The scan speed fix (#30) and the exposure lock were run on a real C920 on 2026-11-20: 73.8 % of the projection measured, 0 % washed out. One scan per project at a time: a second start is refused with `CALIBRATION_BUSY` while one is queued or running (simulator and unit tests only) |
| Scan memory and the two floors | Real | Simulator evidence only (`docs/07`, `docs/09`) |
| Why-unmeasured overlay | Real | Simulator only (#31) |
| Exposure on a Mac | **Locks only on some cameras** | macOS gives no exposure, focus or white-balance control for the built-in MacBook camera or the iPhone camera (measured on a MacBook Pro, 2026-11-19). A USB webcam such as the C920 can be locked but not set to a value; the scan locks it on the white picture and, if it still clips, projects dimmer. Verified on a real C920 on 2026-11-20: 0 % washed out. The slider does nothing on any Mac camera |
| Autofocus switch | Real on lockable cameras | Locks or releases focus through AVFoundation on a Mac (C920: yes; built-in camera: no). Untried on Windows and Linux |
| Camera control values for Windows and Linux | Guessed | "Best-guess", spike S-3 not run |
| Phone as camera (light show + video upload) | Real code, never on a phone | Tested with a simulated phone recording |
| Scan with the projector on the Pi | Real code, never on a Pi | Tested against the fake FPP only |
| Manual mode | Thin | Makes a calibration with no camera data; surfaces are then placed by eye. You can add an optional reference photo (a phone photo of the house) as the background on the Surfaces page, labelled "by eye, not measured": it is fitted into the projector frame and never measured, so outlines land where you put them, not where the projector shines. Simulator and unit tests only; Magic select and Check on the house stay off in manual mode |
| Quick re-align | Real | Simulator only |

## Surfaces

| Feature | State | Notes |
|---|---|---|
| Add surface, rectangle, drag corners | Real | |
| Masks with holes, feathering | Real | |
| Mesh editor (bend a surface) | Real | Covered by the parity test |
| Magic select | **Real, smart model optional** | Default model SAM 2.1 tiny; EfficientSAM-Ti and MobileSAM are also available. Each is downloaded only after you confirm "Download model (NN MB)" (size, source and licence shown first), from the pinned upstream files, with SHA-256 checks. Until a model is installed the toolbar says "Simple selector (GrabCut)"; after, it says "Smart select (<model name>)". On synthetic small objects in clutter, SAM 2.1 tiny needs 1.25 clicks on average to reach IoU 0.85; GrabCut never gets there from clicks alone (`docs/16`). Box prompts ("Add rectangle") work with every engine. Never tried on the owner's real photos or hardware |
| Verify surface ("Check on the house" panel on Surfaces: did the rig move? is the light on the object?) | Real, simulator only | `docs/13`. Needs a camera scan (off in manual mode). Never run on a camera, projector or house; `facadekit verify --self-test` is the procedure for the first hardware run |
| Surfaces on non-flat objects (3D) | Missing | Only flat four-corner surfaces plus the mesh bend |

## Arrange

| Feature | State | Notes |
|---|---|---|
| Media import (video, image, audio) | Real | Size caps from AUDIT-02 |
| Timeline, clips, fades, blend modes | Real | |
| Effects (generators) | Real, preview and render checked by the parity test only | Twelve: `solid`, `wash`, `outline`, `strobe`, plus `chase` (marching border), `rings`, `stars`, `snow`, `sweep`, `cycle`, `plasma`, `flicker` (seeded, deterministic). Each new one has a parity scene; none has been seen on a real house or projector. `flicker` is capped at 3 changes a second; `strobe` keeps its T-109 default of 8 Hz but the form warns above 3 Hz |
| Sample projects | Real, simulator only | "Try a sample project" on Projects, or `facadekit new DIR.fkproj --sample [halloween\|winter]`: a simulated house (scan, door found on the scan, two windows) and a 12 s show of built-in effects and drawn pictures, labelled "simulated house". Reaches Publish with no hardware; the walkthrough runs it |
| Live preview in the browser (WebGL) | Real | Approximate by design; the parity test keeps it close to the render |
| Beat detection | Optional, off by default | Needs `librosa` (`facadekit[ai]`), which a normal install does not have |
| Phone as a remote | Real | |

## Publish

| Feature | State | Notes |
|---|---|---|
| Render to MP4 | Real | Preset values unverified on a Pi (spike S-2) |
| Alignment-check video | Real | |
| Send to FPP, playlist, start | Real code, upload never run on FPP | FPP 10 or newer is required (older versions are refused). The fake FPP now follows the contract read from the FPP 10.4 source (`docs/17`), and every failure says why (name not found, refused, timed out, no route, password needed, not FPP, too old). Only the version reply of the owner's FPP 10.4 is a real answer; folder names and playlist fields are still assumptions until `facadekit spike fpp-contract <fpp-address>` is run against a real Pi (spike S-1), correctable in `config.json`. On macOS "Could not reach FPP" also happens when the app that started FacadeKit lacks Local Network access |

## 3D and other scan sources

| Feature | State | Notes |
|---|---|---|
| LiDAR offline pipeline (`facadekit calib3d solve`, `docs/15`, `docs/18`) | Real code, **simulation only** | Reads a capture folder (format v1) and solves a 3D-aware mapping. No phone app, depth sensor, wall or projector has been used; the depth-sensor model is an assumption. No web screen uses it |
| PS4 / stereo camera scanning | Not started | |

## Not built at all

- Sample projects on a photo of a real house (the samples use the simulated house only).
- Everything v2: generative content (bring-your-own-key), the FPP plugin.
- Installers: `packaging/` has a PyInstaller spec, launcher and smoke test, but no built or
  signed installer has been produced.

## What would add the most depth next

1. Run the hardware spikes (S-1, S-2, S-8): everything Publish-side is unverified. S-1 is `facadekit spike fpp-contract <fpp-address>`, run from the Terminal app on the same network as the Pi.
2. Try Magic select with SAM 2.1 tiny on real house and shelf photos (spike S-6 measured synthetic pictures only).
3. Run `facadekit verify --self-test` and the Check on the house panel on the real rig (simulator only so far).
4. Use manual mode with a real reference photo, and try a phone scan on a real phone.
