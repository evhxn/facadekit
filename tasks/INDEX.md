# Ticket index

Tickets for **FacadeKit** (working name). Each `T-XXX.md` is self-contained: a builder model should need only that file plus the repository. Background lives in `docs/` (02 architecture, 04 repo structure, 05 interfaces).

**Models:** H = Claude Haiku (mechanical work), S = Claude Sonnet (needs judgment), S⚑ = Sonnet with a flag: escalate to a stronger model if the acceptance tests cannot be met after two attempts.

**Rules for builders**

1. Do tickets only when all their dependencies are merged.
2. Touch only the files listed in the ticket. Never change a contract in `docs/05-interfaces.md`; if one seems wrong or missing, write it in `tasks/BLOCKERS.md` and stop.
3. A ticket is done when its Acceptance commands pass and `make lint` is clean.
4. If a `spikes/S-*.md` report exists for something a ticket marks as an assumption, the report wins.

## Hardware spikes (owner, not builder models)

Defined in `docs/03-roadmap.md` §5. Run S-8, S-2 and S-1 before or during wave 1; none of them blocks the build.

| Spike | Affects |
|---|---|
| S-1 FPP contract | T-020, T-021, T-022 |
| S-2 Pi 3 playback | T-018 (default preset) |
| S-3 Webcam controls | T-009 |
| S-4 Projector window exactness | T-030 |
| S-5 Field scan | T-011, T-012 thresholds |
| S-6 MobileSAM export | T-106, T-107 |
| S-7 Packaging | T-116 |
| S-8 HDMI pixel identity | Whether T-113 moves into the MVP |

## MVP

Tickets in the same wave can run in parallel. Waves are in dependency order.

| Wave | Ticket | Title | Model | Depends on |
|---|---|---|---|---|
| 1 | T-001 | Backend scaffold, Makefile, CI | H | – |
| 1 | T-002 | Web UI scaffold | H | – |
| 2 | T-003 | Errors, config, ids | H | 001 |
| 2 | T-007 | Mask rasterisation | H | 001 |
| 2 | T-010 | Gray-code pattern generation | H | 001 |
| 2 | T-020 | Fake FPP server | H | 001 |
| 3 | T-004 | Data models | H | 001, 003 |
| 3 | T-008 | Device protocols and simulator | S | 003 |
| 3 | T-021 | FPP REST client | S | 003, 020 |
| 3 | T-029 | Web API client, types, events, project store | S | 002 |
| 3 | T-030 | Projector page | S | 002 |
| 4 | T-005 | Project store | S | 003, 004 |
| 4 | T-006 | Geometry: homographies, aspect, fit | S | 004 |
| 4 | T-009 | OpenCV webcam | S | 008 |
| 4 | T-011 | Capture loop and pre-checks | S | 004, 008, 010 |
| 4 | T-015 | ffmpeg wrapper, probe, thumbnails | S | 003, 004 |
| 4 | T-022 | Publish workflow | S | 021 |
| 4 | T-023 | Job runner | S | 003, 004 |
| 5 | T-012 | Gray-code decoding | S | 010, 011 |
| 5 | T-016 | Video reader and clip source | S | 015 |
| 5 | T-018 | Presets and encoder | S | 015 |
| 5 | T-024 | API app, project and job routes, `serve`/`new` | S | 005, 023 |
| 5 | T-031 | Projects page and setup wizard | S | 029, 030 |
| 5 | T-032 | Surface editor | S | 029 |
| 5 | T-033 | WebGL compositor and homography (TS) | S⚑ | 029, 030 |
| 5 | T-035 | Publish page | S | 029 |
| 6 | T-013 | Projector→camera map and projector view | S⚑ | 012 |
| 6 | T-017 | Compositor | S | 004, 006, 007, 016 |
| 6 | T-025 | Asset routes | S | 015, 024 |
| 6 | T-034 | Media library and Arrange page | S | 029, 033 |
| 7 | T-014 | Calibration pipeline and files | S | 004, 011, 012, 013 |
| 7 | T-019 | Render pipeline, outlines, alignment check | S | 005, 017, 018 |
| 8 | T-026 | Projector WebSocket, `WsDisplay`, events | S | 008, 019, 024 |
| 8 | T-028 | Frame, render and FPP routes | S | 019, 022, 024 |
| 8 | T-036 | Remaining CLI and simulated end-to-end run | S | 014, 019, 022, 024 |
| 9 | T-027 | Camera and calibration routes | S | 009, 014, 026 |
| 9 | T-037 | Preview / render parity test | S | 028, 033, 034 |
| 9 | T-038 | README and first user guide | H | 036 |

Parallel tracks, for assigning several builders at once:

- **Track A – calibration:** 008 → 009, 010 → 011 → 012 → 013 → 014 → 027
- **Track B – render:** 006, 007, 015 → 016, 018 → 017 → 019
- **Track C – FPP:** 020 → 021 → 022
- **Track D – API:** 003 → 004 → 005, 023 → 024 → 025, 026, 028
- **Track E – web:** 002 → 029, 030 → 031, 032, 033, 035 → 034 → 037

Tracks A, B and C share no files. Track D touches `api/app.py` and `api/deps.py` in several tickets (024, 025, 026, 027, 028): run those one at a time or expect small merge conflicts in those two files only. `tests/conftest.py` is extended by 015, 020 and 024: append fixtures, never rewrite the file.

**MVP done when:** `make test` is green on all three OSes, `facadekit sim e2e` reports a corner error under 3 px, T-037 passes in CI, and the owner's field test (roadmap §1) succeeds.

## v1

| Wave | Ticket | Title | Model | Depends on |
|---|---|---|---|---|
| 1 | T-101 | Polygon masks with holes | S | 032, 033 |
| 1 | T-102 | Interactive timeline with undo/redo | S | 034 |
| 1 | T-104 | Beat and onset detection (backend) | S | 023, 025 |
| 1 | T-107 | Model download manager | H | 003 |
| 1 | T-110 | Mesh warp (backend) | S | 017 |
| 1 | T-112 | Quick re-align | S | 027, 032 |
| 1 | T-114 | Pair with an `.fseq` sequence | H | 028, 035 |
| 1 | T-115 | Automatic latency and exposure tuning | S | 011, 014 |
| 1 | T-117 | Phone remote for fine alignment | S | 024, 032 |
| 2 | T-103 | Audio waveform | H | 102 |
| 2 | T-106 | Click-to-segment backend | S⚑ | 027, 107 |
| 2 | T-109 | Generator clips | S | 017, 033, 034 |
| 2 | T-111 | Mesh warp editor and preview | S | 101, 110 |
| 2 | T-113 | Pi-driven calibration | S⚑ | 014, 018, 022, 027 |
| 2 | T-116 | Installers | S | 036, 107 |
| 3 | T-105 | Beat markers and "cut on beats" | H | 102, 103, 104 |
| 3 | T-108 | Click-to-segment UI | S | 101, 106 |

Conflict notes: T-101, T-109 and T-111 all edit `gl/compositor.ts` and add scenes to `e2e/scenes.ts`; do them in the order 101 → 109 → 111. T-102, T-103 and T-105 all edit the timeline component; do them in order. T-104 and T-106 both create or extend `api/routes_ai.py`.

## v2 and later (not ticketed)

Scope is in `docs/03-roadmap.md` §3–4: FPP script plugin, Pi-attached camera, audience-view correction, two projectors with blending, automatic surface detection, auto-sequencing, BYO-key generative content, phone as camera, projector power control, HEVC/60 fps presets. They are deliberately not broken into tickets: each depends on field results from the MVP and v1 (spikes S-2, S-5, S-8 in particular). Write those tickets after the v1 exit review.

## Counts

MVP: 38 tickets (8 Haiku, 30 Sonnet, 2 of them flagged). v1: 17 tickets (4 Haiku, 13 Sonnet, 2 flagged).
