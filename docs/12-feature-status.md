# 12. What is real, what is thin, what is missing

Checked against the code on 2026-11-19, not against the plans. "Real" means it is built and has
tests. It does not mean it was tried on hardware: only the owner's first hardware run
(2026-11-14, MacBook camera and a projector) has happened, and it covered Setup only.

## Setup

| Feature | State | Notes |
|---|---|---|
| Projector window, resolution check | Real | Tried on hardware |
| Camera listing by name, live preview | Real | Rewritten after the hardware run (#28); the new version is untested on hardware |
| Camera check (verdict, crop suggestion) | Real | |
| Webcam scan (Gray code) | Real | Worked on hardware (78 % measured). Scan speed fix (#30) untested on hardware |
| Scan memory and the two floors | Real | Simulator evidence only (`docs/07`, `docs/09`) |
| Why-unmeasured overlay | Real | Simulator only (#31) |
| Exposure on a Mac | **Locks only on some cameras** | macOS gives no exposure, focus or white-balance control for the built-in MacBook camera or the iPhone camera (measured on a MacBook Pro, 2026-11-19). A USB webcam such as the C920 can be locked but not set to a value; the scan locks it on the white picture and, if it still clips, projects dimmer. The slider does nothing on any Mac camera |
| Autofocus switch | Real on lockable cameras | Locks or releases focus through AVFoundation on a Mac (C920: yes; built-in camera: no). Untried on Windows and Linux |
| Camera control values for Windows and Linux | Guessed | "Best-guess", spike S-3 not run |
| Phone as camera (light show + video upload) | Real code, never on a phone | Tested with a simulated phone recording |
| Scan with the projector on the Pi | Real code, never on a Pi | Tested against the fake FPP only |
| Manual mode | Thin | Makes a calibration with no camera data; surfaces are then placed by eye on a blank view |
| Quick re-align | Real | Simulator only |

## Surfaces

| Feature | State | Notes |
|---|---|---|
| Add surface, rectangle, drag corners | Real | |
| Masks with holes, feathering | Real | |
| Mesh editor (bend a surface) | Real | Covered by the parity test |
| Magic select | **GrabCut only** | MobileSAM is wired up but its model files were never published: the registry checksums still read `TO_BE_FILLED_BY_SPIKE_S6`, so it never loads |
| Surfaces on non-flat objects (3D) | Missing | Only flat four-corner surfaces plus the mesh bend |

## Arrange

| Feature | State | Notes |
|---|---|---|
| Media import (video, image, audio) | Real | Size caps from AUDIT-02 |
| Timeline, clips, fades, blend modes | Real | |
| Effects (generators) | Thin | Three: `solid`, `wash`, `outline` |
| Live preview in the browser (WebGL) | Real | Approximate by design; the parity test keeps it close to the render |
| Beat detection | Optional, off by default | Needs `librosa` (`facadekit[ai]`), which a normal install does not have |
| Phone as a remote | Real | |

## Publish

| Feature | State | Notes |
|---|---|---|
| Render to MP4 | Real | Preset values unverified on a Pi (spike S-2) |
| Alignment-check video | Real | |
| Send to FPP, playlist, start | Real code, never on FPP | Fake FPP only; folder names and playlist fields are assumptions (spike S-1), correctable in `config.json` |

## Not built at all

- Sample projects with CC0 content (`docs/06`, v1).
- Everything v2: generative content (bring-your-own-key), the FPP plugin.
- Installers: `packaging/` has a PyInstaller spec, launcher and smoke test, but no built or
  signed installer has been produced.

## What would add the most depth next

1. Run the hardware spikes (S-1, S-2, S-8): everything Publish-side is unverified.
2. A second hardware run of Setup with the merged fixes, keeping the new log.
3. Publish the MobileSAM models (spike S-6) or remove the option, so Magic select is honest.
4. Try the exposure lock and dimmed projection with a real C920 (only the simulator has run it).
