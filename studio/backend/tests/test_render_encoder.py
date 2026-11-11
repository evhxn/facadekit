from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from facadekit.errors import FkError
from facadekit.media.ffmpeg import ffmpeg_info
from facadekit.media.probe import probe
from facadekit.media.reader import VideoReader
from facadekit.render.encoder import Encoder
from facadekit.render.presets import PRESETS, get_preset, output_size


def _frames(enc: Encoder, n: int = 30, w: int = 320, h: int = 180) -> None:
    for i in range(n):
        frame = np.full((h, w, 3), (i * 8) % 256, dtype=np.uint8)
        enc.write(frame)


def test_presets() -> None:
    assert set(PRESETS) == {"pi3-1080p30", "pi3-720p30", "preview-540p"}
    with pytest.raises(FkError) as ei:
        get_preset("nope")
    assert ei.value.code == "UNKNOWN_PRESET"


def test_output_size() -> None:
    assert output_size(get_preset("pi3-1080p30"), 1920, 1080) == (1920, 1080)
    assert output_size(get_preset("pi3-720p30"), 1920, 1080) == (1280, 720)
    assert output_size(get_preset("preview-540p"), 1280, 800) == (960, 600)
    assert output_size(get_preset("pi3-1080p30"), 853, 480) == (852, 480)


def test_encode_silent(tmp_path: Path) -> None:
    out = tmp_path / "a.mp4"
    enc = Encoder(
        out, width=320, height=180, fps=30, preset=get_preset("preview-540p"),
        audio_path=None, duration_ms=1000,
    )  # fmt: skip
    _frames(enc)
    enc.close()
    enc.close()
    a = probe(out)
    assert (a.width, a.height) == (320, 180)
    assert a.has_audio
    assert a.duration_ms is not None and 900 <= a.duration_ms <= 1100


def test_encode_with_audio(tmp_path: Path, sample_audio: Path) -> None:
    out = tmp_path / "b.mp4"
    enc = Encoder(
        out, width=320, height=180, fps=30, preset=get_preset("preview-540p"),
        audio_path=sample_audio, audio_offset_ms=500, duration_ms=1000,
    )  # fmt: skip
    _frames(enc)
    enc.close()
    a = probe(out)
    assert a.has_audio
    assert a.duration_ms is not None and 900 <= a.duration_ms <= 1100


def test_pi3_profile(tmp_path: Path) -> None:
    out = tmp_path / "c.mp4"
    enc = Encoder(
        out, width=320, height=180, fps=30, preset=get_preset("pi3-1080p30"),
        audio_path=None, duration_ms=1000,
    )  # fmt: skip
    _frames(enc)
    enc.close()
    info = ffmpeg_info(out)
    assert "High" in info and "yuv420p" in info


def test_bad_frame_size(tmp_path: Path) -> None:
    enc = Encoder(
        tmp_path / "d.mp4", width=320, height=180, fps=30, preset=get_preset("preview-540p"),
        audio_path=None, duration_ms=1000,
    )  # fmt: skip
    try:
        with pytest.raises(FkError) as ei:
            enc.write(np.zeros((100, 100, 3), dtype=np.uint8))
        assert ei.value.code == "BAD_IMAGE_SIZE"
    finally:
        enc.abort()


def test_pi3_limits(tmp_path: Path) -> None:
    with pytest.raises(FkError) as ei:
        Encoder(
            tmp_path / "e.mp4", width=320, height=180, fps=60, preset=get_preset("pi3-1080p30"),
            audio_path=None, duration_ms=1000,
        )  # fmt: skip
    assert ei.value.code == "BAD_REQUEST"


def test_abort_removes_output(tmp_path: Path) -> None:
    out = tmp_path / "f.mp4"
    enc = Encoder(
        out, width=320, height=180, fps=30, preset=get_preset("preview-540p"),
        audio_path=None, duration_ms=1000,
    )  # fmt: skip
    _frames(enc, 5)
    enc.abort()
    assert not out.exists()


def _encode_solid(out: Path, bgr: tuple[int, int, int]) -> np.ndarray:
    enc = Encoder(
        out, width=64, height=64, fps=30, preset=get_preset("pi3-1080p30"),
        audio_path=None, duration_ms=340,
    )  # fmt: skip
    frame = np.full((64, 64, 3), bgr, dtype=np.uint8)
    for _ in range(10):
        enc.write(frame)
    enc.close()
    with VideoReader(out, fps=30, width=64, height=64) as reader:
        decoded = None
        for i in range(6):
            got = reader.read()
            assert got is not None, f"video ended before frame {i}"
            decoded = got
    assert decoded is not None
    return decoded


def test_output_tagged_bt709(tmp_path: Path) -> None:
    out = tmp_path / "g.mp4"
    _encode_solid(out, (0, 0, 255))
    info = ffmpeg_info(out)
    assert "bt709" in info


@pytest.mark.parametrize(
    ("bgr", "tol"),
    [((0, 0, 255), 12), ((0, 255, 0), 12), ((128, 128, 128), 6)],
    ids=["red", "green", "gray"],
)
def test_colour_round_trip(tmp_path: Path, bgr: tuple[int, int, int], tol: int) -> None:
    decoded = _encode_solid(tmp_path / "h.mp4", bgr)
    centre = decoded[32, 32].astype(int)
    for got, want in zip(centre, bgr, strict=True):
        assert abs(int(got) - want) <= tol, f"centre {centre.tolist()} vs {list(bgr)}"
