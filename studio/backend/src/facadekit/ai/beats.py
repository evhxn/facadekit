from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from facadekit.errors import AI_UNAVAILABLE, FkError
from facadekit.media.ffmpeg import run_ffmpeg
from facadekit.models.project import Marker

ONSET_BEAT_GUARD_MS = 40
MAX_ANALYSIS_SECONDS = 900
ANALYSIS_SAMPLE_RATE = 22050


def analyze_beats(
    audio_path: Path, progress: Callable[[float, str], None] | None = None
) -> list[Marker]:
    """Detect beat and onset markers (kinds "beat" and "onset"), sorted by time."""
    try:
        import librosa
    except ImportError as exc:
        raise FkError(
            AI_UNAVAILABLE,
            "Beat detection needs the optional 'ai' components. "
            "Install with: pipx inject facadekit librosa",
        ) from exc

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    with tempfile.TemporaryDirectory(prefix="fk_beats_") as tmp:
        wav = Path(tmp) / "audio.wav"
        run_ffmpeg(
            [
                "-y",
                "-i",
                str(audio_path),
                "-t",
                str(MAX_ANALYSIS_SECONDS),
                "-ac",
                "1",
                "-ar",
                str(ANALYSIS_SAMPLE_RATE),
                str(wav),
            ]
        )
        y, sr = librosa.load(str(wav), sr=None)
    report(0.1, "Audio decoded")

    _tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times: Any = librosa.frames_to_time(frames, sr=sr)
    beats_ms = [int(round(float(t) * 1000)) for t in beat_times]
    report(0.6, "Beats found")

    onset_times: Any = librosa.onset.onset_detect(y=y, sr=sr, backtrack=False, units="time")
    onsets_ms = [int(round(float(t) * 1000)) for t in onset_times]
    report(0.9, "Onsets found")

    onsets_ms = [o for o in onsets_ms if all(abs(o - b) > ONSET_BEAT_GUARD_MS for b in beats_ms)]
    markers = [Marker(t_ms=t, kind="beat") for t in beats_ms]
    markers += [Marker(t_ms=t, kind="onset") for t in onsets_ms]
    markers.sort(key=lambda m: m.t_ms)
    return markers
