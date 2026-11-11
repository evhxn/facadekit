# Packaging FacadeKit installers

One-folder builds made with PyInstaller: a zip with `FacadeKit/FacadeKit.exe` (Windows) and a zipped
`FacadeKit.app` (macOS, bundle id `org.facadekit.studio`). No Python needed on the user's machine.

## Build locally

```sh
cd studio/web && pnpm install && pnpm build          # writes studio/backend/src/facadekit/webdist
cd ../backend
uv sync --python 3.13
uv pip install onnxruntime pyinstaller
uv run --no-sync pyinstaller ../../packaging/facadekit.spec --noconfirm --distpath ../../dist --workpath ../../build
uv run --no-sync python ../../packaging/smoke_test.py ../../dist/FacadeKit/FacadeKit   # macOS: ../../dist/FacadeKit.app/Contents/MacOS/FacadeKit
```

`build/` and `dist/` are git-ignored. `librosa`/`numba` are deliberately excluded (large, fragile to
freeze): beat detection needs `pipx install "facadekit[ai]"`. The app reports `AI_UNAVAILABLE` without it.
AI model files are downloaded at run time, never bundled.

## How releases are produced

Pushing a tag `v*` runs `.github/workflows/release.yml`: build the web UI, then freeze on
`windows-latest`, `macos-14` (arm64) and `macos-15-intel` (x64; GitHub retired `macos-13`), smoke-test each (health check, `/` serves the
UI, `sim e2e` exits 0), zip, and attach the zips, wheel, sdist and `SHA256SUMS.txt` to a GitHub Release.
CI also generates `THIRD_PARTY_NOTICES.txt` (`pip-licenses`); `LICENSE` is bundled too. FacadeKit is
GPL-3.0-or-later and the bundled ffmpeg is a GPL build.

### Release workflow status

The release workflow has **not been run yet**: no release has been produced and no test run exists.
Its `workflow_dispatch` trigger was added under R-009 so it can be dry-run without pushing a tag.

To dry-run it from the Actions tab:

1. Open the repository on GitHub, go to **Actions**, choose **Release** in the left sidebar.
2. Click **Run workflow**, pick a **branch** (not a tag) in the dropdown, and start it.
3. Watch the `web`, `build` (three OSes) and `release` jobs. The `build` jobs exercise the freeze and
   smoke test. The `release` job only publishes when the ref is a tag, so on a branch it is expected to
   stop at the publish step; that is the dry run working as intended.
4. Record the outcome here (run link, date, which jobs passed) once it has been run.

Do not dispatch it against a `v*` tag: that would publish a real GitHub Release.

## Known warnings (builds are unsigned)

- Windows SmartScreen: click "More info", then "Run anyway". Defender can be slow on first run.
- macOS Gatekeeper: right-click `FacadeKit.app`, choose Open, then Open again. The first camera use
  asks for permission (`NSCameraUsageDescription` is set).

## Where data lives

Projects and settings are stored in `~/FacadeKit`. The server starts on port 8137, or the next free
port up to 8147; the console window prints the URL. `--port 0` picks any free port.
