# HANDOFF

Planning repo for **FacadeKit** (working name): a free, open-source projection-mapping system for holiday decorators that auto-calibrates with a webcam and delivers pre-warped video to Falcon Player (FPP). This repo contains the plan only. Smaller models build from `tasks/`.

Last updated: 2026-11-09.

## 1. Status

| Phase | Output | State |
|---|---|---|
| 0 Questions | Owner answers in `docs/01-landscape.md` §0 | done |
| 1 Landscape | `docs/01-landscape.md` | done |
| 2 Architecture | `docs/02-architecture.md` (ADR-001 to ADR-023 in §9) | done |
| 3 Roadmap | `docs/03-roadmap.md` | done |
| 4 Build plan | `docs/04-repo-structure.md`, `docs/05-interfaces.md`, `tasks/T-001…T-038` (MVP), `tasks/T-101…T-117` (v1), `tasks/INDEX.md` | done |
| 5 Project plan | `docs/06-project.md` | done |

All phases are complete. Nothing is committed to git yet (the repo is initialised with no commits). No code has been written, by design.

Not done, on purpose: v2 tickets (they depend on field data); no ticket was test-driven against real code, so expect a few contract gaps to surface in `tasks/BLOCKERS.md` once builders start.

## 2. Key decisions (details in `docs/02-architecture.md` §9)

- ADR-001: laptop does calibration, editing and rendering; the Pi only plays video through FPP.
- ADR-002: the warp is baked into the video; FPP 10 has no warp at playback (verified).
- ADR-003: all authoring happens in projector pixel space on a camera-derived "projector view" photo; no projector intrinsics or 3D reconstruction.
- ADR-004/007: Gray code with inverse patterns, two finest bits skipped, own NumPy implementation.
- ADR-005/006: camera via backend OpenCV; projector output is a fullscreen browser page over WebSocket.
- ADR-008/009: Python (FastAPI) backend + React/TypeScript UI; Python compositor is authoritative, WebGL preview is approximate, with a parity test.
- ADR-010: H.264 High@4.0, 1080p30, MP4 + AAC as the default export (`pi3-1080p30`), 720p fallback.
- ADR-011/023: FPP integration through its REST API only, FPP 10.x only; an FPP plugin is v2.
- ADR-013: simulator-first testing (virtual house, projector, camera, fake FPP).
- ADR-014/015: MobileSAM (ONNX, CPU) for click-to-segment and librosa for beats, both v1 and local.
- ADR-016: GPL-3.0-or-later for Studio; GPL-2.0-or-later for the future FPP plugin.
- ADR-020: generative features are bring-your-own-key and v2.

## 3. Assumptions made in lieu of answers

Owner answered Q1–Q4 (`docs/01-landscape.md` §0). Unasked assumptions A-1 to A-4 are listed there. Technical assumptions not yet verified on hardware are tagged [A] in `docs/01-landscape.md` and `docs/05-interfaces.md` §7; the important ones:

- FPP's media directory for video is named `videos`, and playlist media entries look like `{"type":"media","mediaName":…,"videoOut":"--Default--"}` (spike S-1).
- A Pi 3 plays 1080p30 H.264 at 8 Mbit/s smoothly under FPP 10 (spike S-2).
- Laptop HDMI and Pi HDMI drive the same projector pixels (spike S-8). If false, ticket T-113 moves into the MVP.

## 4. Open questions for the owner

1. Do you have a Pi 3 and FPP 10.4 available to run spikes S-1, S-2 and S-8 before builders start? They take one evening and remove the three biggest risks.
2. Is GPL-3.0-or-later acceptable, or do you want a permissive licence (decision ADR-016, reasons in `docs/06-project.md` §2)?
3. Final name: keep FacadeKit or pick another candidate from `docs/06-project.md` §1 (needs a trademark and PyPI check either way).

## 5. Exact next step

1. Owner: run spikes S-8, S-2, S-1 as described in `docs/03-roadmap.md` §5 and save reports under `spikes/`.
2. Builders: start wave 1 of `tasks/INDEX.md` (T-001 and T-002 in parallel), then follow the waves. Give each builder session exactly one ticket file.
3. When the MVP exit criteria in `docs/03-roadmap.md` §1 are met, start the v1 table in `tasks/INDEX.md`.

## 6. Files

| File | Purpose |
|---|---|
| `HANDOFF.md` | This file: status and resume instructions |
| `docs/01-landscape.md` | Research on FPP, xLights, commercial and open-source mapping tools, content sellers; gaps and borrowed ideas; sources |
| `docs/02-architecture.md` | System design, calibration pipeline with pseudocode, render/export, runtime split, AI use, ADR log, licensing |
| `docs/03-roadmap.md` | MVP / v1 / v2 / stretch scope, ranked risks, hardware spikes S-1 to S-8 |
| `docs/04-repo-structure.md` | Directory tree, tooling, coding conventions, numeric defaults |
| `docs/05-interfaces.md` | All contracts: JSON schemas, Python signatures, REST + WebSocket API, FPP contract, CLI, error codes (§13 lists late additions) |
| `docs/06-project.md` | Name, licence, contribution model, onboarding docs, hardware testing, sustainability |
| `tasks/INDEX.md` | All tickets in dependency order with models, waves and parallel tracks |
| `tasks/T-001.md` … `T-038.md` | MVP tickets, one per file, self-contained |
| `tasks/T-101.md` … `T-117.md` | v1 tickets |
