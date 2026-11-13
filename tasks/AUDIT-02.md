# AUDIT-02: audit of main and the open pull requests

Date: 2026-11-12. Audited: `main` at `684aba2` and open PRs #8 to #12. Author: the session that wrote the original plan (docs 01 to 06).

## 1. Result

Three major problems were found and each has its own pull request. Nothing was merged.

| Id | Severity | Problem | Fix |
|---|---|---|---|
| A1 | Major (security) | `Asset.filename` and `Project.calibration_id` were unvalidated and are joined to the project folder. A saved project (also from a paired phone, which may `PUT /projects/{pid}`) could make the asset routes serve or delete any file the user can reach. | PR #13 |
| A2 | Major (security) | Any web page could make the browser `GET` the local API. The camera snapshot opens the webcam; `/fpp/status` and `/fpp/sequences` make the backend call a host the page chooses. | PR #14 |
| A3 | Major (repo hygiene) | PR #9 adds a 6.2 MB `studio/backend/pv.npy` that nothing uses. | PR #15, stacked on #9 |

Also fixed in this PR: the status table in `HANDOFF.md` §1 was broken by an earlier edit of mine (the row for phase 5 had been overwritten by the scan-memory file row).

## 2. What was checked, and how far

| Area | Depth |
|---|---|
| Local gates on `main` | Run. Backend: ruff, format, mypy clean; 440 passed, 2 skipped. Web: lint, typecheck clean; 245 tests passed. |
| Origin guard, remote auth, project store, asset upload, project/projector/FPP/render routes, WebSockets | Read in full |
| Compositor, render pipeline, encoder, presets, FPP client and publish | Read in full |
| Calibration core (`maps.py`, `pipeline.py`, `capture.py`, `decode.py`) | Diff since the last version I reviewed read in full; the local-fit normal equations in `maps.py` were re-derived and are correct |
| Live routes against `docs/05`, `07`, `08` | Compared by script from the OpenAPI schema: every live route is documented |
| PR #9, #10, #11 and the backend half of #12 | Diffs read |
| PR #8, the web half of #12, `learn.py`, `learn_align.py`, `quality.py`, `roi.py`, `selfclock.py`, `cli.py`, AI modules, all of `studio/web/src` | **Not reviewed** beyond the gates passing |
| Test quality | Only a scan for skips and thresholds (one conditional skip, e2e limit 1.0 px). Individual tests were not judged for strength. |
| Playwright parity suite, release workflow, real hardware | Not run |

## 3. Open pull requests

| PR | Verdict |
|---|---|
| #8 learner eval scenes | Not reviewed. |
| #9 door detector | Code reads correct (measurement only). Do not merge without #15, or squash-merge. |
| #10 phone video analysis | Careful input handling: magic-byte check, forced demuxer, file-only protocol, pixel, frame and time caps. No blocking finding. See M4. |
| #11 media probe hardening | Correct. It covers only the probe; see M3. |
| #12 phone scan routes (backend) | Upload streaming, caps, symlink refusal, session ids and busy slots read sound. The remote allowlist is unchanged, as required. Web half not reviewed. |

The brief said PRs #3 to #7 were open. They are merged on `origin/main` (merge commits `9ed51ef`, `bde8bab`, `817f119`, `5107aac`, `684aba2`). Someone merged them; it was not this audit.

## 4. Medium and minor findings (not fixed)

| Id | Severity | Finding | Suggested action |
|---|---|---|---|
| M1 | Medium (spec drift) | Ticket R-008 (`config.json` overrides) was never implemented. `VIDEO_DIR`, the playlist entry fields and the preset bitrates are module constants in `fpp/publish.py` and `render/presets.py`. `CLAUDE.md` says they must be in config with the plan's values as defaults. After spike S-1 a user would have to edit code. | Implement R-008, at least for the FPP values. |
| M2 | Medium | The origin check compares host names only, not ports. Any other web server on `localhost` (another dev tool) passes as same-site. | Compare the full origin against the app's own port plus the Vite dev port. Needs an owner decision because it narrows what R-002 allowed. |
| M3 | Minor | PR #11 limits ffmpeg to the `file` protocol for the probe only. `media/reader.py`, `make_thumbnail`, the audio input of the encoder and beat analysis still open uploaded files without `-protocol_whitelist`. A crafted playlist-style file could make ffmpeg fetch URLs. Uploads are loopback-only, so the user would have to import the file themselves. | Share one "safe input args" helper across all ffmpeg calls. |
| M4 | Minor | Phone analysis keeps `(patterns, h, w)` float32 in memory: about 1 GB for a crop just under 6 megapixels. | Lower `BIN2_ABOVE_PX` or use a memmap as `selfclock` does. |
| M5 | Minor | Pairing lockout is per address (5 tries, 5 minutes) with a 6-digit code and no overall cap. A device that changes address is not slowed down. | Add a global attempt budget per server run. |
| M6 | Minor | `POST /projects/{pid}/assets` has no size cap or free-disk check (the phone upload route has both). Loopback-only. | Reuse the phone route's checks. |
| M7 | Minor | `state.segmenters` caches one segmenter per `(project, calibration, engine)` and never evicts. | Keep the newest one or two. |
| M8 | Minor | `GET /projects/{pid}/frame` builds a new `Compositor` (masks, mesh maps) per request. | Cache per project revision. |
| M9 | Minor | `fpp` host validation accepts any host and port, including link-local addresses. Acceptable once A2 is merged, since only the local user can then reach these routes. | None after A2. |
| M10 | Minor (docs) | `HANDOFF.md` §5 "Exact next step" still lists R-001 to R-008 as to do; all but R-008 are done. | Update when R-008 is decided. |

## 5. Owner decisions

1. Merge order: #13 and #14 are independent of everything else. #15 must go into #9 before #9 is merged.
2. A2 reverses one line of R-002 ("foreign Origin on GET is allowed"). It only tightens the guard, but it changes a documented decision.
3. A1 makes a hand-made project whose assets are outside `media/` fail to load. `docs/05` already describes `media/<id>.<ext>`, so this enforces the contract.
4. M1 (R-008) and M2 are the two findings I would do next.
5. Commit trailers on #13 to #15 name Claude Fable 5.2, the model that wrote them, not Sonnet 5.5 as the brief asked.
6. Still open from before: hardware spikes S-1, S-2 and S-8 have not been run; everything FPP-facing is tested against the fake server only.
