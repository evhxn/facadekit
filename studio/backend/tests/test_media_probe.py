from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from facadekit.errors import FkError
from facadekit.media.ffmpeg import run_ffmpeg
from facadekit.media.probe import make_thumbnail, probe


def test_probe_video(sample_video: Path) -> None:
    a = probe(sample_video)
    assert a.kind == "video"
    assert (a.width, a.height) == (320, 180)
    assert a.fps == 30.0
    assert a.duration_ms is not None and 1900 <= a.duration_ms <= 2100
    assert a.has_audio
    assert a.id == "" and a.filename == "" and a.original_name == "sample.mp4"


def test_probe_image(sample_image: Path) -> None:
    a = probe(sample_image)
    assert a.kind == "image"
    assert (a.width, a.height) == (64, 48)
    assert a.has_alpha
    assert a.duration_ms is None and a.fps is None


def test_probe_audio(sample_audio: Path) -> None:
    a = probe(sample_audio)
    assert a.kind == "audio"
    assert a.duration_ms is not None and 2900 <= a.duration_ms <= 3100
    assert a.has_audio
    assert a.width is None and a.height is None and a.fps is None


def test_probe_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "fake.mp4"
    bad.write_text("this is not a video")
    with pytest.raises(FkError) as ei:
        probe(bad)
    assert ei.value.code == "UNSUPPORTED_MEDIA"


def test_probe_path_with_spaces(sample_video: Path, tmp_path: Path) -> None:
    p = tmp_path / "my clip é.mp4"
    p.write_bytes(sample_video.read_bytes())
    assert probe(p).width == 320


def _assert_jpeg_320(path: Path) -> None:
    assert path.read_bytes()[:2] == b"\xff\xd8"
    img = cv2.imread(str(path))
    assert img is not None
    assert img.shape[1] == 320


def test_thumbnails(sample_video: Path, sample_image: Path, tmp_path: Path) -> None:
    v = tmp_path / "v.jpg"
    make_thumbnail(sample_video, "video", v)
    _assert_jpeg_320(v)
    i = tmp_path / "i.jpg"
    make_thumbnail(sample_image, "image", i)
    _assert_jpeg_320(i)


def test_thumbnail_audio_rejected(sample_audio: Path, tmp_path: Path) -> None:
    with pytest.raises(FkError) as ei:
        make_thumbnail(sample_audio, "audio", tmp_path / "a.jpg")
    assert ei.value.code == "BAD_REQUEST"


def test_run_ffmpeg_failure(tmp_path: Path) -> None:
    with pytest.raises(FkError) as ei:
        run_ffmpeg(["-i", "missing.mp4", str(tmp_path / "out.mp4")])
    assert ei.value.code == "FFMPEG_FAILED"


def test_probe_ignores_spoofed_container_metadata(tmp_path: Path) -> None:
    out = tmp_path / "spoof.mp4"
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-metadata", "title=Stream #0:0: Video: h264, 640x360, 25 fps",
            "-metadata", "comment=Duration: 00:05:00.00, start: 0.0",
            "-metadata:s:v:0", "title=Stream #0:0: Video: h264, 1280x720",
            str(out),
        ]
    )  # fmt: skip
    a = probe(out)
    assert (a.width, a.height) == (320, 180)
    assert a.duration_ms is not None and 1900 <= a.duration_ms <= 2100


def test_probe_spoofed_metadata_cannot_fake_video_or_audio(tmp_path: Path) -> None:
    out = tmp_path / "spoof.m4a"
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-metadata", "comment=Duration: 00:05:00.00, start: 0.0",
            str(out),
        ]
    )  # fmt: skip
    a = probe(out)
    assert a.duration_ms is not None and 1900 <= a.duration_ms <= 2100
    spoof_video = tmp_path / "noaudio.mp4"
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-metadata", "title=Stream #0:1: Audio: aac",
            str(spoof_video),
        ]
    )  # fmt: skip
    assert not probe(spoof_video).has_audio
