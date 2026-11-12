from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from facadekit.calib import phone
from facadekit.calib.patterns import PatternSpec, pattern_sequence
from facadekit.calib.phone import (
    MAX_VIDEO_BYTES,
    PHONE_CAMERA_ID,
    analyse_phone_video,
    check_upload_name,
    check_video_file,
    light_show_seconds,
    phone_upload_path,
    play_light_show,
    run_phone_calibration,
)
from facadekit.calib.selfclock import LEAD_IN, TAIL_BLACK_S, video_seconds
from facadekit.cli import main as cli_main
from facadekit.errors import FkError
from facadekit.media.ffmpeg import ffmpeg_exe
from facadekit.media.probe import probe
from facadekit.models.calibration import CalibSettings
from facadekit.sim.devices import SimDisplay
from facadekit.sim.e2e import _map_corner_error
from facadekit.sim.phone_video import make_phone_video
from facadekit.sim.scene import SimScene, far_scene, true_px_per_proj_px

FPS = 15
PREROLL_S = 11.3
MAX_ERR_PX = 1.5


def _scene() -> SimScene:
    return far_scene(0.9, cam_size=(1920, 1080), world_scale=2.0)


def _spec(scene: SimScene) -> PatternSpec:
    return PatternSpec(*scene.proj_size)


def _door(scene: SimScene) -> np.ndarray:
    return np.array(scene.feature_quad_proj("garage_door"), dtype=np.float64)


def _ffmpeg(*args: str) -> None:
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    scene = _scene()
    out = tmp_path_factory.mktemp("phone") / "phone.mp4"
    make_phone_video(scene, _spec(scene), out, preroll_s=PREROLL_S, fps=FPS)
    return out


def _code(exc: pytest.ExceptionInfo[FkError]) -> str:
    return exc.value.code


# --- light show ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        assert s >= 0
        self.now += s


class RecordingDisplay(SimDisplay):
    def __init__(self, scene: SimScene, clock: FakeClock, delay: np.random.Generator | None):
        super().__init__(scene)
        self.clock = clock
        self.delay = delay
        self.log: list[tuple[float, str]] = []

    def show(self, image: np.ndarray) -> None:
        t = self.clock()
        super().show(image)
        kind = "black" if image.max() == 0 else "white" if image.min() == 255 else "pattern"
        self.log.append((t, kind))
        if self.delay is not None:
            self.clock.now += float(self.delay.uniform(0, 0.08))


def _expected_kinds(spec: PatternSpec) -> list[str]:
    kinds = list(LEAD_IN) + [f.kind for f in pattern_sequence(spec)] + ["black"]
    return kinds


def test_light_show_seconds() -> None:
    spec = PatternSpec(1920, 1080)
    assert light_show_seconds(spec, 15.0) == pytest.approx(15 + video_seconds(38))


def test_play_light_show_exact_deadlines() -> None:
    scene = SimScene(proj_size=(320, 180))
    spec = _spec(scene)
    clock = FakeClock()
    display = RecordingDisplay(scene, clock, None)
    messages: list[str] = []
    start = clock()
    play_light_show(
        display, spec, countdown_s=5, progress=lambda f, m: messages.append(m),
        clock=clock, sleep=clock.sleep,
    )  # fmt: skip
    t0 = start + 5
    assert display.log[0] == (start, "black")
    holds = display.log[1:]
    assert [k for _, k in holds] == _expected_kinds(spec)
    for k, (t, _) in enumerate(holds):
        assert t == pytest.approx(t0 + k, abs=1e-9)
    assert clock() == pytest.approx(t0 + len(holds) - 1 + TAIL_BLACK_S)
    assert messages[:5] == [f"Starting in {n} s" for n in (5, 4, 3, 2, 1)]
    assert messages[5] == f"Light show 1 of {len(holds)}"


def test_play_light_show_slow_display_does_not_accumulate() -> None:
    scene = SimScene(proj_size=(320, 180))
    spec = _spec(scene)
    clock = FakeClock()
    display = RecordingDisplay(scene, clock, np.random.default_rng(3))
    start = clock()
    play_light_show(display, spec, countdown_s=15, clock=clock, sleep=clock.sleep)
    holds = display.log[1:]
    assert [k for _, k in holds] == _expected_kinds(spec)
    t0 = holds[0][0]
    assert 0 <= t0 - (start + 15) <= 0.08  # t0 is read after the first black show() returns
    late = [t - (t0 + k) for k, (t, _) in enumerate(holds)]
    assert max(late) <= 0.08 + 1e-9  # within 80 ms of its deadline, no accumulation
    assert min(late) >= -1e-9


def test_play_light_show_cancel() -> None:
    scene = SimScene(proj_size=(320, 180))
    clock = FakeClock()
    display = RecordingDisplay(scene, clock, None)
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 8

    with pytest.raises(FkError) as exc:
        play_light_show(
            display, _spec(scene), countdown_s=5, cancel=cancel, clock=clock, sleep=clock.sleep
        )
    assert _code(exc) == "CANCELLED"
    assert display.log[-1][1] == "black"


# --- phone video analysis -----------------------------------------------------------------


def test_phone_video_calibration(video: Path, tmp_path: Path) -> None:
    scene = _scene()
    assert true_px_per_proj_px(scene) == pytest.approx(0.65, abs=0.03)
    cal = run_phone_calibration(
        tmp_path, video, CalibSettings(), proj_size=scene.proj_size, strategy=None, advisor=None
    )
    assert cal.camera_id == PHONE_CAMERA_ID
    assert (cal.camera_width, cal.camera_height) == (1920, 1080)
    roi = cal.settings.roi
    assert roi is not None
    assert roi.w * roi.h < 1920 * 1080
    assert cal.quality is not None and cal.quality.coverage >= 0.85
    p2c = np.load(tmp_path / "calibration" / cal.id / "p2c.npy")
    err = _map_corner_error(p2c, scene.h_p2c, _door(scene))
    print("phone corner error", err, "coverage", cal.quality.coverage)
    assert err < MAX_ERR_PX


def test_phone_moved(tmp_path: Path) -> None:
    scene = _scene()
    spec = _spec(scene)
    n = len(pattern_sequence(spec))
    middle = PREROLL_S + len(LEAD_IN) + n / 2
    out = tmp_path / "moved.mp4"
    make_phone_video(
        scene, spec, out, preroll_s=PREROLL_S, fps=FPS, shift_after_s=middle, shift_px=(4, 0)
    )
    with pytest.raises(FkError) as exc:
        analyse_phone_video(out, spec)
    assert _code(exc) == "PHONE_MOVED"


def test_video_cut_before_end(video: Path, tmp_path: Path) -> None:
    scene = _scene()
    spec = _spec(scene)
    end_of_patterns = PREROLL_S + len(LEAD_IN) + len(pattern_sequence(spec))
    cut = tmp_path / "cut.mp4"
    _ffmpeg("-i", str(video), "-t", f"{end_of_patterns - 10:.2f}", "-c", "copy", str(cut))
    with pytest.raises(FkError) as exc:
        analyse_phone_video(cut, spec)
    assert _code(exc) == "PHONE_VIDEO_NO_PATTERN"


def test_late_start_no_marker(video: Path, tmp_path: Path) -> None:
    """Recording started after the flashes: long enough, but no marker."""
    late = tmp_path / "late.mp4"
    _ffmpeg("-ss", f"{PREROLL_S + 6:.2f}", "-i", str(video), "-c:v", "libx264", "-preset",
            "ultrafast", str(late))  # fmt: skip
    spec = PatternSpec(320, 180)  # short sequence, so the duration check passes
    with pytest.raises(FkError) as exc:
        analyse_phone_video(late, spec)
    assert _code(exc) == "PHONE_VIDEO_NO_PATTERN"


def test_rotated_video(video: Path, tmp_path: Path) -> None:
    rotated = tmp_path / "rotated.mp4"
    try:
        _ffmpeg("-display_rotation", "90", "-i", str(video), "-c", "copy", str(rotated))
    except subprocess.CalledProcessError:
        pytest.skip("the bundled ffmpeg cannot write a rotation tag")
    asset = probe(rotated)
    if (asset.width, asset.height) != (1080, 1920):
        pytest.skip("the bundled ffmpeg did not write a rotation tag")
    scene = _scene()

    # Find which quarter turn ffmpeg applies on decode, from one decoded frame of each file.
    def frame(path: Path, w: int, h: int) -> np.ndarray:
        raw = subprocess.run(
            [ffmpeg_exe(), "-loglevel", "error", "-ss", "20", "-i", str(path), "-frames:v", "1",
             "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        return np.frombuffer(raw, np.uint8).reshape(h, w).astype(np.float32)

    upright, turned = frame(video, 1920, 1080), frame(rotated, 1080, 1920)
    k = min(
        (1, 3), key=lambda q: float(np.abs(np.rot90(upright, q) - turned).mean())
    )  # np.rot90 turns counter-clockwise
    # Pixel-corner coordinates: CCW quarter turn (x, y) -> (y, W - x); CW (x, y) -> (H - y, x).
    if k == 1:
        rot = np.array([[0, 1, 0], [-1, 0, 1920], [0, 0, 1]], dtype=np.float64)
    else:
        rot = np.array([[0, -1, 1080], [1, 0, 0], [0, 0, 1]], dtype=np.float64)

    cal = run_phone_calibration(
        tmp_path, rotated, CalibSettings(), proj_size=scene.proj_size, strategy=None, advisor=None
    )
    assert (cal.camera_width, cal.camera_height) == (1080, 1920)
    p2c = np.load(tmp_path / "calibration" / cal.id / "p2c.npy")
    err = _map_corner_error(p2c, rot @ scene.h_p2c, _door(scene))
    print("rotated corner error", err)
    assert err < MAX_ERR_PX


def test_scan_failed_gets_phone_hint(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_a: object, **_k: object) -> None:
        raise FkError("SCAN_FAILED", "Only 20% of the projection was measured.")

    monkeypatch.setattr(phone, "run_calibration_from_capture", fail)
    scene = _scene()
    with pytest.raises(FkError) as exc:
        run_phone_calibration(
            tmp_path, video, CalibSettings(), proj_size=scene.proj_size, strategy=None,
            advisor=None,
        )  # fmt: skip
    assert _code(exc) == "SCAN_FAILED"
    assert exc.value.message.endswith(
        "Did the phone move? Use a tripod or lean it on something steady."
    )


def test_cli_phone_scan_analyse(
    video: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj.fkproj"
    assert cli_main(["new", str(project), "--name", "demo"]) == 0
    assert cli_main(["phone-scan", "analyse", str(project), str(video), "--skip-lsb", "2"]) == 0
    out = capsys.readouterr().out
    assert "coverage:" in out
    assert "corner top-left: camera" in out


# --- hostile inputs -----------------------------------------------------------------------


def test_text_file_renamed_mp4(tmp_path: Path) -> None:
    fake = tmp_path / "video.mp4"
    fake.write_text("this is not a video\n" * 100)
    with pytest.raises(FkError) as exc:
        analyse_phone_video(fake, PatternSpec(320, 180))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


@pytest.mark.parametrize(
    "head",
    [
        b"#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n",  # HLS playlist pointing at a local file
        b"ffconcat version 1.0\nfile '/etc/passwd'\n",  # concat demuxer script
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,  # an image
        b"MZ\x90\x00" + b"\x00" * 64,  # an executable
        b"",  # empty
    ],
)
def test_wrong_container_never_reaches_ffmpeg(
    tmp_path: Path, head: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_ffmpeg(*_a: object, **_k: object) -> None:
        raise AssertionError("ffmpeg must not see this file")

    monkeypatch.setattr(phone.subprocess, "run", no_ffmpeg)
    monkeypatch.setattr(phone.subprocess, "Popen", no_ffmpeg)
    f = tmp_path / "video.mp4"
    f.write_bytes(head)
    with pytest.raises(FkError) as exc:
        analyse_phone_video(f, PatternSpec(320, 180))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


def test_oversized_file(tmp_path: Path) -> None:
    big = tmp_path / "big.mp4"
    with big.open("wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42")
        f.truncate(MAX_VIDEO_BYTES + 1)  # sparse: no disk space used
    with pytest.raises(FkError) as exc:
        check_video_file(big)
    assert _code(exc) == "BAD_REQUEST"


def test_missing_file_and_directory(tmp_path: Path) -> None:
    for p in (tmp_path / "nope.mp4", tmp_path):
        with pytest.raises(FkError) as exc:
            check_video_file(p)
        assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


@pytest.mark.parametrize("name", ["IMG_0001.MOV", "clip.mp4", "a.b.m4v", "x.webm", "y.3gp"])
def test_upload_name_ok(name: str) -> None:
    check_upload_name(name)


@pytest.mark.parametrize(
    "name", ["notes.txt", "clip.mp4.exe", "clip", "photo.png", "..", "../../etc/passwd", ""]
)
def test_upload_name_refused(name: str) -> None:
    with pytest.raises(FkError) as exc:
        check_upload_name(name)
    assert _code(exc) == "BAD_REQUEST"


def test_upload_path_is_server_named(tmp_path: Path) -> None:
    out = phone_upload_path(tmp_path, "phs_0123abcd")
    assert out == tmp_path / "calibration" / ".phone" / "phs_0123abcd.upload"


@pytest.mark.parametrize(
    "sid",
    [
        "../../etc/passwd",
        "phs_0123abcd/../../x",
        "phs_0123abcd/..",
        "..\\..\\x",
        "/etc/passwd",
        "phs_0123ABCD",
        "phs_0123abc",
        "phs_0123abcd\n",
        "phs_0123abcd\x00",
        "",
    ],
)
def test_upload_path_refuses_traversal(tmp_path: Path, sid: str) -> None:
    with pytest.raises(FkError) as exc:
        phone_upload_path(tmp_path, sid)
    assert _code(exc) == "BAD_REQUEST"


def test_truncated_video(video: Path, tmp_path: Path) -> None:
    """The moov box of an ffmpeg-written MP4 is at the end: a cut-off upload has none."""
    cut = tmp_path / "truncated.mp4"
    with video.open("rb") as src:
        cut.write_bytes(src.read(video.stat().st_size // 2))
    with pytest.raises(FkError) as exc:
        analyse_phone_video(cut, _spec(_scene()))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


def test_corrupt_video(video: Path, tmp_path: Path) -> None:
    """Valid magic, garbage after it."""
    bad = tmp_path / "corrupt.mp4"
    rng = np.random.default_rng(0)
    bad.write_bytes(b"\x00\x00\x00\x18ftypmp42" + rng.bytes(2_000_000))
    with pytest.raises(FkError) as exc:
        analyse_phone_video(bad, _spec(_scene()))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


def test_corrupt_middle(video: Path, tmp_path: Path) -> None:
    """Garbage in the middle of the frame data: a clean error, never a crash or a hang."""
    data = bytearray(video.read_bytes())
    mid = len(data) // 2
    data[mid : mid + 4_000_000] = np.random.default_rng(1).bytes(4_000_000)
    bad = tmp_path / "corrupt_middle.mp4"
    bad.write_bytes(bytes(data))
    scene = _scene()
    try:
        run_phone_calibration(
            tmp_path, bad, CalibSettings(), proj_size=scene.proj_size, strategy=None,
            advisor=None,
        )  # fmt: skip
    except FkError as exc:
        assert exc.code in {
            "PHONE_VIDEO_UNREADABLE", "PHONE_VIDEO_NO_PATTERN", "PHONE_MOVED", "SCAN_FAILED",
            "CAMERA_NO_PROJECTION",
        }  # fmt: skip


def test_tiny_resolution_refused(tmp_path: Path) -> None:
    tiny = tmp_path / "tiny.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=black:s=96x64:d=60:r=15", "-c:v", "libx264",
            "-preset", "ultrafast", str(tiny))  # fmt: skip
    with pytest.raises(FkError) as exc:
        analyse_phone_video(tiny, PatternSpec(320, 180))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


def test_too_long_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = tmp_path / "long.mkv"
    _ffmpeg("-f", "lavfi", "-i", "color=black:s=320x180:d=50:r=5", "-c:v", "libx264",
            "-preset", "ultrafast", str(clip))  # fmt: skip
    monkeypatch.setattr(phone, "MAX_VIDEO_S", 40)
    with pytest.raises(FkError) as exc:
        analyse_phone_video(clip, PatternSpec(320, 180))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


def test_too_short_refused(tmp_path: Path) -> None:
    clip = tmp_path / "short.mov"
    _ffmpeg("-f", "lavfi", "-i", "color=black:s=320x180:d=5:r=5", "-c:v", "libx264",
            "-preset", "ultrafast", str(clip))  # fmt: skip
    with pytest.raises(FkError) as exc:
        analyse_phone_video(clip, PatternSpec(320, 180))
    assert _code(exc) == "PHONE_VIDEO_NO_PATTERN"


def test_decode_bounds(video: Path) -> None:
    frames = list(phone._decode(video, "mov", "fps=30,scale=160:90", (160, 90), max_frames=7))
    assert len(frames) == 7
    with pytest.raises(FkError) as exc:
        list(
            phone._decode(video, "mov", "fps=30", (1920, 1080), max_frames=100_000, timeout_s=0.05)
        )
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"
    assert "too long" in exc.value.message


def test_forced_demuxer_mismatch_is_unreadable(video: Path) -> None:
    with pytest.raises(FkError) as exc:
        list(phone._decode(video, "matroska", "fps=30", (1920, 1080), max_frames=10))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


# --- security review follow-ups -----------------------------------------------------------


def _spoofed(tmp_path: Path, size: str, seconds: int, title: str, comment: str) -> Path:
    out = tmp_path / f"spoof_{size}.mp4"
    _ffmpeg("-f", "lavfi", "-i", f"color=black:s={size}:d={seconds}:r=1", "-c:v", "libx264",
            "-preset", "ultrafast", "-metadata", f"title={title}", "-metadata",
            f"comment={comment}", str(out))  # fmt: skip
    return out


@pytest.fixture(scope="module")
def huge_spoof(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """8192 x 8192 frames whose metadata pretends to be a 640 x 360, 5-minute video."""
    return _spoofed(
        tmp_path_factory.mktemp("spoof"), "8192x8192", 2,
        "Stream #0:0: Video: h264, 640x360", "Duration: 00:05:00.00, start",
    )  # fmt: skip


def test_metadata_cannot_spoof_size(huge_spoof: Path) -> None:
    import time

    t = time.monotonic()
    with pytest.raises(FkError) as exc:
        analyse_phone_video(huge_spoof, PatternSpec(320, 180))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"
    assert time.monotonic() - t < 5  # refused at the probe, nothing decoded


def test_decoder_refuses_huge_frames_whatever_the_probe_says(huge_spoof: Path) -> None:
    """-max_pixels: even a decode told to expect 640 x 360 never allocates 8192 x 8192."""
    with pytest.raises(FkError) as exc:
        list(phone._decode(huge_spoof, "mov", "fps=30,scale=640:360", (640, 360), max_frames=5))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"


def test_metadata_cannot_spoof_duration(tmp_path: Path) -> None:
    clip = _spoofed(
        tmp_path, "320x180", 5, "x", "x\n  Duration: 00:02:00.00, start: 0.000000, bitrate: 1 kb/s"
    )
    with pytest.raises(FkError) as exc:
        analyse_phone_video(clip, PatternSpec(320, 180))
    assert _code(exc) == "PHONE_VIDEO_NO_PATTERN"  # its real 5 s, not the claimed 2 min


def test_parse_info_ignores_metadata_lines() -> None:
    text = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'file:/x/Duration: 00:09:00.00,.mp4':\n"
        "  Metadata:\n"
        "    title           : Stream #0:0: Video: h264, 640x360\n"
        "    comment         : x\n"
        "                    :   Duration: 00:05:00.00, start\n"
        "  Duration: 00:01:00.00, start: 0.000000, bitrate: 26 kb/s\n"
        "  Stream #0:0[0x1](und): Video: h264 (avc1 / 0x31637661), yuv420p(progressive), "
        "8192x8192 [SAR 1:1 DAR 1:1], 26 kb/s, 2 fps, 2 tbr, 16384 tbn (default)\n"
        "    Metadata:\n"
        "      handler_name    : Display Matrix: rotation of 90.00 degrees\n"
        "    Side data:\n"
        "      Display Matrix: rotation of -90.00 degrees\n"
    )
    assert phone._parse_info(text) == (8192, 8192, 60.0)


def test_probe_uses_forced_demuxer_and_file_protocol(
    video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(list(cmd))
        return real_run(cmd, **kw)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(phone.subprocess, "run", spy)
    phone._video_size(video, "mov", 10)
    (cmd,) = seen
    i = cmd.index("-i")
    assert cmd[i + 1] == f"file:{video.resolve()}"
    pre = cmd[:i]
    assert pre[pre.index("-protocol_whitelist") + 1] == "file"
    assert pre[pre.index("-f") + 1] == "mov"
    assert pre[pre.index("-max_pixels") + 1] == str(phone.MAX_VIDEO_PX)


def test_one_deadline_for_all_passes(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phone, "DECODE_TIMEOUT_S", 0.3)
    with pytest.raises(FkError) as exc:
        analyse_phone_video(video, _spec(_scene()))
    assert _code(exc) == "PHONE_VIDEO_UNREADABLE"
    assert "too long" in exc.value.message


def test_decode_max_frames_clamped(video: Path) -> None:
    for mf in (0, -1):
        frames = list(phone._decode(video, "mov", "fps=30,scale=160:90", (160, 90), max_frames=mf))
        assert len(frames) == 1


def test_upload_path_refuses_symlinked_dirs(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    proj = tmp_path / "proj"
    (proj / "calibration").mkdir(parents=True)
    (proj / "calibration" / ".phone").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FkError):
        phone_upload_path(proj, "phs_deadbeef")

    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    (proj2 / "calibration").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FkError):
        phone_upload_path(proj2, "phs_deadbeef")

    proj3 = tmp_path / "proj3"
    (proj3 / "calibration" / ".phone").mkdir(parents=True)
    (proj3 / "calibration" / ".phone" / "phs_deadbeef.upload").symlink_to(outside / "x")
    with pytest.raises(FkError):
        phone_upload_path(proj3, "phs_deadbeef")  # a dangling link planted at the upload name
    assert phone_upload_path(proj3, "phs_0badcafe").parent == proj3 / "calibration" / ".phone"
