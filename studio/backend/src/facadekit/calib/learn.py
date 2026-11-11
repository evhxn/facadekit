"""Scan memory: learn which scan strategy works for this camera and lighting.

Every scan attempt is appended to a small local log (``<data dir>/learning/scans.jsonl``).
Before the next scan the advisor scores each strategy from that log and picks the best one,
so a failed or rejected scan makes the wizard try something different next time, and a
strategy that worked is remembered for the same camera and similar lighting.

This is a contextual bandit with a Beta posterior per strategy and a greedy policy over
optimistic priors. It is deterministic on purpose: the same history always gives the same
choice. Nothing leaves the computer.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from facadekit.ids import new_id
from facadekit.models.calibration import Calibration, CalibSettings
from facadekit.models.jobs import utc_now_iso

Bucket = Literal["low", "medium", "high"]
Verdict = Literal["good", "bad"]

MAX_RECORDS = 500  # the log is trimmed to the newest records when it is rewritten
MISTAKE_WEIGHT = 2.0  # a failed or rejected scan counts double: mistakes teach more
SAME_BUCKET_OTHER_CAMERA = 0.5
SAME_CAMERA_OTHER_BUCKET = 0.25
SIBLING_SHARE = 0.5  # how much of a failed scan also counts against same-or-finer-block strategies
TIME_COST_PER_FRAME = 0.01  # reward given up for each extra averaged frame


@dataclass(frozen=True)
class Strategy:
    id: str
    label: str
    skip_lsb: int
    average_frames: int
    min_conf: float
    prior: tuple[float, float]  # Beta(a, b): what we believe before any scan

    def apply(self, settings: CalibSettings) -> CalibSettings:
        return settings.model_copy(
            update={
                "skip_lsb": self.skip_lsb,
                "average_frames": self.average_frames,
                "min_conf": self.min_conf,
            }
        )


# Order matters: ties go to the earlier strategy. "standard" equals the fixed defaults and has
# the strongest prior, so with an empty log the wizard behaves exactly as it did before.
STRATEGIES: tuple[Strategy, ...] = (
    Strategy("standard", "Standard", 2, 3, 0.10, (4.0, 1.0)),
    Strategy("low_light", "Low light", 2, 6, 0.06, (2.0, 1.0)),
    Strategy("soft_focus", "Soft focus", 3, 3, 0.10, (2.0, 1.0)),
    Strategy("low_light_soft", "Low light, soft focus", 3, 6, 0.06, (1.5, 1.0)),
    Strategy("very_soft", "Very soft focus", 4, 3, 0.10, (1.5, 1.0)),
)
_BY_ID = {s.id: s for s in STRATEGIES}


def get_strategy(strategy_id: str) -> Strategy | None:
    return _BY_ID.get(strategy_id)


def contrast_bucket(median_contrast: float) -> Bucket:
    """Lighting context, from the white-minus-black contrast measured before the scan."""
    if median_contrast < 35.0:
        return "low"
    if median_contrast < 80.0:
        return "medium"
    return "high"


class ScanRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    at: str
    camera_id: str
    bucket: Bucket
    median_contrast: float
    lit_fraction: float
    strategy: str
    ok: bool
    error_code: str | None = None
    coverage: float | None = None
    planarity: float | None = None
    calibration_id: str | None = None
    verdict: Verdict | None = None


def reward(record: ScanRecord) -> float:
    """0..1. Failed or rejected scans score 0; otherwise coverage, minus a small time cost."""
    if not record.ok or record.verdict == "bad" or record.coverage is None:
        return 0.0
    quality = min(1.0, max(0.0, (record.coverage - 0.5) / 0.45))
    strategy = _BY_ID.get(record.strategy)
    frames = strategy.average_frames if strategy is not None else 1
    value = quality - TIME_COST_PER_FRAME * (frames - 1)
    if record.verdict == "good":
        value += 0.1
    return min(1.0, max(0.0, value))


def _weight(record: ScanRecord, camera_id: str, bucket: Bucket) -> float:
    same_camera = record.camera_id == camera_id
    same_bucket = record.bucket == bucket
    if same_camera and same_bucket:
        similarity = 1.0
    elif same_bucket:
        similarity = SAME_BUCKET_OTHER_CAMERA
    elif same_camera:
        similarity = SAME_CAMERA_OTHER_BUCKET
    else:
        return 0.0
    mistake = not record.ok or record.verdict == "bad"
    return similarity * (MISTAKE_WEIGHT if mistake else 1.0)


def posterior(
    records: list[ScanRecord], camera_id: str, bucket: Bucket
) -> dict[str, tuple[float, float]]:
    """Beta(a, b) per strategy id for this camera and lighting."""
    out = {s.id: s.prior for s in STRATEGIES}
    for rec in records:
        if rec.strategy not in out:
            continue
        w = _weight(rec, camera_id, bucket)
        if w == 0.0:
            continue
        r = reward(rec)
        a, b = out[rec.strategy]
        out[rec.strategy] = (a + w * r, b + w * (1.0 - r))
        if not rec.ok and rec.bucket != "low":
            # Stripes the camera cannot resolve stay unresolved with more frames or a lower
            # threshold, so a failure also counts (less) against same-or-finer-block strategies.
            # Not in dim light: there noise, not blur, is the likelier cause and averaging helps.
            used = _BY_ID[rec.strategy]
            for other in STRATEGIES:
                if other.id != used.id and other.skip_lsb <= used.skip_lsb:
                    oa, ob = out[other.id]
                    out[other.id] = (oa, ob + SIBLING_SHARE * w)
    return out


def choose(records: list[ScanRecord], camera_id: str, bucket: Bucket) -> Strategy:
    """The strategy with the highest expected reward; ties go to the earlier strategy."""
    post = posterior(records, camera_id, bucket)
    best = STRATEGIES[0]
    best_mean = -1.0
    for s in STRATEGIES:
        a, b = post[s.id]
        mean = a / (a + b)
        if mean > best_mean + 1e-9:
            best, best_mean = s, mean
    return best


class ScanMemory:
    """Append-only JSONL log of scan attempts. Safe to use from several threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def records(self) -> list[ScanRecord]:
        with self._lock:
            return self._read()

    def _read(self) -> list[ScanRecord]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[ScanRecord] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(ScanRecord.model_validate_json(line))
            except ValidationError:
                continue  # a damaged line only loses that one scan
        return out

    def append(self, record: ScanRecord) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")

    def _rewrite(self, records: list[ScanRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        body = "".join(r.model_dump_json() + "\n" for r in records[-MAX_RECORDS:])
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, self.path)

    def set_verdict(self, calibration_id: str, verdict: Verdict) -> bool:
        """Store the user's opinion of a finished scan. False when the scan is not in the log."""
        with self._lock:
            records = self._read()
            found = False
            for rec in records:
                if rec.calibration_id == calibration_id:
                    rec.verdict = verdict
                    found = True
            if found:
                self._rewrite(records)
            return found

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)


class ScanAdvisor:
    """What the calibration pipeline talks to: pick a strategy, then report how it went."""

    def __init__(self, memory: ScanMemory) -> None:
        self.memory = memory

    def choose(self, camera_id: str, pre: dict[str, float]) -> Strategy:
        return choose(self.memory.records(), camera_id, contrast_bucket(pre["median_contrast"]))

    def _record(
        self, camera_id: str, pre: dict[str, float], strategy: Strategy, **fields: object
    ) -> None:
        record = ScanRecord.model_validate(
            {
                "id": new_id("scn"),
                "at": utc_now_iso(),
                "camera_id": camera_id,
                "bucket": contrast_bucket(pre["median_contrast"]),
                "median_contrast": pre["median_contrast"],
                "lit_fraction": pre["lit_fraction"],
                "strategy": strategy.id,
                **fields,
            }
        )
        try:
            self.memory.append(record)
        except OSError:
            pass  # learning must never break a scan

    def succeeded(
        self, camera_id: str, pre: dict[str, float], strategy: Strategy, cal: Calibration
    ) -> None:
        assert cal.quality is not None
        self._record(
            camera_id,
            pre,
            strategy,
            ok=True,
            coverage=cal.quality.coverage,
            planarity=cal.quality.planarity,
            calibration_id=cal.id,
        )

    def failed(
        self, camera_id: str, pre: dict[str, float], strategy: Strategy, error_code: str
    ) -> None:
        self._record(camera_id, pre, strategy, ok=False, error_code=error_code)


class StrategySummary(BaseModel):
    id: str
    label: str
    tries: int
    mistakes: int
    mean_reward: float | None


def summarize(records: list[ScanRecord]) -> list[StrategySummary]:
    out: list[StrategySummary] = []
    for s in STRATEGIES:
        mine = [r for r in records if r.strategy == s.id]
        rewards = [reward(r) for r in mine]
        out.append(
            StrategySummary(
                id=s.id,
                label=s.label,
                tries=len(mine),
                mistakes=sum(1 for r in mine if not r.ok or r.verdict == "bad"),
                mean_reward=sum(rewards) / len(rewards) if rewards else None,
            )
        )
    return out


def learning_enabled() -> bool:
    """FACADEKIT_LEARN=0 turns scan memory off (read on every call)."""
    return os.environ.get("FACADEKIT_LEARN", "1").strip().lower() not in ("0", "false", "no", "off")
