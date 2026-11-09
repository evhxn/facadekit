# 04 – Repository structure, tooling and conventions

Working name: **FacadeKit**. This file tells a builder where every file goes and how code must look. Contracts between modules are in `docs/05-interfaces.md`.

## 1. Directory tree

```
facadekit/
├── README.md                     quick start for users (T-038)
├── LICENSE                       GPL-3.0-or-later text
├── CONTRIBUTING.md
├── HANDOFF.md                    planning status (not for builders)
├── Makefile                      setup / lint / test / build-web / dev
├── .github/workflows/ci.yml      lint + tests on ubuntu, macos, windows
├── docs/                         plan documents 01–06, later user docs in docs/user/
├── tasks/                        build tickets T-XXX.md and INDEX.md
├── spikes/                       hardware spike reports S-N-*.md (written by the owner)
└── studio/
    ├── backend/                              Python 3.11+
    │   ├── pyproject.toml
    │   ├── src/facadekit/
    │   │   ├── __init__.py                   __version__
    │   │   ├── __main__.py                   calls cli.main()
    │   │   ├── cli.py                        argparse CLI
    │   │   ├── config.py                     data dir, settings
    │   │   ├── errors.py                     FkError + error codes
    │   │   ├── ids.py                        new_id(prefix)
    │   │   ├── models/
    │   │   │   ├── project.py                Project, Surface, Asset, Clip … (pydantic)
    │   │   │   ├── calibration.py            Calibration, CalibQuality …
    │   │   │   └── jobs.py                   Job, JobState
    │   │   ├── store/project_store.py        project folder I/O
    │   │   ├── geom/
    │   │   │   ├── homography.py             quad_homography, apply_h, estimate_aspect, fit_rects
    │   │   │   ├── mask.py                   rasterize_mask
    │   │   │   └── mesh.py                   (v1) mesh warp maps
    │   │   ├── devices/
    │   │   │   ├── base.py                   Camera, ProjectorDisplay protocols
    │   │   │   ├── camera_opencv.py          OpenCvCamera, list_cameras
    │   │   │   └── display_ws.py             WsDisplay (projector browser window)
    │   │   ├── sim/
    │   │   │   ├── scene.py                  SimScene, default_scene
    │   │   │   ├── devices.py                SimDisplay, SimCamera
    │   │   │   └── e2e.py                    end-to-end simulated run
    │   │   ├── calib/
    │   │   │   ├── patterns.py               Gray-code generation
    │   │   │   ├── capture.py                prechecks + capture loop
    │   │   │   ├── decode.py                 camera→projector decode
    │   │   │   ├── maps.py                   projector→camera map, projector view
    │   │   │   ├── pipeline.py               run_calibration (writes files)
    │   │   │   ├── realign.py                (v1) quick re-align
    │   │   │   └── selfclock.py              (v1) Pi-driven calibration
    │   │   ├── media/
    │   │   │   ├── ffmpeg.py                 ffmpeg_exe, run helpers
    │   │   │   ├── probe.py                  probe, make_thumbnail
    │   │   │   └── reader.py                 VideoReader, ClipSource
    │   │   ├── render/
    │   │   │   ├── compositor.py             Compositor (authoritative)
    │   │   │   ├── generators.py             (v1)
    │   │   │   ├── presets.py                PRESETS
    │   │   │   ├── encoder.py                Encoder
    │   │   │   ├── outline.py                outline frame, alignment-check video
    │   │   │   └── pipeline.py               render_project
    │   │   ├── fpp/
    │   │   │   ├── client.py                 FppClient
    │   │   │   ├── publish.py                publish_render
    │   │   │   └── fake.py                   fake FPP server for tests
    │   │   ├── ai/                           (v1)
    │   │   │   ├── beats.py
    │   │   │   ├── segment.py
    │   │   │   └── models.py                 model download manager
    │   │   ├── jobs/runner.py                JobRunner
    │   │   ├── api/
    │   │   │   ├── app.py                    create_app()
    │   │   │   ├── deps.py                   shared state (AppState)
    │   │   │   ├── routes_projects.py
    │   │   │   ├── routes_assets.py
    │   │   │   ├── routes_devices.py
    │   │   │   ├── routes_projector.py
    │   │   │   ├── routes_calibration.py
    │   │   │   ├── routes_render.py
    │   │   │   ├── routes_fpp.py
    │   │   │   ├── routes_jobs.py
    │   │   │   ├── routes_ai.py              (v1)
    │   │   │   └── ws.py                     /ws/projector and /ws/events
    │   │   └── webdist/                      built web UI (generated; git-ignored except .gitkeep)
    │   └── tests/
    │       ├── conftest.py                   shared fixtures (tmp project, sim devices, fake FPP)
    │       ├── fixtures/                     small media + FPP JSON fixtures
    │       └── test_*.py                     one test file per module, named after it
    └── web/                                  TypeScript + React
        ├── package.json, pnpm-lock.yaml, vite.config.ts, tsconfig.json, .eslintrc.cjs
        ├── index.html
        └── src/
            ├── main.tsx, App.tsx, routes.tsx
            ├── api/client.ts                 typed fetch wrappers
            ├── api/types.ts                  TypeScript mirror of the JSON schemas
            ├── api/events.ts                 /ws/events client
            ├── state/projectStore.ts         Zustand store, save/debounce/undo
            ├── gl/homography.ts              3x3 math (mirror of geom/homography.py)
            ├── gl/compositor.ts              WebGL2 preview compositor
            ├── pages/ProjectsPage.tsx
            ├── pages/SetupPage.tsx           wizard: projector, camera, scan
            ├── pages/SurfacesPage.tsx
            ├── pages/ArrangePage.tsx
            ├── pages/PublishPage.tsx
            ├── projector/ProjectorPage.tsx   fullscreen page shown on the projector
            ├── components/                   small shared components
            └── **/*.test.ts(x)               Vitest tests next to the code
```

## 2. Languages and tooling

| Area | Tool | Command |
|---|---|---|
| Python env and deps | `uv` | `cd studio/backend && uv sync --all-extras` |
| Python lint + format | `ruff` (line length 100) | `uv run ruff check . && uv run ruff format --check .` |
| Python types | `mypy` (strict on `src/`) | `uv run mypy src` |
| Python tests | `pytest` | `uv run pytest -q` |
| JS deps | `pnpm` (Node 20+) | `cd studio/web && pnpm install` |
| JS lint + types | `eslint`, `tsc --noEmit` | `pnpm lint && pnpm typecheck` |
| JS tests | `vitest` | `pnpm test` |
| Browser tests | `playwright` (only T-037) | `pnpm e2e` |
| Build web into backend | Vite `outDir` = `../backend/src/facadekit/webdist` | `pnpm build` |
| Everything | `make` at the repo root | `make setup`, `make lint`, `make test`, `make build-web`, `make dev` |

Python runtime dependencies (MVP): `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `numpy`, `opencv-python-headless`, `imageio-ffmpeg`, `httpx`, `python-multipart`. Dev: `pytest`, `ruff`, `mypy`. v1 optional extra `ai`: `librosa`, `onnxruntime`.

Web runtime dependencies (MVP): `react`, `react-dom`, `react-router-dom`, `zustand`. No waveform library: the v1 waveform is custom canvas drawing (T-103). Do not add other runtime dependencies without a ticket saying so.

## 3. Coding conventions

### Python

- Full type hints on every function. `from __future__ import annotations` at the top of each module.
- No module-level mutable state. Shared state lives in `api/deps.py::AppState`.
- Raise `FkError(code, message)` for every expected failure. Never raise bare `Exception` or return `None` to signal an error. Codes are listed in `docs/05-interfaces.md` §10.
- Images are NumPy arrays. Colour images are **BGR `uint8` shaped `(H, W, 3)`** (OpenCV order). Grayscale is `(H, W)`. Float images are `float32`. Sizes passed as tuples are always `(width, height)`.
- Coordinates are `(x, y)` in pixels, origin at the top-left, x to the right, y down, as `float`. Arrays of points are shaped `(N, 2)`.
- Homographies are `numpy.ndarray` `float64` `(3, 3)` and map *source → destination* in the direction named by the variable: `h_c2p` maps camera to projector.
- Times are integer milliseconds in variables ending `_ms`.
- Long operations accept `progress: Callable[[float, str], None] | None` and `cancel: Callable[[], bool] | None`. Call `progress(fraction, message)` at least once per second of work and raise `FkError("CANCELLED", …)` when `cancel()` returns true.
- Tests never need hardware or network. Use `sim` devices and the fake FPP. Tests that write files use `tmp_path`.
- One public class or a small set of related functions per module. Keep modules under about 300 lines.

### TypeScript

- `strict` mode. No `any` except at JSON parse boundaries, immediately narrowed.
- Function components and hooks only. State that must survive navigation lives in the Zustand store.
- All backend calls go through `api/client.ts`. Components never call `fetch` directly.
- Types in `api/types.ts` use the exact field names of the JSON (snake_case). Do not convert case.
- CSS modules, no CSS framework.

### Git

- Branch per ticket: `t-017-compositor`. Commit message prefix: `T-017: …`.
- A ticket is done only when its acceptance commands pass and `make lint` is clean.
- Do not edit files outside the ticket's "Files" list. If a contract seems wrong, stop and write the problem in `tasks/BLOCKERS.md` instead of inventing a different interface.

## 4. Numeric defaults (single source of truth)

| Name | Value | Where it is used |
|---|---|---|
| Default projector | 1920×1080 @ 30 fps | project creation |
| Server port | 8137 | `facadekit serve` |
| Data dir | `~/FacadeKit` (override with `--data-dir` or env `FACADEKIT_DATA`) | config |
| `skip_lsb` | 2 | calibration |
| `settle_ms`, `discard_frames`, `average_frames` | 300, 5, 3 | capture |
| `min_conf`, `outlier_px` | 0.10, 8.0 | decode |
| `grid_step` | 8 | maps |
| Coverage fail / warn | 0.50 / 0.85 | quality |
| Upload chunk | 8 MiB | FPP client |
