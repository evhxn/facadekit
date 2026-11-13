# HANDOFF

Planning repo for **FacadeKit** (working name): a free, open-source projection-mapping system for holiday decorators that auto-calibrates with a webcam and delivers pre-warped video to Falcon Player (FPP). The repo holds the plan (`docs/`, `tasks/`) and the implementation built from it (`studio/`).

Last updated: 2026-11-13.

## 1. Status

| Phase | Output | State |
|---|---|---|
| 0 Questions | Owner answers in `docs/01-landscape.md` §0 | done |
| 1 Landscape | `docs/01-landscape.md` | done |
| 2 Architecture | `docs/02-architecture.md` (ADR-001 to ADR-023 in §9) | done |
| 3 Roadmap | `docs/03-roadmap.md` | done |
| 4 Build plan | `docs/04-repo-structure.md`, `docs/05-interfaces.md`, `tasks/T-001…T-038` (MVP), `tasks/T-101…T-117` (v1), `tasks/INDEX.md` | done |
| 5 Project plan | `docs/06-project.md` | done |

All phases are complete and built. MVP (T-001 to T-038), v1 (T-101 to T-117) and the camera-quality and phone-camera tickets (T-201 to T-208, `docs/08`) are implemented in `studio/backend` (Python) and `studio/web` (React/TypeScript). Two reviews followed: `tasks/REVIEW-01.md` (fix tickets R-001 to R-009; all done except R-008, which is pull request #18) and `tasks/AUDIT-02.md` (three major findings fixed; the medium and minor ones are fixed or listed there). Scan memory and its evaluation are described in `docs/07`.

Not done, on purpose: v2 tickets (they depend on field data). Hardware spikes S-1, S-2, S-8 and the rest of `docs/03-roadmap.md` §5 have not been run, so everything FPP-facing is tested only against the fake server and every scan result comes from the simulator. The release workflow (`.github/workflows/release.yml`) has not been run; see `packaging/README.md`. GitHub Actions is not running for this repository at the moment, so pull requests are checked with the local gates (`uv run ruff check . && ruff format --check . && mypy src && pytest -q` in `studio/backend`; `pnpm lint && pnpm typecheck && pnpm test` in `studio/web`).

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

1. Owner: run hardware spikes S-8, S-2, S-1 as described in `docs/03-roadmap.md` §5 and save reports under `spikes/`. If S-1 shows different FPP directory names or playlist fields, set them in `<data dir>/config.json` once R-008 (pull request #18) is merged; no code change is needed then.
2. Owner: field test on a real house (`docs/03-roadmap.md` §1). Keep the scan log (`<data dir>/learning/scans.jsonl`): it is the first real data for scan memory.
3. Then write the v2 tickets.
4. Open items from `tasks/AUDIT-02.md` §4 that nobody has decided to do: M4 (phone analysis memory) and M8 (compositor rebuilt per `/frame` request).

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
| `docs/07-scan-memory.md` | Scan memory: how the wizard learns a scan strategy from failed and rejected scans (bandit, routes, limits) |
| `docs/08-camera-quality-and-phone-camera.md` | Camera check, crop, picture-scale verdict, phone as camera (T-201 to T-208) |
| `tasks/REVIEW-01.md`, `tasks/AUDIT-02.md` | The two reviews of the built code and their fix tickets |
| `tasks/INDEX.md` | All tickets in dependency order with models, waves and parallel tracks |
| `tasks/T-001.md` … `T-038.md` | MVP tickets, one per file, self-contained |
| `tasks/T-101.md` … `T-117.md` | v1 tickets |
