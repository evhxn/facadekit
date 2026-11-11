# REVIEW-01 – Review of the built MVP + v1, with fix tickets

Reviewed: `main` at `4571e43` (all 55 tickets T-001…T-117 implemented), 2026-11-10.
Audience: the owner, and builder models picking up the fix tickets in §3. Each fix ticket is self-contained.

## 1. What was checked and what was found

| Check | How | Result |
|---|---|---|
| Backend lint, format, types | `ruff check`, `ruff format --check`, `mypy src` | Clean |
| Backend tests | `uv run pytest -q` | 293 passed, 1 skipped (MobileSAM test, needs models) |
| Web lint, types, unit tests | `pnpm lint`, `pnpm typecheck`, `pnpm test` | Clean; 202 tests passed |
| Simulated end-to-end | `facadekit sim e2e` | Passes. Max corner error **2.57 px** against a 3.0 px limit |
| App smoke test | `facadekit serve --sim`, project → scan → open all four pages in a browser | Works; scan job succeeds; no console errors |
| GitHub CI | `gh run list` | **Never ran.** The only run failed in 0 s with a workflow-file error |
| Cross-origin behaviour | `curl` with a foreign `Host` and `Origin` | **Accepted.** See R-002 |
| Code read | `api/app.py`, `api/remote_auth.py`, `render/compositor.py`, `render/presets.py`, `fpp/publish.py`, `config.py`, workflows, README | Findings below |

Not checked: the Playwright parity suite (T-037) was not run in this review and has never run in CI; the release workflow has never been triggered; about 60 of 68 backend modules and the web source were not read line by line; nothing was tested on real hardware. Spikes S-1, S-2 and S-8 are still not run.

Overall: the builders followed the tickets closely, the code is typed, tested and readable, and deviations were recorded (`tasks/BLOCKERS.md`, small ticket text fixes). The parity work in particular found and fixed a real half-pixel convention bug in the plan's compositor spec. The problems below are mostly gaps in the plan itself, not builder mistakes.

## 2. Findings, most important first

| # | Severity | Finding | Fix |
|---|---|---|---|
| F1 | High | CI is broken, so nothing has been verified on Linux or Windows. `.github/workflows/ci.yml` uses `hashFiles()` in a job-level `if:`, which GitHub rejects. (The plan's T-001 asked for this; the plan was wrong.) | R-001 |
| F2 | High | The local server trusts any request that arrives on loopback. It does not check the `Host` or `Origin` header. A web page open in the user's browser can connect to `ws://127.0.0.1:8137/ws/projector` (WebSockets have no same-origin rule) and take over the projector window, and a DNS-rebinding page can call every API route, including deleting projects and making the machine send HTTP requests to LAN hosts via the FPP routes. Verified: `Host: evil.example.com` returns 200; a WebSocket upgrade with a foreign `Origin` returns 101. | R-002 |
| F3 | Medium | The README says `pipx install facadekit`. The package is not on PyPI (checked: 404), and the built web UI is git-ignored, so installing from the git URL gives a backend with no UI. A new user cannot install the project by following the README. | R-003 |
| F4 | Medium | Rendered video carries no colour tags and is converted RGB→YUV with ffmpeg's default matrix (BT.601). HD players assume BT.709, so reds and greens shift slightly on the Pi. | R-004 |
| F5 | Medium | End-to-end accuracy is 2.57 px with a 3.0 px limit: little margin before real-world noise. The compositor had a half-pixel convention bug (fixed in T-037); the calibration map, projector-view and simulator code use the same OpenCV calls and were never audited for it. | R-005 |
| F6 | Medium | "Magic select" can never use MobileSAM: `ai/model_registry.json` still has `OWNER` and `TO_BE_FILLED_BY_SPIKE_S6`. It always falls back to GrabCut and the UI tells the user a model download failed. | R-006 |
| F7 | Low | The UI is functional but unstyled (843 lines of CSS in total, browser-default buttons and inputs). For non-technical decorators this will read as unfinished. | R-007 |
| F8 | Low | `AGENTS.md` requires unverified FPP and output values to live in config. They are module constants (`fpp/publish.py`, `render/presets.py`), which is tidy but not user-overridable without editing code. | R-008 |
| F9 | Low | Housekeeping: `HANDOFF.md` still says no code exists; stale local branches `t-001-backend-scaffold`, `t-002-web-scaffold`; Starlette's test client warns that `httpx` support is deprecated; the release workflow is untested. | R-009 |

## 3. Fix tickets

Rules are the same as `tasks/INDEX.md`: touch only the listed files, acceptance commands must pass, `make lint` clean. Suggested order: R-001, R-002, R-003, R-004, then the rest in any order. R-001 to R-004 touch disjoint files and can run in parallel.

---

### R-001 – Make CI run

- **Model:** Haiku
- **Files:** `.github/workflows/ci.yml`

**Goal.** Get the CI workflow accepted by GitHub and green on all jobs.

**Steps.**

1. In job `web`, delete the line `if: hashFiles('studio/web/package.json') != ''` (the web project exists; `hashFiles` is not allowed in a job-level `if`).
2. Add a job `actionlint` on `ubuntu-latest`: checkout, then run `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest -color` so workflow syntax errors (including in `release.yml`) are caught in pull requests.
3. Push to a branch and open a pull request. Fix whatever fails on `ubuntu-latest` and `windows-latest` (these have never run). Typical suspects: path separators, file locking in tests that leave ffmpeg subprocesses alive, the `parity` job's Chromium flags.
4. If a failure needs a source change outside this file, stop and add an entry to `tasks/BLOCKERS.md` naming the failing test and OS.

**Acceptance.** The pull request shows jobs `backend` (3 OSes), `web`, `parity` and `actionlint` all green.

**Out of scope.** Changing tests to skip on an OS without recording why.

---

### R-002 – Reject foreign Host and Origin headers

- **Model:** Sonnet
- **Files:** create `studio/backend/src/facadekit/api/origin_guard.py`, `studio/backend/tests/test_api_origin_guard.py`; modify `studio/backend/src/facadekit/api/app.py`, `studio/backend/src/facadekit/cli.py` (only to pass the port through if needed)

**Background.** FacadeKit Studio runs an HTTP + WebSocket server on the user's own machine with no login. `api/remote_auth.py` already blocks non-loopback clients unless LAN mode is on and the device is paired. It does not stop a **web page in the user's own browser** from talking to the server: the request then comes from loopback. Two attacks follow. (a) Any page can open `ws://127.0.0.1:8137/ws/projector` or `/ws/events`, because browsers do not apply the same-origin policy to WebSockets. (b) With DNS rebinding, a page on `evil.example.com` re-points its hostname at `127.0.0.1` and can then call every API route as same-origin; the request carries `Host: evil.example.com`.

**Goal.** Add an ASGI middleware that rejects requests whose `Host` is not one of ours, and state-changing or WebSocket requests whose `Origin` is not one of ours.

**Specification.**

```python
# api/origin_guard.py
def allowed_hostnames(*, lan: bool, extra: Iterable[str] = ()) -> set[str]
    # always: "localhost", "127.0.0.1", "::1"
    # when lan is True: every address from facadekit.api.remote_auth.lan_addresses(), plus socket.gethostname()
    #   and f"{socket.gethostname()}.local", lower-cased
    # plus `extra` and the comma-separated env var FACADEKIT_ALLOWED_HOSTS (trimmed, lower-cased, empty items dropped)

def host_of(header_value: str) -> str | None
    # parse "name", "name:port", "[::1]:port" -> lower-cased hostname without brackets or port; None if malformed

class OriginGuardMiddleware:        # pure ASGI, same style as RemoteAccessMiddleware
    def __init__(self, app: ASGIApp, *, hostnames: set[str]) -> None: ...
```

Rules, applied to scopes of type `http` and `websocket` only:

1. `Host` header missing, malformed, or its hostname not in `hostnames` → reject.
2. If an `Origin` header is present **and** (the scope is a WebSocket, or the HTTP method is not `GET`/`HEAD`/`OPTIONS`): parse it with `urllib.parse.urlsplit`; scheme must be `http` or `https` and its hostname must be in `hostnames`; otherwise reject. The port is not compared (the Vite dev server on port 5173 proxying to 8137 must keep working). The literal origin `null` is rejected.
3. A WebSocket request **without** an `Origin` header is allowed (non-browser clients and the test client send none).
4. Rejection: HTTP → status 403, JSON body `{"error": {"code": "BAD_REQUEST", "message": "Request from an unknown site was blocked"}}`. WebSocket → consume the `websocket.connect` message, then send `{"type": "websocket.close", "code": 1008}`.

Wiring in `create_app`: add this middleware so it runs **before** `RemoteAccessMiddleware` (Starlette runs the last-added middleware first, so add it after). Pass `allowed_hostnames(lan=lan)`. Starlette's `TestClient` sends `Host: testserver`; add `"testserver"` to the set only when `create_app` is called with a new keyword `testing: bool = False` set to true, and update the `client` fixture in `tests/conftest.py` to pass it (that file may be edited for this one line).

**Pitfalls.**

- Header names in the ASGI scope are lower-case bytes; decode values as latin-1.
- IPv6 literals: `Host: [::1]:8137` must parse to `::1`.
- Do not add CORS headers. The app is same-origin by design.
- The packaged app and `--port 0` use other ports; never compare ports.

**Acceptance.**

```
cd studio/backend && uv run pytest -q && uv run mypy src && uv run ruff check .
```

New tests: `host_of` cases (`localhost:8137`, `127.0.0.1`, `[::1]:8137`, `EXAMPLE.com:80`, empty string → `None`); `GET /api/v1/health` with `Host: evil.example.com` → 403; with `Host: localhost:8137` → 200; `POST /api/v1/projects` with `Origin: http://evil.example.com` → 403; same with `Origin: http://localhost:5173` → 201; `GET` with a foreign `Origin` → 200 (reads are protected by the Host rule and the browser's own same-origin policy); WebSocket `/ws/events` with `Origin: http://evil.example.com` → closed with 1008; without `Origin` → accepted; `FACADEKIT_ALLOWED_HOSTS=studio.lan` allows `Host: studio.lan`; with `lan=True` a LAN address is an accepted Host (monkeypatch `lan_addresses` to return `["192.168.1.20"]`). All existing tests still pass.

**Out of scope.** Authentication for loopback clients, HTTPS, changing the pairing flow.

---

### R-003 – Truthful install instructions and a working source install

- **Model:** Haiku
- **Files:** `README.md`, `docs/user/quick-start.md`, `Makefile`

**Facts.** The package `facadekit` is **not published on PyPI**. The web UI is built by `pnpm build` into `studio/backend/src/facadekit/webdist/`, which is git-ignored, so the UI exists only after a build. Prebuilt installers are produced by `.github/workflows/release.yml` on `v*` tags, and no tag has been pushed yet. The repository is `https://github.com/evhxn/facadekit`. Tools needed for a source install: `git`, `uv`, Node 20+, `pnpm`.

**Steps.**

1. In `README.md` replace the Install section with: a status line ("There is no packaged release yet. For now, install from source."), then the source steps:
   ```
   git clone https://github.com/evhxn/facadekit
   cd facadekit
   make setup
   make build-web
   cd studio/backend && uv run facadekit serve
   ```
   Keep one sentence saying `pipx install facadekit` and downloadable apps will come with the first release.
2. Everywhere the README or quick-start says `facadekit serve`, `facadekit doctor` or `facadekit serve --sim` as a bare command, add once near the top: "From a source install, run commands as `uv run facadekit …` inside `studio/backend`."
3. Add a `make run` target: `cd studio/backend && uv run facadekit serve`, and `make run-sim` with `--sim`. Mention them in the README.
4. Remove "Python 3.11 or newer" from the hardware list and put it in the install section with the other tools.

**Acceptance.** On a clean clone, the five commands above end with the UI opening in the browser (do it and say so in the pull request). No sentence in `README.md` or `docs/user/quick-start.md` tells the user to run a command that does not work today. All relative links still resolve.

**Out of scope.** Publishing to PyPI, creating a release, screenshots.

---

### R-004 – Tag and convert rendered video as BT.709

- **Model:** Haiku
- **Files:** `studio/backend/src/facadekit/render/encoder.py`, `studio/backend/tests/test_render_encoder.py`

**Background.** `Encoder` pipes raw `bgr24` frames into ffmpeg, which converts them to `yuv420p` for H.264. Without instructions ffmpeg uses the BT.601 matrix and writes no colour metadata. Players treat untagged HD video as BT.709, so colours come out slightly wrong. The fix is to convert with the BT.709 matrix, limited range, and tag the stream.

**Steps.**

1. In the ffmpeg command built by `Encoder`, add immediately before the preset's `video_args`:
   ```
   -vf scale=in_range=pc:out_range=tv:out_color_matrix=bt709
   -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv
   ```
   Put these in a module constant `COLOR_ARGS: list[str]` with a comment that the Pi decoder honouring the tags is unverified until spike S-2.
2. Do not change `Preset` objects or frame sizes; the `scale` filter here has no size arguments, so it only converts colour.

**Acceptance.**

```
cd studio/backend && uv run pytest -q tests/test_render_encoder.py tests/test_render_pipeline.py && uv run mypy src && uv run ruff check .
```

New tests: the stderr text of `ffmpeg -i <output>` (helper `facadekit.media.ffmpeg.ffmpeg_info`) contains `bt709`; encode 10 frames of pure red `(0, 0, 255)` BGR at 64×64 with the `pi3-1080p30` video args, decode the middle frame with `VideoReader`, and the centre pixel is within 12 levels of `(0, 0, 255)` per channel; same for pure green and a mid-gray `(128, 128, 128)` (within 6). Existing tests still pass.

**Out of scope.** HDR, 10-bit, changing presets.

---

### R-005 – Audit pixel-coordinate conventions in calibration and tighten the end-to-end limit

- **Model:** Sonnet (escalate to a stronger model if the error cannot be brought under the target)
- **Files:** `studio/backend/src/facadekit/calib/decode.py`, `calib/maps.py`, `sim/scene.py`, `sim/e2e.py`, and their tests; add `studio/backend/tests/test_pixel_conventions.py`

**Background.** The project stores points in **pixel-corner** coordinates: `(0, 0)` is the top-left corner of the top-left pixel and a pixel's centre is at `+0.5`. OpenCV's `warpPerspective` and `remap` treat integer coordinates as pixel **centres**. `render/compositor.py` already corrects for this by conjugating homographies with a half-pixel shift (see the comment there and `tasks/BLOCKERS.md`). The calibration and simulator code call the same OpenCV functions and were written before that fix. `facadekit sim e2e` currently reports a 2.57 px maximum corner error with a 3.0 px limit.

**Goal.** Make every stage use the pixel-corner convention consistently, measure each stage's error separately, and reach an end-to-end corner error below **1.5 px** in the simulator.

**Steps.**

1. Write `tests/test_pixel_conventions.py` first, with one test per stage that isolates it:
   - *Simulator:* project a single white projector pixel block (4×4 px at a known position) through `SimScene.render_camera` with `blur_sigma=0, noise_sigma=0`; the intensity-weighted centroid in the camera image must be within 0.25 px of `apply_h(scene.h_p2c, block centre in pixel-corner coordinates)`.
   - *Decode:* with `skip_lsb=0` and an identity "camera" (projector image used directly), the decoded value for camera pixel index `(x, y)` must be `(x + 0.5, y + 0.5)`.
   - *Maps:* for a known homography, `p2c[y, x]` must equal `apply_h(inv(h_c2p), (x + 0.5, y + 0.5))` (the camera position of that projector pixel's **centre**, in pixel-corner coordinates) within 0.5 px.
   - *Projector view:* a camera image containing a single bright dot at a known camera position must appear in `make_proj_view` at the projector pixel predicted by the homography, within 0.75 px (centroid).
2. Run them. For each failing stage apply the half-pixel correction in that module: when calling `cv2.warpPerspective` with a homography expressed in pixel-corner coordinates use `T(-0.5) @ H @ T(+0.5)`; when calling `cv2.remap` subtract 0.5 from map values; when a grid node represents a position, decide whether it is a corner or a centre and document it in the function docstring.
3. In `sim/e2e.py`, log the corner error of (a) the detected door quad versus ground truth and (b) the calibration map alone (ground-truth door corners pushed through `p2c` versus `scene.h_p2c`), so future regressions show which half is responsible.
4. Lower the pass limit in `facadekit sim e2e` and `tests/test_sim_e2e.py` from 3.0 to 1.5 px. If the result stays above 1.5 px after the convention fixes, do not loosen anything: record the per-stage numbers in `tasks/BLOCKERS.md`.

**Acceptance.**

```
cd studio/backend && uv run pytest -q && uv run mypy src && uv run ruff check .
uv run facadekit sim e2e        # prints both error figures; max corner error < 1.50 px
```

**Out of scope.** Changing the Gray-code scheme, sub-pixel decoding, the compositor (already corrected).

---

### R-006 – Honest "Magic select" until the MobileSAM models are published

- **Model:** Haiku
- **Files:** `studio/backend/src/facadekit/ai/models.py`, `studio/backend/src/facadekit/ai/model_registry.json`, `studio/backend/src/facadekit/api/routes_ai.py`, `studio/web/src/pages/surfaces/MagicSelect.tsx`, and their tests

**Background.** `ai/model_registry.json` has placeholder URLs (`OWNER`) and hashes (`TO_BE_FILLED_BY_SPIKE_S6`). `ensure_model` therefore always raises `MODEL_DOWNLOAD_FAILED`, the segmenter falls back to GrabCut, and the UI shows "model download failed", which is false: nothing was attempted.

**Steps.**

1. In the registry, change `OWNER` to `evhxn`. Leave the hashes as placeholders.
2. In `models.py` add `def is_published(name: str) -> bool` (false when the hash starts with `TO_BE_FILLED`).
3. Add `GET /api/v1/ai/capabilities` → `{"segment_engines": ["grabcut"] or ["mobilesam", "grabcut"], "beats": bool}` where MobileSAM is listed only when both model entries are published and `onnxruntime` imports, and `beats` is true when `librosa` imports. Do the import checks with `importlib.util.find_spec` (do not import the heavy modules).
4. In `MagicSelect.tsx`, call the capabilities route once. When MobileSAM is not available, request `prefer: "grabcut"` from the first click and show no failure note; show a neutral badge "Simple selector".
5. Add a section to `spikes/README.md` (create the file) describing spike S-6's output: the two ONNX files uploaded to the GitHub release tag `models-v1`, and the SHA-256 and size values to paste into the registry.

**Acceptance.** Backend and web test suites pass, including: capabilities returns only `grabcut` with the placeholder registry; with a temp registry whose hashes are real it lists `mobilesam` when `onnxruntime` is importable; the UI test shows no "download failed" text when capabilities lack MobileSAM, and sends `prefer: "grabcut"`.

**Out of scope.** Exporting or hosting the models (spike S-6, owner).

---

### R-007 – Visual pass on the UI

- **Model:** Sonnet
- **Files:** `studio/web/src/**/*.module.css`, a new `studio/web/src/theme.css`, and class-name-only changes in components

**Background.** The UI works but uses browser-default controls and almost no layout styling. Users are hobbyist decorators, often working outside at night on a laptop.

**Specification.**

1. `theme.css` with CSS variables: a dark theme by default (it is used outdoors at night; a bright white page also ruins night vision and lights up the scan), a light theme under `@media (prefers-color-scheme: light)` only if the user's system asks for it, spacing scale, one accent colour, focus-ring style.
2. Shared styles for buttons (primary, secondary, danger), inputs, selects, sliders, cards, banners (error, warning, info), and the progress bar.
3. App shell: the four steps (Setup, Surfaces, Arrange, Publish) as a numbered stepper showing which are complete (Setup complete = project has a calibration; Surfaces = at least one surface; Arrange = at least one clip; Publish = at least one render).
4. Setup wizard: one clear primary action per step, the instruction text in a readable size (≥ 16 px), the step counter as a stepper.
5. Minimum target size 36 px for buttons; visible keyboard focus everywhere; contrast ratio ≥ 4.5:1 for text.
6. No new runtime dependencies, no CSS framework, no behaviour changes. Do not change any text that tests assert on.

**Acceptance.** `pnpm lint && pnpm typecheck && pnpm test` pass unchanged. `pnpm e2e` (parity) still passes. Screenshots of all four pages plus the projects page in simulator mode are attached to the pull request, at 1280×800 and 390×844.

**Out of scope.** Redesigning flows, icons or illustrations, animations, the projector page (it must stay a bare canvas).

---

### R-008 – User-overridable FPP and encoding assumptions

- **Model:** Sonnet
- **Priority:** low; do it only if spike S-1 or S-2 shows the defaults are wrong for some setups
- **Files:** `studio/backend/src/facadekit/config.py`, `fpp/publish.py`, `render/presets.py`, tests

**Goal.** Let a user correct an unverified assumption without editing code, by a file `config.json` in the data directory.

**Specification.** `config.load_overrides() -> dict[str, Any]` reads `<data_dir>/config.json` if present (invalid JSON → `FkError("BAD_REQUEST", "config.json is not valid JSON")`). Recognised keys, all optional: `fpp.video_dir` (string), `fpp.video_out` (string), `fpp.entry_type_media` (string), `fpp.entry_type_both` (string), `render.presets.<id>.video_args` (list of strings), `render.default_preset` (string). `publish.py` and `presets.py` read through small accessor functions (`video_dir()`, `get_preset(id)`) instead of using constants directly. Unknown keys are ignored with one warning log line. `facadekit doctor` prints the active overrides.

**Acceptance.** Tests: with a temp data dir and `{"fpp": {"video_dir": "media"}}` the publish flow uploads to `media` on the fake FPP; an override of `video_args` appears in the encoder command; invalid JSON raises `BAD_REQUEST`; no file → defaults. Full suite passes.

---

### R-009 – Housekeeping

- **Model:** Haiku
- **Files:** `HANDOFF.md`, `tasks/INDEX.md`, `studio/backend/pyproject.toml`

1. Rewrite `HANDOFF.md` §1 and §5: all MVP and v1 tickets are implemented; next steps are the fix tickets in this file, then hardware spikes S-8, S-2, S-1, then the owner's field test. Remove the sentence saying no code has been written.
2. In `tasks/INDEX.md` add a short "Status" section at the top: MVP and v1 built on 2026-11-10; link to this review.
3. The test run prints `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`. Add `httpx2` to the dev dependency group if it resolves with the current lock (`uv add --dev httpx2`, then run the full suite); if it does not resolve or tests change behaviour, leave it and note that in the pull request.
4. Trigger the release workflow once on a throwaway tag in a fork or with `workflow_dispatch` (add that trigger) and record the outcome in `packaging/README.md`. Do not publish a real release.
5. Delete the merged local branches `t-001-backend-scaffold` and `t-002-web-scaffold` only if `git branch --merged main` lists them.

**Acceptance.** `HANDOFF.md` matches the repository's real state; the full backend suite passes; the release workflow's result is written down.

## 4. Still open for the owner (no ticket can do these)

1. Run hardware spikes S-8, S-2 and S-1 (`docs/03-roadmap.md` §5). Everything FPP-facing is still tested only against the fake server.
2. Run spike S-6 (export MobileSAM to ONNX, upload to a `models-v1` release) if click-to-segment quality matters before launch.
3. Decide on the name and licence questions in `HANDOFF.md` §4 before publishing to PyPI; the name `facadekit` is currently free there.
4. Field test on a real house, then write the v2 tickets.
