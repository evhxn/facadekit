# 05 – Interfaces

Working name: **FacadeKit**. These are the exact contracts between modules. Builders must not invent or rename anything here. If something is missing or wrong, record it in `tasks/BLOCKERS.md`.

Conventions (from `docs/04-repo-structure.md`): colour images are BGR `uint8` `(H, W, 3)`; sizes are `(width, height)`; points are `(x, y)` pixels with origin top-left; times are integer milliseconds; expected failures raise `FkError(code, message)`.

Tags: **(MVP)** needed for v0, **(v1)** added later. A v1 field is optional and defaults to `null`/empty so MVP files stay valid.

---

## 1. IDs

`ids.new_id(prefix: str) -> str` returns `f"{prefix}_{8 lowercase hex chars}"`. Prefixes: `srf` surface, `ast` asset, `clp` clip, `trk` track, `cal` calibration, `rnd` render, `job` job. All ids match `^[a-z0-9_-]{1,40}$`. The reserved surface id `canvas` means the full projector frame and is never stored in `surfaces`.

Project id = folder name without `.fkproj`, matching `^[a-z0-9][a-z0-9_-]{0,39}$`. It is derived from the project name by lower-casing, replacing every run of other characters with `-`, trimming `-`, and appending `-2`, `-3`… if taken.

## 2. Project folder

```
<data_dir>/projects/<project_id>.fkproj/
├── project.json
├── media/<asset_id>.<ext>            imported files, original extension lower-cased
├── media/<asset_id>.thumb.jpg        320 px wide thumbnail (video: frame at 1 s or middle; image: itself; audio: none)
├── calibration/<calibration_id>/
│   ├── calibration.json
│   ├── proj_view.png                 projector-space photo, W×H BGR
│   ├── p2c.npy                       float32 (H, W, 2) projector→camera map (scan mode only)
│   └── measured_mask.png             8-bit, 255 where measured, W×H (scan mode only)
└── renders/<render_id>.mp4
```

## 3. `project.json` (MVP)

Pydantic models live in `facadekit/models/project.py`. All models use `model_config = ConfigDict(extra="forbid")`.

```python
Point = tuple[float, float]                      # (x, y) projector pixels

class ProjectorSpec(BaseModel):
    width: int = 1920                            # 160..4096
    height: int = 1080                           # 90..2160
    fps: int = 30                                # one of 24, 25, 30, 50, 60

class Mask(BaseModel):                           # (v1 UI; renderer supports it in MVP)
    outer: list[Point]                           # >= 3 points
    holes: list[list[Point]] = []

class Mesh(BaseModel):                           # (v1)
    cols: int                                    # cells, 1..16
    rows: int                                    # cells, 1..16
    offsets: list[Point]                         # (cols+1)*(rows+1) offsets in projector px, row-major

class Surface(BaseModel):
    id: str
    name: str
    quad: tuple[Point, Point, Point, Point]      # order: top-left, top-right, bottom-right, bottom-left as seen on the house
    mask: Mask | None = None
    feather_px: float = 0.0                      # 0..200
    aspect: float                                # real-world width / height, > 0
    mesh: Mesh | None = None

class Asset(BaseModel):
    id: str
    kind: Literal["video", "image", "audio"]
    filename: str                                # relative to the project folder, e.g. "media/ast_1a2b3c4d.mp4"
    original_name: str
    width: int | None                            # null for audio
    height: int | None
    duration_ms: int | None                      # null for image
    fps: float | None                            # video only
    has_audio: bool = False
    has_alpha: bool = False                      # true only for images with an alpha channel in MVP

class Generator(BaseModel):                      # (v1)
    type: Literal["solid", "wash", "outline", "strobe"]
    params: dict[str, float | str] = {}

class Clip(BaseModel):
    id: str
    asset_id: str | None                         # exactly one of asset_id / generator is set
    generator: Generator | None = None
    surface_id: str                              # a surface id or "canvas"
    start_ms: int                                # >= 0
    duration_ms: int                             # > 0
    in_offset_ms: int = 0                        # >= 0, start position inside the source
    loop: bool = True
    fit: Literal["fill", "fit", "stretch"] = "fill"
    opacity: float = 1.0                         # 0..1
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    blend: Literal["normal", "add", "screen"] = "normal"

class Track(BaseModel):
    id: str
    name: str
    clips: list[Clip] = []                       # clips on one track must not overlap in time

class Timeline(BaseModel):
    duration_ms: int = 60000                     # > 0
    audio_asset_id: str | None = None
    tracks: list[Track] = []                     # index 0 is the bottom layer

class Marker(BaseModel):                         # (v1)
    t_ms: int
    kind: Literal["beat", "downbeat", "onset", "user"]

class FppTarget(BaseModel):
    host: str | None = None                      # hostname or IP, no scheme
    playlist_name: str | None = None

class Project(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    name: str
    revision: int = 0
    projector: ProjectorSpec = ProjectorSpec()
    calibration_id: str | None = None
    surfaces: list[Surface] = []
    assets: list[Asset] = []
    timeline: Timeline = Timeline()
    markers: list[Marker] = []
    fpp: FppTarget = FppTarget()
```

Cross-field validation (`Project.model_validator`, error code `INVALID_PROJECT`):

1. All ids unique within their list.
2. Every `clip.surface_id` is `"canvas"` or an existing surface id.
3. Every `clip.asset_id` exists and is not an audio asset; `timeline.audio_asset_id`, if set, is an audio asset or a video asset with `has_audio`.
4. Exactly one of `clip.asset_id` and `clip.generator` is non-null.
5. Clips on the same track do not overlap: sorted by `start_ms`, each `start_ms >= previous.start_ms + previous.duration_ms`.

Example:

```json
{
  "schema_version": 1, "id": "garage-2026", "name": "Garage 2026", "revision": 3,
  "projector": {"width": 1920, "height": 1080, "fps": 30},
  "calibration_id": "cal_0a1b2c3d",
  "surfaces": [{"id": "srf_11111111", "name": "Garage door",
    "quad": [[512.0, 610.5], [1320.2, 598.0], [1335.0, 1012.4], [498.7, 1020.0]],
    "mask": null, "feather_px": 4.0, "aspect": 2.29, "mesh": null}],
  "assets": [{"id": "ast_22222222", "kind": "video", "filename": "media/ast_22222222.mp4",
    "original_name": "ghosts.mp4", "width": 1920, "height": 1080, "duration_ms": 45000,
    "fps": 30.0, "has_audio": true, "has_alpha": false}],
  "timeline": {"duration_ms": 60000, "audio_asset_id": null, "tracks": [{"id": "trk_33333333",
    "name": "Track 1", "clips": [{"id": "clp_44444444", "asset_id": "ast_22222222", "generator": null,
    "surface_id": "srf_11111111", "start_ms": 0, "duration_ms": 60000, "in_offset_ms": 0, "loop": true,
    "fit": "fill", "opacity": 1.0, "fade_in_ms": 500, "fade_out_ms": 500, "blend": "normal"}]}]},
  "markers": [], "fpp": {"host": "fpp.local", "playlist_name": null}
}
```

## 4. `calibration.json` (MVP)

`facadekit/models/calibration.py`:

```python
class CalibSettings(BaseModel):
    skip_lsb: int = 2
    settle_ms: int = 300
    discard_frames: int = 5
    average_frames: int = 3

class CalibQuality(BaseModel):
    coverage: float                 # 0..1 fraction of projector grid cells measured
    planarity: float                # 0..1 RANSAC inlier fraction of the global homography
    median_contrast: float          # gray levels
    lit_fraction: float             # fraction of camera pixels lit by the projector

class Calibration(BaseModel):
    id: str
    created_at: str                 # ISO 8601 UTC, e.g. "2026-11-09T21:14:03Z"
    mode: Literal["scan", "manual"]
    projector_width: int
    projector_height: int
    camera_id: str | None           # null in manual mode
    camera_width: int | None
    camera_height: int | None
    settings: CalibSettings | None  # null in manual mode
    quality: CalibQuality | None    # null in manual mode
    h_c2p: list[list[float]] | None # 3x3 camera→projector, null if not computed
    warnings: list[str] = []        # warning codes, see §10
```

Manual mode writes only `calibration.json` and a `proj_view.png` that is a dark gray (BGR 40,40,40) image with a light grid line every 120 px and the pixel coordinate printed at each crossing.

## 5. Jobs

`facadekit/models/jobs.py`:

```python
JobKind = Literal["calibration", "render", "publish", "analyze"]
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]

class JobError(BaseModel):
    code: str
    message: str

class Job(BaseModel):
    id: str
    kind: JobKind
    state: JobState
    progress: float = 0.0           # 0..1
    message: str = ""
    error: JobError | None = None
    result: dict[str, Any] | None = None
    created_at: str                 # ISO 8601 UTC
```

`facadekit/jobs/runner.py`:

```python
JobFn = Callable[[Callable[[float, str], None], Callable[[], bool]], dict[str, Any]]
#               progress(fraction, message)      cancel() -> bool        returns result dict

class JobRunner:
    def __init__(self, on_update: Callable[[Job], None] | None = None) -> None: ...
    def submit(self, kind: JobKind, fn: JobFn) -> Job: ...      # returns immediately, state "queued"
    def get(self, job_id: str) -> Job: ...                      # FkError NOT_FOUND
    def cancel(self, job_id: str) -> Job: ...                   # sets the cancel flag
    def shutdown(self) -> None: ...
```

One worker thread runs jobs in submission order. `FkError` raised by `fn` becomes `state="failed"` with that code; `FkError("CANCELLED")` becomes `state="cancelled"`; any other exception becomes `failed` with code `INTERNAL`. `on_update` is called (from the worker thread) on every state or progress change, at most 10 times per second for progress.

Job `result` shapes: calibration `{"calibration_id": str}`; render `{"render_id": str, "filename": str, "size_bytes": int, "duration_ms": int}`; publish `{"media_name": str, "playlist_name": str, "started": bool}`; analyze `{"markers": int}`.

## 6. Python module contracts

### 6.1 `errors.py`, `config.py`

```python
class FkError(Exception):
    def __init__(self, code: str, message: str) -> None: ...
    code: str
    message: str

def data_dir() -> Path          # env FACADEKIT_DATA, else ~/FacadeKit ; created if missing
def projects_dir() -> Path      # data_dir()/projects
def models_dir() -> Path        # data_dir()/models
```

### 6.2 `store/project_store.py`

```python
class ProjectSummary(BaseModel):
    id: str
    name: str
    modified_at: str            # ISO 8601 UTC mtime of project.json

class ProjectStore:
    def __init__(self, root: Path) -> None: ...                       # root = projects_dir()
    def list(self) -> list[ProjectSummary]: ...                       # newest first
    def create(self, name: str, projector: ProjectorSpec) -> Project: ...
    def project_dir(self, project_id: str) -> Path: ...               # FkError NOT_FOUND
    def load(self, project_id: str) -> Project: ...                   # FkError NOT_FOUND / INVALID_PROJECT
    def save(self, project: Project) -> Project: ...                  # see below
    def delete(self, project_id: str) -> None: ...

def load_project_dir(path: Path) -> Project        # load from an explicit folder (CLI use)
def save_project_dir(path: Path, project: Project) -> None   # atomic write, no revision check
```

`save` requires `project.revision == stored.revision`, else `FkError("REVISION_CONFLICT")`. It writes atomically (temp file + `os.replace`) with `revision + 1` and returns the saved project.

### 6.3 `geom/homography.py`, `geom/mask.py`

```python
def quad_homography(src_w: float, src_h: float, quad: Sequence[Point]) -> np.ndarray
    # 3x3 float64 mapping (0,0),(src_w,0),(src_w,src_h),(0,src_h) -> quad[0..3]; uses cv2.getPerspectiveTransform
def apply_h(h: np.ndarray, pts: np.ndarray) -> np.ndarray        # (N,2) -> (N,2), perspective divide
def estimate_aspect(quad: Sequence[Point]) -> float
    # (|TL-TR| + |BL-BR|) / (|TL-BL| + |TR-BR|)
def fit_rects(src_w: float, src_h: float, surface_aspect: float, fit: str
              ) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]
    # returns (src_rect, dst_unit_rect), each (x0, y0, x1, y1).
    # src_rect is in source pixels; dst_unit_rect is in the surface unit square [0,1]x[0,1].
    #   stretch: src = (0,0,src_w,src_h); dst = (0,0,1,1)
    #   fill:    dst = (0,0,1,1); src = largest centred sub-rect of the source with aspect == surface_aspect
    #   fit:     src = whole source; dst = largest centred sub-rect of the unit square such that
    #            (dst_width * surface_aspect) / dst_height == src_w / src_h
def clip_homography(src_w: float, src_h: float, surface: Surface, fit: str) -> tuple[np.ndarray, tuple[float,float,float,float]]
    # returns (H, src_rect): H maps source pixel coordinates to projector pixels for this clip.
    # H = quad_homography(1, 1, surface.quad) @ M, where M maps src_rect corners to dst_unit_rect corners (affine).

def rasterize_mask(width: int, height: int, outer: Sequence[Point],
                   holes: Sequence[Sequence[Point]] = (), feather_px: float = 0.0) -> np.ndarray
    # float32 (height, width) in 0..1. cv2.fillPoly with LINE_AA on a uint8 canvas (holes filled with 0),
    # then if feather_px > 0: cv2.GaussianBlur with sigma = feather_px / 2.
```

For the reserved surface `canvas`, callers build `Surface(id="canvas", name="Canvas", quad=((0,0),(W,0),(W,H),(0,H)), aspect=W/H)` via `canvas_surface(projector: ProjectorSpec) -> Surface` in `geom/homography.py`.

### 6.4 `devices/base.py`

```python
class Camera(Protocol):
    @property
    def resolution(self) -> tuple[int, int]: ...          # (width, height); valid after open()
    def open(self) -> None: ...                            # FkError CAMERA_OPEN_FAILED
    def close(self) -> None: ...
    def read(self) -> np.ndarray: ...                      # newest frame, BGR uint8; FkError CAMERA_READ_FAILED
    def set_controls(self, *, auto_exposure: bool | None = None, exposure: float | None = None,
                     auto_wb: bool | None = None, autofocus: bool | None = None) -> dict[str, bool]: ...
        # best effort; returns {"auto_exposure": applied?, "exposure": …, "auto_wb": …, "autofocus": …}
        # only for the arguments that were not None. exposure is normalised 0..1 (0 darkest).

class ProjectorDisplay(Protocol):
    @property
    def size(self) -> tuple[int, int]: ...                 # (width, height) in physical pixels
    def show(self, image: np.ndarray) -> None: ...         # (H, W) gray or (H, W, 3) BGR uint8, exactly `size`;
                                                           # returns only after the image is on screen.
                                                           # FkError PROJECTOR_NOT_CONNECTED / PROJECTOR_TIMEOUT / BAD_IMAGE_SIZE
```

`devices/camera_opencv.py`:

```python
class CameraInfo(BaseModel):
    id: str            # "0", "1", … (OpenCV index) or "sim"
    name: str          # "Camera 0"
    width: int
    height: int

def list_cameras(max_index: int = 5) -> list[CameraInfo]
class OpenCvCamera:    # implements Camera
    def __init__(self, index: int, width: int = 1280, height: int = 720) -> None: ...
```

### 6.5 `sim/scene.py`, `sim/devices.py`

```python
@dataclass
class SimScene:
    world_size: tuple[int, int] = (1600, 900)             # the flat facade, in "world pixels"
    proj_size: tuple[int, int] = (1920, 1080)
    cam_size: tuple[int, int] = (1280, 720)
    proj_corners_world: tuple[Point, Point, Point, Point] = ((150, 80), (1480, 40), (1520, 860), (110, 820))
        # where projector frame corners (0,0),(W,0),(W,H),(0,H) land on the world plane
    world_corners_cam: tuple[Point, Point, Point, Point] = ((90, 60), (1200, 95), (1180, 660), (70, 640))
        # where world corners (0,0),(1600,0),(1600,900),(0,900) appear in the camera image
    features_world: dict[str, tuple[float, float, float, float]] = field(default_factory=lambda: {
        "garage_door": (500, 480, 1100, 800), "window_left": (250, 200, 420, 380),
        "window_right": (1180, 200, 1350, 380)})         # (x0, y0, x1, y1) rectangles on the world plane
    occluders_world: list[tuple[float, float, float, float]] = field(default_factory=list)   # block projector light
    ambient: float = 0.05           # ambient light level 0..1
    gain: float = 0.85              # projector light gain
    gamma: float = 2.2              # projector response
    blur_sigma: float = 1.0         # camera blur in camera pixels
    noise_sigma: float = 2.0        # camera noise in gray levels
    seed: int = 0

    @property
    def h_p2w(self) -> np.ndarray: ...     # projector px -> world
    @property
    def h_w2c(self) -> np.ndarray: ...     # world -> camera px
    @property
    def h_p2c(self) -> np.ndarray: ...     # h_w2c @ h_p2w
    def albedo(self) -> np.ndarray: ...    # (900,1600,3) BGR uint8 procedural facade: brick-red wall (60,70,150)
                                           # with darker mortar lines every 24 px, features drawn as lighter
                                           # rectangles (door (200,200,200), windows (90,60,40)) with dark frames
    def feature_quad_proj(self, name: str) -> list[Point]: ...   # ground-truth quad in projector px, order TL,TR,BR,BL
    def render_camera(self, proj_image: np.ndarray | None, rng: np.random.Generator) -> np.ndarray: ...

def default_scene() -> SimScene

class SimDisplay:   # implements ProjectorDisplay; size == scene.proj_size
    def __init__(self, scene: SimScene) -> None: ...
    current: np.ndarray | None      # last shown image (always stored as (H, W, 3) BGR)
class SimCamera:    # implements Camera; resolution == scene.cam_size
    def __init__(self, scene: SimScene, display: SimDisplay) -> None: ...
```

`render_camera` model, all in float 0..1:

```
light_world = warpPerspective((proj_image/255) ** gamma, h_p2w, world_size)        # zeros if proj_image is None
light_world[occluders] = 0
radiance = albedo/255 * (ambient + gain * light_world)
cam = warpPerspective(radiance, h_w2c, cam_size)                                    # black outside the wall
cam = GaussianBlur(cam, blur_sigma); cam = cam * 255 + normal(0, noise_sigma); clip to 0..255 uint8
```

`SimCamera.set_controls` returns `True` for every requested control and changes nothing.

### 6.6 `calib/*`

```python
# patterns.py
@dataclass(frozen=True)
class PatternSpec:
    width: int
    height: int
    skip_lsb: int = 2
@dataclass(frozen=True)
class PatternFrame:
    kind: Literal["black", "white", "pattern"]
    axis: Literal["x", "y"] | None = None
    bit: int | None = None            # bit index in the Gray code, 0 = least significant
    inverse: bool = False
def bits_for(n: int) -> int                       # ceil(log2(n)), bits_for(1920) == 11
def pattern_sequence(spec: PatternSpec) -> list[PatternFrame]
    # [black, white] + for axis in ("x","y"): for bit in range(bits-1, skip_lsb-1, -1): [pattern, inverse]
def render_pattern(spec: PatternSpec, frame: PatternFrame) -> np.ndarray   # uint8 (H, W), values 0 or 255
def gray_to_binary(g: np.ndarray) -> np.ndarray   # elementwise, integer arrays

# capture.py
@dataclass
class CaptureSet:
    spec: PatternSpec
    frames: list[PatternFrame]
    images: np.ndarray                # float32 (N, Hc, Wc) grayscale, same order as frames
    white_bgr: np.ndarray             # uint8 (Hc, Wc, 3) colour photo under the WHITE frame
    controls_applied: dict[str, bool] # result of the camera locking attempt
def precheck(display: ProjectorDisplay, camera: Camera, spec: PatternSpec, *,
             sleep: Callable[[float], None] = time.sleep) -> dict[str, float]
    # raises FkError RESOLUTION_MISMATCH / CAMERA_NO_PROJECTION / TOO_BRIGHT; returns {"lit_fraction", "median_contrast"}
def run_capture(display: ProjectorDisplay, camera: Camera, spec: PatternSpec, settings: CalibSettings, *,
                progress: Callable[[float, str], None] | None = None,
                cancel: Callable[[], bool] | None = None,
                sleep: Callable[[float], None] = time.sleep) -> CaptureSet

# decode.py
@dataclass
class DecodeResult:
    c2p: np.ndarray                   # float32 (Hc, Wc, 2); NaN where invalid; [...,0]=proj x, [...,1]=proj y
    valid: np.ndarray                 # bool (Hc, Wc)
    contrast: np.ndarray              # float32 (Hc, Wc) = white - black
    lit_fraction: float
    median_contrast: float
def decode(capture: CaptureSet, *, min_conf: float = 0.10, outlier_px: float = 8.0) -> DecodeResult

# maps.py
@dataclass
class ProjectorMaps:
    p2c: np.ndarray                   # float32 (H, W, 2): camera (x, y) for each projector pixel
    measured: np.ndarray              # bool (H, W)
    h_c2p: np.ndarray | None          # (3,3) float64
    coverage: float
    planarity: float
def build_maps(decoded: DecodeResult, spec: PatternSpec, grid_step: int = 8) -> ProjectorMaps
    # raises FkError SCAN_FAILED if fewer than 1000 valid camera pixels
def make_proj_view(white_bgr: np.ndarray, maps: ProjectorMaps) -> np.ndarray     # uint8 (H, W, 3)

# pipeline.py
def run_calibration(project_dir: Path, display: ProjectorDisplay, camera: Camera, camera_id: str,
                    settings: CalibSettings, *, progress=None, cancel=None,
                    sleep: Callable[[float], None] = time.sleep) -> Calibration
    # precheck -> run_capture -> decode -> build_maps -> make_proj_view -> write files (§2) -> return model
    # raises FkError SCAN_FAILED when coverage < 0.50; adds warning LOW_COVERAGE when coverage < 0.85,
    # NON_PLANAR when planarity < 0.5, CAMERA_CONTROLS_UNAVAILABLE when exposure lock was refused
def create_manual_calibration(project_dir: Path, projector: ProjectorSpec) -> Calibration
```

`run_calibration` does **not** modify `project.json`; the caller sets `project.calibration_id`.

### 6.7 `media/*`

```python
# ffmpeg.py
def ffmpeg_exe() -> str                                  # imageio_ffmpeg.get_ffmpeg_exe()
def run_ffmpeg(args: list[str], *, timeout: float | None = None) -> None   # FkError FFMPEG_FAILED with stderr tail

# probe.py
def probe(path: Path) -> Asset                           # id="", filename="", original_name=path.name; caller fills ids
    # kind by extension then by content: images (.png .jpg .jpeg .webp .gif .bmp) are read with cv2.imread(IMREAD_UNCHANGED);
    # audio (.mp3 .wav .m4a .flac .ogg .aac): duration parsed from `ffmpeg -i`; everything else is video via
    # imageio_ffmpeg.read_frames meta (size, fps, duration, audio_codec). FkError UNSUPPORTED_MEDIA.
def make_thumbnail(path: Path, kind: str, out_path: Path) -> None

# reader.py
class VideoReader:
    def __init__(self, path: Path, *, fps: int, width: int, height: int, start_ms: int = 0) -> None: ...
        # ffmpeg -ss <start> -i path -vf fps=<fps>,scale=<width>:<height> -pix_fmt bgr24 -f rawvideo -
    def read(self) -> np.ndarray | None: ...             # next frame (height, width, 3) or None at end
    def close(self) -> None: ...

class ClipSource:
    """Random-access-ish frame source for one asset, optimised for increasing times."""
    def __init__(self, asset: Asset, path: Path, *, fps: int, max_size: tuple[int, int]) -> None: ...
        # decodes at the asset's own size, but never larger than max_size (keeps aspect, never upscales)
    size: tuple[int, int]                                # decode size (width, height)
    def frame_at(self, source_ms: int) -> np.ndarray: ...
        # BGR (H, W, 3), or BGRA (H, W, 4) for images with alpha. For video: if source_ms is the next frame
        # or later within 2 s, read forward; otherwise reopen VideoReader at source_ms. Past the end: last frame.
    def close(self) -> None: ...
```

### 6.8 `render/*`

```python
# compositor.py
class Compositor:
    def __init__(self, project: Project, project_dir: Path, *, scale: float = 1.0) -> None: ...
        # output size = (round(W*scale), round(H*scale)); quads, masks and feather are scaled by `scale`
    size: tuple[int, int]
    def frame(self, t_ms: int) -> np.ndarray: ...        # BGR uint8; rules in docs/02-architecture.md §6.2
    def close(self) -> None: ...

# presets.py
@dataclass(frozen=True)
class Preset:
    id: str
    width: int | None          # None = projector width
    height: int | None
    video_args: list[str]
    audio_args: list[str]
PRESETS: dict[str, Preset]
#  "pi3-1080p30": Preset("pi3-1080p30", None, None,
#     ["-c:v","libx264","-profile:v","high","-level","4.0","-pix_fmt","yuv420p","-preset","medium","-crf","20",
#      "-maxrate","8M","-bufsize","16M","-g","30","-bf","2"], ["-c:a","aac","-b:a","192k","-ar","48000","-ac","2"])
#  "pi3-720p30":  same but width=1280, height=720, "-maxrate","5M","-bufsize","10M"
#  "preview-540p": width=960, height=540, ["-c:v","libx264","-pix_fmt","yuv420p","-preset","ultrafast","-crf","28"], same audio
# A preset whose size differs from the projector keeps the projector aspect: height = round(width * H / W / 2) * 2.

# encoder.py
class Encoder:
    def __init__(self, out_path: Path, *, width: int, height: int, fps: int, preset: Preset,
                 audio_path: Path | None, audio_offset_ms: int = 0, duration_ms: int) -> None: ...
        # ffmpeg -y -f rawvideo -pix_fmt bgr24 -s WxH -r fps -i -  [-ss off -i audio | -f lavfi -i anullsrc=r=48000:cl=stereo]
        #        -map 0:v -map 1:a <video_args> <audio_args> -t <duration> -movflags +faststart out_path
    def write(self, frame: np.ndarray) -> None: ...
    def close(self) -> None: ...                          # waits for ffmpeg; FkError FFMPEG_FAILED

# pipeline.py
def render_project(project_dir: Path, preset_id: str, out_path: Path, *, include_audio: bool = True,
                   progress=None, cancel=None) -> dict[str, Any]
    # returns {"filename": out_path.name, "size_bytes": int, "duration_ms": int, "frames": int}
    # frame count = ceil(timeline.duration_ms * fps / 1000); FkError UNKNOWN_PRESET

# outline.py
def outline_frame(project: Project, *, selected_id: str | None = None) -> np.ndarray
    # black W×H BGR frame; each surface quad drawn as a 3 px white polyline (selected: yellow (0,255,255)),
    # corner TL marked with a filled 8 px circle, surface name printed at the quad centre
def render_alignment_check(project_dir: Path, out_path: Path, *, seconds: int = 20, preset_id: str = "pi3-1080p30") -> dict[str, Any]
    # outline frame, blinking at 1 Hz between full outlines and outlines + 10 % gray fill; silent audio
```

### 6.9 `fpp/*`

```python
# client.py
@dataclass
class FppVersion:
    version: str          # e.g. "10.4"
    major: int
    minor: int
    branch: str

class FppClient:
    def __init__(self, host: str, *, timeout: float = 10.0) -> None: ...   # base URL http://{host}
    def version(self) -> FppVersion: ...                 # FkError FPP_UNREACHABLE / FPP_BAD_RESPONSE
    def system_info(self) -> dict[str, Any]: ...
    def list_files(self, dir_name: str) -> list[str]: ...
    def upload(self, dir_name: str, local_path: Path, remote_name: str,
               progress: Callable[[float, str], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> None: ...     # FkError FPP_UPLOAD_FAILED
    def media_duration(self, name: str) -> float | None: ...              # seconds
    def put_playlist(self, name: str, entries: list[dict[str, Any]]) -> None: ...
    def start_playlist(self, name: str) -> None: ...
    def stop(self) -> None: ...
    def status(self) -> dict[str, Any]: ...
    def close(self) -> None: ...

# publish.py
def sanitize_name(name: str) -> str        # keep [A-Za-z0-9._-], others -> "_", prefix "fk_" if missing
def media_entry(media_name: str, sequence_name: str | None = None) -> dict[str, Any]
def publish_render(host: str, local_path: Path, *, media_name: str, playlist_name: str, start: bool,
                   sequence_name: str | None = None, progress=None, cancel=None) -> dict[str, Any]
    # version (major >= 10 else FkError FPP_TOO_OLD) -> upload -> verify listed -> put_playlist -> optional start
    # returns {"media_name", "playlist_name", "started"}

# fake.py
def create_fake_fpp(state_dir: Path, *, version: str = "10.4") -> FastAPI     # implements §7 with files on disk
```

### 6.10 `ai/*` (v1)

```python
# beats.py
def analyze_beats(audio_path: Path) -> list[Marker]          # kinds "beat" and "onset"; needs extra "ai"; FkError AI_UNAVAILABLE
# segment.py
@dataclass
class SegmentResult:
    outer: list[Point]            # simplified polygon, projector px
    quad: list[Point]             # fitted quad TL,TR,BR,BL
    score: float
class Segmenter(Protocol):
    def set_image(self, bgr: np.ndarray) -> None: ...
    def segment(self, points: list[tuple[float, float, int]]) -> SegmentResult: ...   # (x, y, label) label 1=include 0=exclude
def get_segmenter(prefer: Literal["auto", "mobilesam", "grabcut"] = "auto") -> Segmenter
def mask_to_polygon_and_quad(mask: np.ndarray) -> tuple[list[Point], list[Point]]
# models.py
def ensure_model(name: str, progress=None) -> Path           # downloads to models_dir(), verifies SHA-256; FkError MODEL_DOWNLOAD_FAILED
```

## 7. FPP REST contract used by FacadeKit

Base URL `http://{host}`. Items tagged [A] are assumptions to be confirmed by spike S-1 (`docs/03-roadmap.md`); they are isolated in `fpp/client.py` and `fpp/publish.py`.

| Purpose | Request | Response used |
|---|---|---|
| Version | `GET /api/fppd/version` | JSON with `version` (string), `majorVersion` (int), `minorVersion` (int), `branch` |
| System info | `GET /api/system/info` | JSON; we display `Platform`, `Variant`, `HostName` if present [A field names] |
| List files | `GET /api/files/{DirName}` | JSON `{"status":"ok","files":[{"name":…,"sizeBytes":…}]}` [A wrapper; accept a bare list too] |
| Upload start | `POST /api/file/{DirName}` with empty body | 200; body ignored |
| Upload chunk | `PATCH /api/file/{DirName}` headers `Upload-Name: <name>`, `Upload-Offset: <byte offset>`, `Upload-Length: <total bytes>`, `Content-Type: application/offset+octet-stream` [A content type]; body = raw chunk (8 MiB) | 2xx per chunk |
| Media duration | `GET /api/media/{name}/duration` | JSON containing a number of seconds under key `{name}` → `duration`, or top-level `duration` [A; accept both] |
| Save playlist | `POST /api/playlist/{PlaylistName}` JSON body below | 2xx |
| Start playlist | `POST /api/playlist/{PlaylistName}/start` | 2xx |
| Stop | `POST /api/playlists/stop` | 2xx |
| Status | `GET /api/fppd/status` | JSON; we read `status_name`, `current_playlist.playlist`, `seconds_played` [A field names; treat all as optional] |

`DirName` for rendered videos is `videos` [A]. Playlist body:

```json
{
  "name": "FacadeKit - Garage 2026",
  "mainPlaylist": [
    {"type": "media", "enabled": 1, "playOnce": 0, "mediaName": "fk_garage-2026.mp4", "videoOut": "--Default--"}
  ],
  "playlistInfo": {"total_duration": 60.0, "total_items": 1}
}
```

With a paired sequence (v1) the entry is `{"type": "both", "enabled": 1, "playOnce": 0, "sequenceName": "song.fseq", "mediaName": "fk_garage-2026.mp4", "videoOut": "--Default--"}` [A].

## 8. Studio backend HTTP API (MVP unless tagged)

Prefix `/api/v1`. JSON everywhere unless stated. Errors: HTTP status + body `{"error": {"code": "<CODE>", "message": "<text>"}}`. Mapping: `NOT_FOUND`→404, `REVISION_CONFLICT` and `ASSET_IN_USE`→409, `INVALID_PROJECT` / `BAD_REQUEST` / `UNSUPPORTED_MEDIA` / `UNKNOWN_PRESET`→422, `PROJECTOR_NOT_CONNECTED`→409, everything else→500 (`errors.http_status`).

| Method and path | Request | Response |
|---|---|---|
| `GET /health` | – | `{"status":"ok","version":"<pkg version>","sim":bool}` |
| `GET /projects` | – | `ProjectSummary[]` |
| `POST /projects` | `{"name": str, "projector": ProjectorSpec}` | 201 `Project` |
| `GET /projects/{pid}` | – | `Project` |
| `PUT /projects/{pid}` | `Project` (with the revision it was loaded at) | `Project` (revision + 1) |
| `DELETE /projects/{pid}` | – | 204 |
| `POST /projects/{pid}/assets` | multipart field `file` | 201 `{"asset": Asset, "project": Project}` (asset appended, revision bumped) |
| `GET /projects/{pid}/assets/{aid}/file` | supports HTTP Range | file bytes |
| `GET /projects/{pid}/assets/{aid}/thumb` | – | `image/jpeg` (404 for audio) |
| `DELETE /projects/{pid}/assets/{aid}` | – | `Project`; 409 `ASSET_IN_USE` if any clip or the timeline audio references it |
| `GET /devices/cameras` | – | `CameraInfo[]` (in `--sim` mode exactly one entry with id `"sim"`) |
| `GET /devices/cameras/{cid}/snapshot.jpg` | – | `image/jpeg` current frame |
| `POST /devices/cameras/{cid}/controls` | `{"auto_exposure"?: bool, "exposure"?: number, "auto_wb"?: bool, "autofocus"?: bool}` | `{"applied": {…: bool}}` |
| `GET /projector/status` | – | `ProjectorStatus` (below) |
| `POST /projector/show` | `{"mode": "blank"\|"white"\|"grid"\|"outlines"\|"compositor", "project_id"?: str, "selected_id"?: str}` | `ProjectorStatus`; 409 if not connected |
| `POST /projector/transport` | `{"project_id": str, "playing": bool, "t_ms": int, "revision": int}` | 204 (relayed to the projector page) |
| `POST /projects/{pid}/calibrations` | `{"mode": "scan"\|"manual", "camera_id"?: str, "settings"?: CalibSettings}` | 202 `{"job_id": str}`; on success the server sets `project.calibration_id` and bumps the revision |
| `GET /projects/{pid}/calibrations/{cid}` | – | `Calibration` |
| `GET /projects/{pid}/calibrations/{cid}/proj_view.png` | – | `image/png` |
| `GET /projects/{pid}/calibrations/{cid}/measured_mask.png` | – | `image/png` (404 in manual mode) |
| `GET /projects/{pid}/frame?t_ms=<int>&scale=<0.1..1>` | – | `image/jpeg` authoritative composited frame |
| `POST /projects/{pid}/renders` | `{"preset": str, "include_audio": bool, "kind": "show"\|"alignment_check"}` | 202 `{"job_id": str}` |
| `GET /projects/{pid}/renders` | – | `[{"render_id": str, "filename": str, "size_bytes": int, "modified_at": str}]` |
| `GET /projects/{pid}/renders/{rid}/file` | Range | `video/mp4` |
| `POST /fpp/probe` | `{"host": str}` | `{"reachable": bool, "version": str\|null, "major": int\|null, "ok": bool, "problems": str[]}` (never an HTTP error for an unreachable host) |
| `GET /fpp/status?host=` | – | `{"status_name": str\|null, "current_playlist": str\|null, "seconds_played": number\|null}` |
| `POST /projects/{pid}/publish` | `{"host": str, "render_id": str, "playlist_name"?: str, "start": bool, "sequence_name"?: str}` | 202 `{"job_id": str}` |
| `GET /jobs/{jid}` | – | `Job` |
| `POST /jobs/{jid}/cancel` | – | `Job` |
| `POST /projects/{pid}/assets/{aid}/analyze` (v1) | – | 202 `{"job_id"}`; job appends markers to the project |
| `POST /projects/{pid}/segment` (v1) | `{"points": [{"x": number, "y": number, "label": 0\|1}]}` | `{"outer": Point[], "quad": Point[], "score": number}` |

```python
class ProjectorStatus(BaseModel):
    connected: bool
    width: int | None         # physical pixels reported by the page: round(innerWidth * dpr)
    height: int | None
    dpr: float | None
    mode: str                 # last mode shown: "blank" | "white" | "grid" | "outlines" | "compositor" | "image"
```

Default publish names: media `fk_<project_id>.mp4` (alignment check: `fk_<project_id>_align.mp4`), playlist `FacadeKit - <project name>`.

Static files: any path not under `/api` or `/ws` serves the web UI from `webdist/` with `index.html` fallback (single-page app).

## 9. WebSockets

### 9.1 `/ws/events` (UI)

Server → client JSON messages only:

```json
{"type": "job", "job": { …Job… }}
{"type": "projector", "status": { …ProjectorStatus… }}
{"type": "project", "project_id": "garage-2026", "revision": 4}
```

### 9.2 `/ws/projector` (projector page)

Only one projector page may be connected; a second connection replaces the first (the first receives close code 4000).

Client → server:

```json
{"type": "hello", "width": 1920, "height": 1080, "dpr": 1}      // CSS innerWidth/innerHeight and devicePixelRatio
{"type": "ack", "seq": 17}                                       // sent after the content for seq has been on screen for 2 animation frames
```

Server → client (every message has an increasing integer `seq`):

```json
{"type": "image", "seq": 17, "url": "/api/v1/projector/image/17.png"}     // draw 1:1, no smoothing
{"type": "blank", "seq": 18}
{"type": "white", "seq": 19}
{"type": "grid", "seq": 20}                                               // 1 px white lines every 120 px + border
{"type": "outlines", "seq": 21, "width": 1920, "height": 1080,
 "surfaces": [{"id": "srf_1", "name": "Garage door", "quad": [[…],[…],[…],[…]], "selected": true}]}
{"type": "compositor", "seq": 22, "project_id": "garage-2026"}            // page loads the project and runs the WebGL compositor
{"type": "transport", "seq": 23, "playing": true, "t_ms": 1200, "revision": 4}
```

`GET /api/v1/projector/image/{seq}.png` serves the PNG for an `image` message (kept in memory, only the latest 4).

`devices/display_ws.py::WsDisplay` implements `ProjectorDisplay` on top of this: `show()` encodes PNG, sends `image`, blocks until the matching `ack` (timeout 5 s → `FkError("PROJECTOR_TIMEOUT")`). It is called from job worker threads while the socket lives on the asyncio loop, so it uses `asyncio.run_coroutine_threadsafe` to send and a `threading.Event` per `seq` to wait.

## 10. Error and warning codes

Errors (`FkError.code`): `NOT_FOUND`, `BAD_REQUEST`, `INVALID_PROJECT`, `REVISION_CONFLICT`, `ASSET_IN_USE`, `UNSUPPORTED_MEDIA`, `FFMPEG_FAILED`, `UNKNOWN_PRESET`, `CAMERA_OPEN_FAILED`, `CAMERA_READ_FAILED`, `PROJECTOR_NOT_CONNECTED`, `PROJECTOR_TIMEOUT`, `BAD_IMAGE_SIZE`, `RESOLUTION_MISMATCH`, `CAMERA_NO_PROJECTION`, `TOO_BRIGHT`, `SCAN_FAILED`, `FPP_UNREACHABLE`, `FPP_BAD_RESPONSE`, `FPP_TOO_OLD`, `FPP_UPLOAD_FAILED`, `CANCELLED`, `INTERNAL`, and (v1) `AI_UNAVAILABLE`, `MODEL_DOWNLOAD_FAILED`, `REALIGN_FAILED`.

Calibration warnings: `LOW_COVERAGE`, `NON_PLANAR`, `CAMERA_CONTROLS_UNAVAILABLE`.

User-facing text for each code lives in the web UI (`studio/web/src/api/errors.ts`), following the wording in `docs/02-architecture.md` §4.10.

## 11. CLI

Entry point `facadekit` (`facadekit.cli:main`). Exit codes: 0 success, 1 `FkError` (prints `CODE: message` to stderr), 2 usage error.

```
facadekit serve    [--host 127.0.0.1] [--port 8137] [--data-dir PATH] [--no-browser] [--sim]
facadekit new      PROJECT_DIR --name NAME [--width 1920] [--height 1080] [--fps 30]
facadekit calibrate PROJECT_DIR (--sim | --manual)                 # real hardware calibration needs the UI (projector window)
facadekit render   PROJECT_DIR [--preset pi3-1080p30] [--out PATH] [--no-audio] [--alignment-check]
facadekit publish  PROJECT_DIR --host HOST [--render PATH] [--playlist NAME] [--start]
facadekit doctor                                                    # prints versions, ffmpeg path, cameras found, data dir
facadekit sim e2e  [--workdir PATH]                                 # full simulated run; prints max corner error in px
```

`--sim` on `serve` replaces the camera list with one `sim` camera and makes the projector display a `SimDisplay` that is always "connected" at the default scene's size, so the whole UI can be exercised without hardware.

`PROJECT_DIR` is a path to a `.fkproj` folder. `render` default output: `PROJECT_DIR/renders/<new render id>.mp4`.

## 12. Web UI internal contracts

`api/types.ts` mirrors §3–5 and §8 one-to-one with the same field names. `gl/homography.ts` exports:

```ts
export type Pt = [number, number];
export type Mat3 = [number, number, number, number, number, number, number, number, number]; // row-major
export function quadHomography(srcW: number, srcH: number, quad: Pt[]): Mat3;   // same mapping as geom.quad_homography
export function applyH(h: Mat3, p: Pt): Pt;
export function multiply(a: Mat3, b: Mat3): Mat3;
export function estimateAspect(quad: Pt[]): number;
export function fitRects(srcW: number, srcH: number, surfaceAspect: number, fit: "fill" | "fit" | "stretch"):
  { src: [number, number, number, number]; dst: [number, number, number, number] };
```

`gl/compositor.ts` exports:

```ts
export interface FrameSource { texture(gl: WebGL2RenderingContext, sourceMs: number): WebGLTexture | null; width: number; height: number; }
export class PreviewCompositor {
  constructor(canvas: HTMLCanvasElement, opts: { assetUrl: (assetId: string) => string });
  setProject(project: Project): void;
  render(tMs: number): void;          // same rules as docs/02-architecture.md §6.2, feather approximated in the shader
  dispose(): void;
}
```

`state/projectStore.ts` exposes `{ project, load(id), update(mutator), saveNow(), dirty, saving, error }`. `update` applies an Immer-style mutator to a copy, marks dirty, and a 500 ms debounce `PUT`s the project. On `REVISION_CONFLICT` it reloads and surfaces the error.

## 13. Additions fixed while writing the tickets

These refine the sections above. Where a ticket and this document differ, this section and the ticket agree and take precedence.

| Where | Addition | Ticket |
|---|---|---|
| `ids.py` | `slugify(name) -> str`, `unique_slug(name, taken) -> str` | T-003 |
| `errors.py` | `HTTP_STATUS` dict and `http_status(code) -> int` | T-003 |
| `models/project.py` | `parse_project(data) -> Project` raising `FkError("INVALID_PROJECT")`; surface id `"canvas"` is rejected by the `Project` validator, not by `Surface` | T-004, T-006 |
| `models/jobs.py` | `utc_now_iso() -> str` | T-004 |
| `calib/pipeline.py` | `load_calibration(project_dir, calibration_id) -> Calibration`; calibration ids are validated against `^cal_[0-9a-f]{8}$` | T-014 |
| `media/ffmpeg.py` | `ffmpeg_info(path) -> str` (stderr of `ffmpeg -i`); there is no ffprobe in `imageio-ffmpeg` | T-015 |
| `render/presets.py` | `get_preset(id)`, `output_size(preset, projector_w, projector_h)` | T-018 |
| `render/encoder.py` | `Encoder.abort()` kills ffmpeg and removes the partial file | T-018 |
| `render/outline.py` | `outline_frame(project, *, selected_id=None, fill=0.0)` | T-019 |
| `fpp/publish.py` | `VIDEO_DIR = "videos"`, `probe_fpp(host) -> dict` | T-022 |
| `api/deps.py` | `AppState` gains `events: EventHub`, `projector: ProjectorHub`, `cameras: CameraManager`, simulator objects | T-026, T-027 |
| Renders on disk | Sidecar `renders/<render_id>.json` = `{"kind": "show"\|"alignment_check", "preset": str}` | T-028 |
| FPP routes | Hosts containing `/ ? # @` or whitespace are rejected with `BAD_REQUEST` | T-028 |
| Camera snapshot | Returns 409 `BAD_REQUEST` "Camera is busy scanning" while a scan job holds the camera | T-027 |
| Web | `api/errors.ts::userMessage`, `api/events.ts::waitForJob`, `newId(prefix)` in `api/types.ts`, `PreviewCompositor.render(tMs, playing)` | T-029, T-033 |
| v1 routes | `GET /fpp/sequences?host=` (T-114); `POST /projects/{pid}/realign` (T-112); calibration `mode: "scan_pi"` with `host` (T-113); `CalibSettings.settle_ms == 0` means automatic (T-115); `GET /remote/info`, `POST /remote/pair`, `serve --lan` (T-117); optional `grids` in `POST /projector/show` (T-111); `segment` response includes `engine` (T-106) | as listed |
| Scan memory | `CalibSettings.min_conf` (default 0.10), `Calibration.strategy`, `run_calibration(..., advisor=)`, calibration request field `learn` (now also honoured for `mode: "scan_pi"`; no new field, behaviour change only, flagged), `POST /projects/{pid}/calibrations/{cid}/feedback`, `GET`/`DELETE /learning/scans`; `build_maps` grid step defaults to `max(8, 2 × 2**skip_lsb)`. Full contract in `docs/07-scan-memory.md` | – |
| Re-align learning | `RealignResponse.warning: str \| null` (null until 5 applied re-aligns are logged), `AppState.align_memory`, `GET`/`DELETE /learning/realign`. Contract in `docs/07-scan-memory.md` | – |
| AI capabilities | `GET /api/v1/ai/capabilities` -> `{"segment_engines": ["grabcut"]` or `["mobilesam", "grabcut"]`, `"beats": bool}`. MobileSAM is listed only when both registry models are published and `onnxruntime` is importable; `beats` is true when `librosa` is importable. Checks use `importlib.util.find_spec`, so heavy modules are not imported | R-006 |
| Calibration quality | `CalibQuality` gains optional `px_per_proj_px: float \| null` (camera pixels per projector pixel), `snr: float \| null`, `verdict: "good"\|"marginal"\|"too_coarse" \| null`. Verdict reasons (`calib/quality.py`): `SMALL_IN_FRAME`, `BLURRY`, `NOISY`, `CLIPPED`, `NO_EXPOSURE_CONTROL` | T-202 |
| `calib/capture.py` | `precheck(..., crop_check: bool = False)` returns a `dict[str, float]` with keys `lit_fraction`, `median_contrast`, `lit_px`, `px_per_proj_px`, `p99_white`, `lit_x0`, `lit_y0`, `lit_x1`, `lit_y1`, `snr`, and `crop_tight` (1.0 or 0.0, only when `crop_check=True`) | T-202, T-203 |
| `CAMERA_NO_PROJECTION` rule | Raised when no lit blob is found, or the largest lit blob has fewer than 4000 px (`MIN_LIT_PX`) or less than half of all lit pixels (`projection_found`); also raised by exposure tuning when white and black differ by too little | T-202 |
| Camera crop | `CameraRoi` (`models/calibration.py`, `extra="forbid"`): `x, y, w, h, frame_width, frame_height: int`, `bin: 1\|2 = 1`; covers `[x, x+w) x [y, y+h)` in full-frame camera pixels; at least 64 x 64, inside the frame, `w` and `h` multiples of `bin`. `CalibSettings.roi: CameraRoi \| null = null` (null = whole picture). Calibration warning `CROP_TOO_TIGHT` (in addition to those listed in §10) when more than 2 % of the lit blob lies within 2 px of the crop border | T-203 |
| Camera check | `POST /api/v1/projects/{pid}/camera-check` body `{"camera_id": str, "roi": CameraRoi \| null}` -> 202 `{"job_id": str}` (kind `"calibration"`, camera busy -> 409). Job result `{"check": CameraCheck}` where `CameraCheck` = `{verdict: "good"\|"marginal"\|"too_coarse", reasons: [Reason], camera_width: int, camera_height: int, lit_box: CameraRoi \| null, suggested_roi: CameraRoi \| null, px_per_proj_px: float, proj_px_per_cam_px: float, stripe_modulation: {"2": float, "3": float, "4": float}, snr: float, median_contrast: float, controls: {str: bool}, suggest_1080p: bool = false}`; all boxes are full-frame | T-204 |
| Camera snapshot crop | `GET /api/v1/devices/cameras/{cid}/snapshot.jpg?crop=x,y,w,h` (optional): four non-negative integers, clamped to the frame; a malformed value or a crop entirely outside the frame gives 400 `BAD_REQUEST` | T-204 |
| `config.py` | `camera_size() -> tuple[int, int]` from env `FACADEKIT_CAMERA_SIZE="WIDTHxHEIGHT"`, default `1280x720`; `1920x1080` is opt-in. An invalid value logs a warning and uses the default | T-204 |
| Scan memory scale | `learn.Scale = "fine"\|"coarse"`; `scale_class(px_per_proj_px) -> Scale \| None` ("fine" when `finest_readable_skip(px_per_proj_px) <= 2`, else "coarse"; `None` for `None`); `ScanRecord.scale: Scale \| None = None` (absent in older records) | T-205 |
| `config.py`, `fpp/publish.py`, `render/presets.py` | User overrides in `<data dir>/config.json` (R-008): `config.load_overrides() -> dict[str, Any]` (dotted keys; invalid JSON raises `BAD_REQUEST`; unknown or malformed settings ignored with one warning). Keys: `fpp.video_dir`, `fpp.sequences_dir`, `fpp.video_out`, `fpp.entry_type_media`, `fpp.entry_type_both`, `render.presets.<id>.video_args`, `render.default_preset`. Accessors `publish.video_dir()`, `publish.sequences_dir()`, `presets.default_preset_id()`; `get_preset(id)` applies the `video_args` override. `facadekit doctor` prints the active overrides; `facadekit render --preset` defaults to `default_preset_id()` | R-008 |
| Logging and diagnostics | `facadekit serve` writes `<data dir>/logs/facadekit.log` (rotating, 1 MB x 3 backups; `logs.setup_logging(data_dir)`). Every HTTP response carries `X-Request-ID` (8 hex chars), the same id that is on the request's log lines; error bodies are unchanged. Refused requests are logged with their error code and message, crashes with a traceback (the response is `INTERNAL` naming the request id). `GET /diagnostics` (loopback only) -> `{"system": {facadekit, python, platform, os, machine, opencv, numpy}, "sim": bool, "projector": ProjectorStatus, "jobs": [{id, kind, state, message, error, created_at}], "log_file": str \| null, "log_tail": [str]}` with the home directory written as `~`. `facadekit doctor --bundle FILE.zip` writes the doctor report and the log files. `AppState.log_file`, `JobRunner.recent()`, `create_app(log_file=)`. Web: `ApiError.requestId`, `recentErrors()`, `getDiagnostics()`, `components/DiagnosticsButton` | – |
| Camera layer | `CameraInfo` gains `kind: "built_in"\|"external"\|"phone"\|"unknown"` (default `"unknown"`); `name` is the device name where the system gives it (macOS via AVFoundation, Linux via sysfs) and no camera is opened to list them; `width`/`height` are the requested size until the camera has been opened once. `GET /devices/cameras?refresh=true` makes a platform that can only find cameras by opening them look again (otherwise it looks once). `GET /devices/cameras/access` -> `{"status": "authorized"\|"denied"\|"restricted"\|"not_determined"\|"unknown", "message": str \| null}`. New error code `CAMERA_ACCESS_DENIED` (HTTP 500 by the default mapping) with the fix in its message. `devices/avfoundation.py` (`devices()`, `authorization()`), `camera_opencv.named_cameras(size)`, `probe_cameras(..., known=)` (`list_cameras` kept as an alias), `access_problem()`, `prepare_camera_access()` (called by `serve` on the main thread). `CameraManager(camera_factory=, lister=, prober=, clock=)`, `CameraManager.list(refresh=)`; the preview camera is read by a background thread and a snapshot returns its newest frame. Web: `listCameras(refresh)`, `cameraAccess()`, `fetchSnapshot(cid)`, `pages/setup/LiveView` | – |
| Scan timing | `CalibSettings.pattern_settle_ms: int \| null` (default null = use `settle_ms` for every picture): the wait before a stripe picture. With `settle_ms == 0` (automatic) it is measured by `tuning.measure_pattern_latency(display, camera, *, step_settle_ms, frame_interval_ms, clock, sleep) -> int \| None` and is never longer than `settle_ms`; black and white pictures keep `settle_ms`. `capture._shoot(..., stripes=)`. `calib/timing.py::ScanTimer` wraps display, camera and sleep; `run_calibration` logs one `scan timing` line per scan. `sim/lag.py`: `VirtualClock`, `LaggyDisplay`, `LaggyCamera` (timing model for tests; its numbers are assumptions) | – |
