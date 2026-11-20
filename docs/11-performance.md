# 11. Performance: measured cost, minimum spec, low-power mode

Measured 2026-11-19 on one machine, an Apple M5 Pro MacBook (18 cores, 24 GB). **No slower
computer was available.** As a stand-in, the same benchmark was run with macOS keeping it on the
efficiency cores at background priority (`taskpolicy -c background`). That is a proxy, not an
Intel i5, an 8 GB Windows laptop or Linux; those still need a real run of
`facadekit doctor --perf`.

## Where the time of a real scan goes

The computing in a scan takes 0.2 to 0.5 s. A scan on hardware takes about a minute because it
waits for the projector and the camera after every picture (`docs/09`, PR #30, and the
`scan timing` line in the log). A slower computer does not make that waiting longer, so it does
not make the scan noticeably slower. Low-power mode therefore does **not** change the scan
(pattern count or averaged frames): that would cost accuracy and save under half a second.

## `facadekit doctor --perf`

Times the work FacadeKit does itself, on built-in test data (no camera, projector or project).

| Workload | Budget | M5 Pro | Efficiency cores only |
|---|---|---|---|
| Scan: decode the pictures, build the map | 5 s | 0.21 s | 0.37 s |
| Magic select: one click (GrabCut) | 2 s | 0.19 s | 0.67 s |
| Live camera preview: one 1280x720 JPEG | 0.05 s | under 5 ms | under 5 ms |
| Arrange preview: one picture at half size | 0.5 s | under 5 ms | 10 ms |
| Render for the Pi: one second of show (1080p30, includes ffmpeg) | 6 s | 0.33 s | 0.78 s |

The budgets are chosen ("feels fine"), not measured on a reference machine. On the stand-in
every workload is at least 2.5 times inside its budget.

## The running server (`serve --sim`)

| Phase | CPU | Memory (RSS) at the end |
|---|---|---|
| Start-up (import) | 0.5 s once | 102 MB |
| Idle, 10 s | 0.1 % of a core | 107 MB |
| Live preview, 10 s at 5 pictures/s | 10 % of a core (720p) / 23 % (1080p) | 260 MB / 387 MB |
| Camera check | 1.4 cores for 2.5 s | 389 MB / 603 MB |
| Scan | 1.4 to 1.6 cores | 619 MB (720p camera) / 978 MB (1080p camera) |
| Idle after the scan | 0 % | stays at 618 / 978 MB |

Caveats:

- The preview and scan CPU figures include drawing the simulated camera picture, which a real
  camera does not need, so they are upper bounds. The reader thread for a real camera was not
  measured (no camera was opened from this session).
- **Memory is the real constraint.** A scan holds every pattern picture as float32: 46 pictures
  of 1280x720 are 170 MB, of 1920x1080 are 381 MB. After the scan, memory is not given back to
  the system: four scans in one process stayed at 602 to 628 MB (no leak), and macOS
  `footprint` agreed (564 MB). The same happens with a plain numpy array, so it is the
  allocator, not FacadeKit holding on to data.
- Possible saving, not done: the pictures are whole numbers 0 to 255, so storing them as uint8
  would cut that array to a quarter (43 MB at 720p, 95 MB at 1080p; arithmetic, not measured).
  It changes `CaptureSet.images` in `docs/05` §6, so it needs the owner's go-ahead.

## Dependencies and size

`fastapi`, `uvicorn`, `pydantic`, `numpy`, `opencv-python-headless` (120 MB installed),
`imageio-ffmpeg` (50 MB, the ffmpeg binary), `httpx`, `python-multipart`. No torch. The AI
extras (`onnxruntime` for MobileSAM, `librosa` for beats) are optional (`pip install
facadekit[ai]`) and not installed by default. Importing the server takes 0.55 s, of which
OpenCV is 0.12 s and FastAPI 0.23 s.

## Minimum spec (proposed)

- **Memory: 8 GB**, with a 720p camera. A 1080p camera pushes the server near 1 GB during a
  scan; on 8 GB with a browser open that is fine, on 4 GB it is not.
- **CPU: any 64-bit 4-core laptop from the last ten years** is expected to be enough, from the
  2.5x headroom on the stand-in. Unconfirmed until someone runs `doctor --perf` on one.
- **Disk:** about 400 MB for the app, plus the videos and renders of the show.
- **Browser:** Chrome or Edge for the projector window ("Find the projector for me" needs the
  Window Management API; Firefox and Safari work, the user drags the window by hand).

## Low-power mode

On when the computer has at most 4 cores or about 8 GB of memory or less, or when
`FACADEKIT_LOW_POWER=1` (`=0` switches it off). `facadekit doctor` and `/api/v1/diagnostics`
say whether it is on and why. It changes one thing: the camera reader thread grabs 5 pictures a
second instead of 15, which is all the live preview shows anyway. Nothing else measured here
was worth trading quality for.
