from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from facadekit import config
from facadekit.cli import main
from facadekit.errors import FkError
from facadekit.fpp.publish import media_entry, publish_render, sequences_dir, video_dir
from facadekit.render.encoder import Encoder
from facadekit.render.presets import PRESETS, default_preset_id, get_preset
from tests.conftest import FakeFpp


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv("FACADEKIT_DATA", str(root))
    config._warned.clear()
    return root


def _write(root: Path, content: Any) -> None:
    root.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (root / "config.json").write_text(text, encoding="utf-8")


def test_no_file_means_plan_defaults(data: Path) -> None:
    assert config.load_overrides() == {}
    assert (video_dir(), sequences_dir()) == ("videos", "sequences")
    assert media_entry("fk_a.mp4") == {
        "type": "media", "enabled": 1, "playOnce": 0, "mediaName": "fk_a.mp4",
        "videoOut": "--Default--",
    }  # fmt: skip
    assert get_preset("pi3-1080p30") is PRESETS["pi3-1080p30"]
    assert default_preset_id() == "pi3-1080p30"


def test_invalid_json_is_reported(data: Path) -> None:
    for bad in ("{not json", "[1, 2]"):
        _write(data, bad)
        with pytest.raises(FkError) as exc:
            config.load_overrides()
        assert exc.value.code == "BAD_REQUEST" and "config.json" in exc.value.message


def test_fpp_overrides_reach_the_playlist_entry(data: Path) -> None:
    _write(
        data,
        {
            "fpp": {
                "video_dir": "media", "sequences_dir": "seq", "video_out": "HDMI-A-1",
                "entry_type_media": "video", "entry_type_both": "combo",
            }
        },
    )  # fmt: skip
    assert (video_dir(), sequences_dir()) == ("media", "seq")
    assert media_entry("fk_a.mp4")["type"] == "video"
    both = media_entry("fk_a.mp4", "show.fseq")
    assert (both["type"], both["videoOut"], both["sequenceName"]) == (
        "combo",
        "HDMI-A-1",
        "show.fseq",
    )


def test_publish_uploads_to_the_overridden_directory(
    data: Path, fake_fpp: FakeFpp, tmp_path: Path
) -> None:
    _write(data, {"fpp": {"video_dir": "media"}})
    mp4 = tmp_path / "render.mp4"
    mp4.write_bytes(b"\x00" * 4096)
    publish_render(fake_fpp.host, mp4, media_name="show.mp4", playlist_name="P", start=False)
    assert (fake_fpp.state_dir / "media" / "fk_show.mp4").is_file()
    assert not (fake_fpp.state_dir / "videos").exists()


def test_unknown_and_malformed_settings_are_ignored_with_one_warning(
    data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(
        data,
        {
            "fpp": {"video_dir": "../etc", "video_out": 7, "nonsense": "x"},
            "render": {"presets": {"pi3-720p30": {"video_args": ["-c:v", 3]}}},
        },
    )
    with caplog.at_level(logging.WARNING, logger="facadekit.config"):
        assert config.load_overrides() == {}
        assert config.load_overrides() == {}
    assert video_dir() == "videos"  # a path-like directory name is refused
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == len(set(messages)) == 4


def test_preset_override_reaches_the_encoder_command(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "3M"]
    _write(
        data,
        {
            "render": {
                "presets": {"pi3-720p30": {"video_args": args}},
                "default_preset": "pi3-720p30",
            }
        },
    )
    preset = get_preset("pi3-720p30")
    assert preset.video_args == args
    assert PRESETS["pi3-720p30"].video_args != args  # the built-in default is untouched
    assert get_preset("pi3-1080p30") is PRESETS["pi3-1080p30"]
    assert default_preset_id() == "pi3-720p30"

    seen: list[list[str]] = []

    class _Proc:
        stdin = None

        def kill(self) -> None: ...

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **_kw: Any) -> _Proc:
        seen.append(cmd)
        return _Proc()

    monkeypatch.setattr("facadekit.render.encoder.ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr("facadekit.render.encoder.subprocess.Popen", fake_popen)
    enc = Encoder(
        tmp_path / "o.mp4", width=320, height=180, fps=30, preset=preset,
        audio_path=None, duration_ms=1000,
    )  # fmt: skip
    enc.abort()
    joined = " ".join(seen[0])
    assert "-b:v 3M" in joined and "-crf" not in joined


def test_unknown_default_preset_falls_back(data: Path) -> None:
    _write(data, {"render": {"default_preset": "nope"}})
    assert default_preset_id() == "pi3-1080p30"


def test_doctor_prints_active_overrides(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(data, {"fpp": {"video_dir": "media"}})
    assert main(["doctor", "--no-cameras"]) == 0
    out = capsys.readouterr().out
    assert "config.json overrides: 1" in out and "fpp.video_dir = 'media'" in out
