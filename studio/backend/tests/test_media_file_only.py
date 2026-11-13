"""Every ffmpeg run on a user-supplied file opens it through media.ffmpeg.local_input:
local `file:` only. The commands are captured rather than attacked: the ffmpeg build bundled
by imageio-ffmpeg refused every crafted playlist tried during AUDIT-02 even unguarded, so a
live attack test could not fail here. Other builds (FACADEKIT uses whatever imageio ships)
may differ, which is why the guard is applied everywhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from facadekit.errors import FkError
from facadekit.media.ffmpeg import ffmpeg_info, local_input
from facadekit.media.probe import make_thumbnail, probe
from facadekit.media.reader import VideoReader
from facadekit.render.encoder import Encoder
from facadekit.render.presets import get_preset


def _user_inputs(cmd: list[str]) -> list[str]:
    """The `-i` operands that are files (not the raw-frame pipe or a lavfi source)."""
    out = []
    for i, arg in enumerate(cmd):
        if arg == "-i" and cmd[i + 1] != "-" and cmd[i - 2 : i] != ["-f", "lavfi"]:
            out.append(cmd[i + 1])
            assert cmd[i - 2 : i] == ["-protocol_whitelist", "file"], cmd
    return out


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    real_popen = subprocess.Popen

    class Popen(real_popen):  # type: ignore[type-arg]
        def __init__(self, cmd: list[str], *a: Any, **kw: Any) -> None:
            seen.append(list(cmd))
            super().__init__(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Popen)  # subprocess.run goes through Popen too
    return seen


def test_local_input_shape(tmp_path: Path) -> None:
    args = local_input(tmp_path / "a b:c.mp4")
    assert args[:3] == ["-protocol_whitelist", "file", "-i"]
    assert args[3] == f"file:{(tmp_path / 'a b:c.mp4').resolve()}"


def test_probe_thumbnail_and_reader_open_local_files_only(
    commands: list[list[str]], sample_video: Path, sample_audio: Path, tmp_path: Path
) -> None:
    ffmpeg_info(sample_video)
    assert probe(sample_video).kind == "video"
    assert probe(sample_audio).kind == "audio"
    make_thumbnail(sample_video, "video", tmp_path / "t.jpg")
    assert (tmp_path / "t.jpg").stat().st_size > 0
    reader = VideoReader(sample_video, fps=30, width=64, height=36)
    try:
        assert reader.read() is not None
    finally:
        reader.close()
    found = [f for f in (_user_inputs(c) for c in commands if "-i" in c) if f]
    assert len(found) >= 5
    for operands in found:
        assert len(operands) == 1 and operands[0].startswith("file:/")


def test_encoder_audio_input_is_local_file_only(
    commands: list[list[str]], sample_audio: Path, tmp_path: Path
) -> None:
    enc = Encoder(
        tmp_path / "out.mp4", width=64, height=36, fps=30, preset=get_preset("preview-540p"),
        audio_path=sample_audio, duration_ms=200,
    )  # fmt: skip
    for _ in range(6):
        enc.write(np.zeros((36, 64, 3), np.uint8))
    enc.close()
    assert (tmp_path / "out.mp4").stat().st_size > 0
    # (the sample fixture's own lavfi command may be captured too; it has no file input)
    found = [_user_inputs(c) for c in commands if "-i" in c]
    assert [f for f in found if f] == [[f"file:{sample_audio.resolve()}"]]  # pipe is exempt


def test_encoder_without_audio_has_no_file_input(commands: list[list[str]], tmp_path: Path) -> None:
    enc = Encoder(
        tmp_path / "out.mp4", width=64, height=36, fps=30, preset=get_preset("preview-540p"),
        audio_path=None, duration_ms=100,
    )  # fmt: skip
    enc.abort()
    (cmd,) = [c for c in commands if "-i" in c]
    assert _user_inputs(cmd) == []


def test_beat_analysis_decodes_a_local_file_only(
    commands: list[list[str]], sample_audio: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without librosa installed the decode step is still reached and checked."""
    import sys
    import types

    from facadekit.ai import beats

    class _Stop(Exception):
        pass

    def load(*_a: Any, **_kw: Any) -> Any:
        raise _Stop

    monkeypatch.setitem(sys.modules, "librosa", types.SimpleNamespace(load=load))
    with pytest.raises(_Stop):
        beats.analyze_beats(sample_audio)
    found = [_user_inputs(c) for c in commands if "-i" in c]
    assert [f for f in found if f] == [[f"file:{sample_audio.resolve()}"]]


def test_a_playlist_with_a_media_extension_is_refused(tmp_path: Path) -> None:
    evil = tmp_path / "evil.mp4"
    evil.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nhttp://127.0.0.1:9/seg.ts\n"
        "#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    with pytest.raises(FkError):
        probe(evil)
