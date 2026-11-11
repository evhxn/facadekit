"""Learning from Quick re-align: remember how far surfaces usually move, warn on outliers.

Signal. The backend never sees the user dragging a corner after a re-align (the editor saves
whole projects), so the real signal is what re-align measures itself: `max_shift_px`, the
largest distance any surface corner moved when the estimated drift was applied. Only applied
re-aligns are logged (a preview is not a decision). `reproj_error_px` and `inliers` are stored
for the record but do not drive anything.

Use. The median `max_shift_px` of the last `WINDOW` applied re-aligns sets a warning threshold:
`clamp(WARN_FACTOR * median, WARN_DEFAULT_SHIFT_PX, WARN_CAP_SHIFT_PX)`. It never drops below
the default and never exceeds 4x the default. With fewer than `MIN_SAMPLES` records, or with
learning off, no warning is produced, so an empty log behaves exactly as before. The hard gate
`realign.MAX_REPROJ_ERROR_PX` is never touched: a warning is advice, a failed gate is a refusal.

Storage: `<data dir>/learning/realign.jsonl`, newest 500 kept, same switch as scan memory
(`FACADEKIT_LEARN=0`).
"""

from __future__ import annotations

import statistics
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from facadekit.calib.jsonl_store import atomic_write, file_lock, read_lines
from facadekit.calib.learn import MAX_RECORDS, learning_enabled
from facadekit.ids import new_id
from facadekit.models.jobs import utc_now_iso

WINDOW = 20  # most recent corrections that count
MIN_SAMPLES = 5  # below this the log is ignored
WARN_DEFAULT_SHIFT_PX = 40.0  # lower bound of the warning threshold
WARN_FACTOR = 2.0  # warn when a shift exceeds this many times the usual one
WARN_CAP_SHIFT_PX = 160.0  # upper bound: 4 x the default


class AlignRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    at: str
    project_id: str
    max_shift_px: float
    reproj_error_px: float
    inliers: int


def warn_threshold(records: list[AlignRecord]) -> float | None:
    """Shift (px) above which a re-align deserves a warning; None until there is enough history."""
    recent = [r.max_shift_px for r in records[-WINDOW:]]
    if len(recent) < MIN_SAMPLES:
        return None
    raw = WARN_FACTOR * statistics.median(recent)
    return min(WARN_CAP_SHIFT_PX, max(WARN_DEFAULT_SHIFT_PX, raw))


def warning_for(records: list[AlignRecord], max_shift_px: float) -> str | None:
    limit = warn_threshold(records)
    if limit is None or max_shift_px <= limit:
        return None
    usual = statistics.median(r.max_shift_px for r in records[-WINDOW:])
    return (
        f"Surfaces moved by up to {max_shift_px:.0f} px, much more than your usual re-align "
        f"({usual:.0f} px). Check the result before you apply it."
    )


class AlignMemory:
    """Append-only JSONL log of applied re-aligns. Safe to use from several threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def records(self) -> list[AlignRecord]:
        with self._lock:
            return self._read()

    def _read(self) -> list[AlignRecord]:
        out: list[AlignRecord] = []
        for line in read_lines(self.path):
            if not line.strip():
                continue
            try:
                out.append(AlignRecord.model_validate_json(line))
            except ValidationError:
                continue
        return out

    def append(self, record: AlignRecord) -> None:
        with self._lock, file_lock(self.path):
            records = self._read()
            records.append(record)
            body = "".join(r.model_dump_json() + "\n" for r in records[-MAX_RECORDS:])
            atomic_write(self.path, body)

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)

    def record_applied(
        self, project_id: str, max_shift_px: float, reproj_error_px: float, inliers: int
    ) -> None:
        """Log one applied re-align. Never raises: learning must not break a re-align."""
        if not learning_enabled():
            return
        record = AlignRecord(
            id=new_id("aln"),
            at=utc_now_iso(),
            project_id=project_id,
            max_shift_px=max_shift_px,
            reproj_error_px=reproj_error_px,
            inliers=inliers,
        )
        try:
            self.append(record)
        except OSError:
            pass

    def warning(self, max_shift_px: float) -> str | None:
        if not learning_enabled():
            return None
        return warning_for(self.records(), max_shift_px)


class AlignSummary(BaseModel):
    enabled: bool
    corrections: int
    median_shift_px: float | None
    warn_threshold_px: float | None


def summarize_align(records: list[AlignRecord], enabled: bool) -> AlignSummary:
    recent = [r.max_shift_px for r in records[-WINDOW:]]
    return AlignSummary(
        enabled=enabled,
        corrections=len(records),
        median_shift_px=statistics.median(recent) if recent else None,
        warn_threshold_px=warn_threshold(records) if enabled else None,
    )
