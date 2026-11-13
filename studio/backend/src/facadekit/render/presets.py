from __future__ import annotations

from dataclasses import dataclass, replace

from facadekit.config import load_overrides
from facadekit.errors import UNKNOWN_PRESET, FkError

# Output resolution/bitrate values are unverified defaults (hardware spikes S-1/S-2/S-8 not run).
# Keep them here as data; do not hard-code them elsewhere. They are defaults: `get_preset`
# applies the user's config.json (`render.presets.<id>.video_args`, `render.default_preset`).

DEFAULT_PRESET = "pi3-1080p30"


@dataclass(frozen=True)
class Preset:
    id: str
    width: int | None  # None = projector width
    height: int | None
    video_args: list[str]
    audio_args: list[str]


_AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def _pi3_video_args(maxrate: str, bufsize: str) -> list[str]:
    return [
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "20", "-maxrate", maxrate, "-bufsize", bufsize,
        "-g", "30", "-bf", "2",
    ]  # fmt: skip


PRESETS: dict[str, Preset] = {
    "pi3-1080p30": Preset(
        "pi3-1080p30", None, None, _pi3_video_args("8M", "16M"), list(_AUDIO_ARGS)
    ),
    "pi3-720p30": Preset("pi3-720p30", 1280, 720, _pi3_video_args("5M", "10M"), list(_AUDIO_ARGS)),
    "preview-540p": Preset(
        "preview-540p",
        960,
        540,
        ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "28"],
        list(_AUDIO_ARGS),
    ),
}


def get_preset(preset_id: str) -> Preset:
    """The preset with the user's `video_args` override applied, if there is one."""
    try:
        preset = PRESETS[preset_id]
    except KeyError:
        raise FkError(UNKNOWN_PRESET, f"Unknown preset: {preset_id}") from None
    video_args = load_overrides().get(f"render.presets.{preset_id}.video_args")
    if video_args is not None:
        preset = replace(preset, video_args=list(video_args))
    return preset


def default_preset_id() -> str:
    """Preset used when the caller names none (config key ``render.default_preset``)."""
    chosen = load_overrides().get("render.default_preset")
    return chosen if isinstance(chosen, str) and chosen in PRESETS else DEFAULT_PRESET


def output_size(preset: Preset, projector_w: int, projector_h: int) -> tuple[int, int]:
    if preset.width is None:
        return projector_w // 2 * 2, projector_h // 2 * 2
    width = preset.width
    height = round(width * projector_h / projector_w / 2) * 2
    return width, height
