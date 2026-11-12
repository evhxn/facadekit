from __future__ import annotations

from pathlib import Path

import numpy as np

from facadekit.media.reader import ClipSource, VideoReader
from facadekit.models.project import Asset


def _count(reader: VideoReader) -> int:
    n = 0
    with reader:
        while (f := reader.read()) is not None:
            assert f.shape == (180, 320, 3)
            n += 1
        assert reader.read() is None
    return n


def _video_asset() -> Asset:
    return Asset(
        id="a1",
        kind="video",
        filename="media/v.mp4",
        original_name="v.mp4",
        width=320,
        height=180,
        duration_ms=2000,
        fps=30.0,
    )


def test_reader_30fps(sample_video: Path) -> None:
    reader = VideoReader(sample_video, fps=30, width=320, height=180)
    proc = reader._proc
    assert 58 <= _count(reader) <= 61
    assert proc.poll() is not None


def test_reader_15fps(sample_video: Path) -> None:
    assert 28 <= _count(VideoReader(sample_video, fps=15, width=320, height=180)) <= 31


def test_reader_start_offset(sample_video: Path) -> None:
    assert (
        28 <= _count(VideoReader(sample_video, fps=30, width=320, height=180, start_ms=1000)) <= 31
    )


def test_reader_close_idempotent(sample_video: Path) -> None:
    reader = VideoReader(sample_video, fps=30, width=320, height=180)
    reader.close()
    reader.close()
    assert reader._proc.poll() is not None


def test_clip_source_video(sample_video: Path) -> None:
    src = ClipSource(_video_asset(), sample_video, fps=30, max_size=(1000, 1000))
    fresh = ClipSource(_video_asset(), sample_video, fps=30, max_size=(1000, 1000))
    try:
        a = src.frame_at(0).copy()
        b = src.frame_at(1000)
        assert np.abs(a.astype(int) - b.astype(int)).mean() > 1
        assert np.array_equal(src.frame_at(1000), b)
        src.frame_at(1500)
        back = src.frame_at(500)
        assert np.array_equal(back, fresh.frame_at(500))
        end = src.frame_at(99999)
        assert end.shape == (180, 320, 3)
    finally:
        src.close()
        fresh.close()
    assert src._reader is None


def test_clip_source_max_size(sample_video: Path) -> None:
    src = ClipSource(_video_asset(), sample_video, fps=30, max_size=(160, 90))
    try:
        assert src.size == (160, 90)
        assert src.frame_at(0).shape == (90, 160, 3)
    finally:
        src.close()


def test_clip_source_image(sample_image: Path) -> None:
    asset = Asset(
        id="i1",
        kind="image",
        filename="media/i.png",
        original_name="i.png",
        width=64,
        height=48,
        duration_ms=None,
        fps=None,
        has_alpha=True,
    )
    src = ClipSource(asset, sample_image, fps=30, max_size=(1000, 1000))
    assert src.frame_at(0).shape == (48, 64, 4)
    assert src.frame_at(5000) is src.frame_at(0)
    src.close()
