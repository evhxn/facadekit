from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from facadekit.errors import FkError
from facadekit.media.probe import probe
from facadekit.media.reader import VideoReader
from facadekit.models.project import Asset, Clip, Project, ProjectorSpec, Surface, Timeline, Track
from facadekit.render.outline import outline_frame, render_alignment_check
from facadekit.render.pipeline import render_project
from facadekit.store.project_store import save_project_dir

QUAD = ((80.0, 30.0), (240.0, 30.0), (240.0, 150.0), (80.0, 150.0))


def _project(tmp_path: Path, *, audio: bool = False, sample_audio: Path | None = None) -> Project:
    img = np.zeros((50, 50, 3), np.uint8)
    img[:] = (0, 0, 255)
    cv2.imwrite(str(tmp_path / "red.png"), img)
    assets = [
        Asset(
            id="red", kind="image", filename="red.png", original_name="red.png",
            width=50, height=50, duration_ms=None, fps=None, has_alpha=False,
        )
    ]  # fmt: skip
    if audio:
        assert sample_audio is not None
        (tmp_path / "a.wav").write_bytes(sample_audio.read_bytes())
        assets.append(
            probe(tmp_path / "a.wav").model_copy(update={"id": "aud", "filename": "a.wav"})
        )
    clip = Clip(
        id="c1", asset_id="red", surface_id="s1", start_ms=0, duration_ms=2000, fit="stretch"
    )
    project = Project(
        id="p",
        name="p",
        projector=ProjectorSpec(width=320, height=180, fps=30),
        surfaces=[Surface(id="s1", name="Wall", quad=QUAD, aspect=1.0)],
        assets=assets,
        timeline=Timeline(
            duration_ms=2000,
            audio_asset_id="aud" if audio else None,
            tracks=[Track(id="t0", name="t0", clips=[clip])],
        ),
    )
    save_project_dir(tmp_path, project)
    return project


def test_render_red(tmp_path: Path) -> None:
    _project(tmp_path)
    out = tmp_path / "out" / "r.mp4"
    result = render_project(tmp_path, "preview-540p", out)
    assert result["frames"] == 60
    assert result["filename"] == "r.mp4"
    assert result["size_bytes"] == out.stat().st_size > 0
    info = probe(out)
    assert info.kind == "video"
    assert info.duration_ms is not None and 1900 <= info.duration_ms <= 2100
    assert not out.with_suffix(".part.mp4").exists()
    with VideoReader(out, fps=30, width=960, height=540, start_ms=1000) as reader:
        frame = reader.read()
    assert frame is not None
    b, g, r = (int(v) for v in frame[270, 480])
    assert r > 150 and g < 80
    assert int(frame[5, 5].max()) < 30


def test_audio(tmp_path: Path, sample_audio: Path) -> None:
    _project(tmp_path, audio=True, sample_audio=sample_audio)
    with_audio = tmp_path / "a.mp4"
    render_project(tmp_path, "preview-540p", with_audio)
    assert probe(with_audio).has_audio
    silent = tmp_path / "s.mp4"
    render_project(tmp_path, "preview-540p", silent, include_audio=False)
    assert probe(silent).has_audio


def test_cancel(tmp_path: Path) -> None:
    _project(tmp_path)
    out = tmp_path / "c.mp4"
    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 5

    with pytest.raises(FkError) as ei:
        render_project(tmp_path, "preview-540p", out, cancel=cancel)
    assert ei.value.code == "CANCELLED"
    assert not out.exists()
    assert not out.with_suffix(".part.mp4").exists()


def test_unknown_preset(tmp_path: Path) -> None:
    _project(tmp_path)
    with pytest.raises(FkError) as ei:
        render_project(tmp_path, "nope", tmp_path / "x.mp4")
    assert ei.value.code == "UNKNOWN_PRESET"


def test_progress(tmp_path: Path) -> None:
    _project(tmp_path)
    values: list[float] = []
    render_project(
        tmp_path, "preview-540p", tmp_path / "p.mp4", progress=lambda v, _m: values.append(v)
    )
    assert values and values[-1] == 1.0
    assert values == sorted(values)


def test_outline_frame(tmp_path: Path) -> None:
    project = _project(tmp_path)
    frame = outline_frame(project)
    assert frame.shape == (180, 320, 3)
    assert tuple(int(v) for v in frame[30, 160]) == (255, 255, 255)
    assert tuple(int(v) for v in frame[100, 120]) == (0, 0, 0)
    filled = outline_frame(project, fill=0.1)
    assert tuple(int(v) for v in filled[60, 100]) == (26, 26, 26)
    selected = outline_frame(project, selected_id="s1")
    assert tuple(int(v) for v in selected[30, 160]) == (0, 255, 255)


def test_alignment_check(tmp_path: Path) -> None:
    _project(tmp_path)
    out = tmp_path / "al.mp4"
    result = render_alignment_check(tmp_path, out, seconds=2, preset_id="preview-540p")
    assert result["frames"] == 60
    info = probe(out)
    assert info.duration_ms is not None and 1900 <= info.duration_ms <= 2100
