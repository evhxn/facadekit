from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from facadekit.errors import CANCELLED, INTERNAL, NOT_FOUND, FkError
from facadekit.ids import new_id
from facadekit.models.jobs import Job, JobError, JobKind, utc_now_iso

log = logging.getLogger(__name__)

JobFn = Callable[[Callable[[float, str], None], Callable[[], bool]], dict[str, Any]]

MAX_JOBS = 50
MIN_PROGRESS_INTERVAL_S = 0.1  # at most 10 progress updates per second per job


class JobRunner:
    def __init__(self, on_update: Callable[[Job], None] | None = None) -> None:
        self._on_update = on_update
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._fns: dict[str, JobFn] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._last_emit: dict[str, float] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="job-runner", daemon=True)
        self._thread.start()

    def submit(self, kind: JobKind, fn: JobFn) -> Job:
        job = Job(id=new_id("job"), kind=kind, state="queued", created_at=utc_now_iso())
        with self._lock:
            self._jobs[job.id] = job
            self._fns[job.id] = fn
            self._cancel[job.id] = threading.Event()
            self._trim()
            snapshot = job.model_copy(deep=True)
        self._emit(snapshot)
        self._queue.put(job.id)
        return snapshot

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._lookup(job_id).model_copy(deep=True)

    def recent(self) -> list[Job]:
        """Every job still remembered, oldest first."""
        with self._lock:
            return [job.model_copy(deep=True) for job in self._jobs.values()]

    def cancel(self, job_id: str) -> Job:
        snapshot: Job | None = None
        with self._lock:
            job = self._lookup(job_id)
            if job.state == "queued":
                job.state = "cancelled"
                self._cancel[job_id].set()
                self._fns.pop(job_id, None)
                snapshot = job.model_copy(deep=True)
            elif job.state == "running":
                self._cancel[job_id].set()
            result = job.model_copy(deep=True)
        if snapshot is not None:
            self._emit(snapshot)
        return result

    def shutdown(self) -> None:
        with self._lock:
            for event in self._cancel.values():
                event.set()
        self._queue.put(None)
        self._thread.join(timeout=5)

    # -- internals -------------------------------------------------------

    def _lookup(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise FkError(NOT_FOUND, f"job not found: {job_id}")
        return job

    def _trim(self) -> None:
        # Caller holds the lock. Drop the oldest jobs beyond the retention limit,
        # preferring finished ones so queued/running jobs are not lost.
        while len(self._jobs) > MAX_JOBS:
            victim = next(
                (i for i, j in self._jobs.items() if j.state not in ("queued", "running")),
                None,
            )
            if victim is None:
                break
            for store in (self._jobs, self._fns, self._cancel, self._last_emit):
                store.pop(victim, None)

    def _emit(self, job: Job) -> None:
        if self._on_update is None:
            return
        try:
            self._on_update(job)
        except Exception:
            log.exception("job on_update callback failed")

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            self._run(job_id)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            fn = self._fns.pop(job_id, None)
            if job is None or fn is None or job.state != "queued":
                return
            job.state = "running"
            event = self._cancel[job_id]
            snapshot = job.model_copy(deep=True)
        self._emit(snapshot)
        log.info("job %s (%s) started", job_id, snapshot.kind)
        started = time.monotonic()

        def progress(fraction: float, message: str) -> None:
            fraction = min(1.0, max(0.0, fraction))
            now = time.monotonic()
            with self._lock:
                j = self._jobs.get(job_id)
                if j is None:
                    return
                j.progress = fraction
                j.message = message
                last = self._last_emit.get(job_id)
                if last is not None and now - last < MIN_PROGRESS_INTERVAL_S:
                    return
                self._last_emit[job_id] = now
                snap = j.model_copy(deep=True)
            self._emit(snap)

        state = "succeeded"
        result: dict[str, Any] | None = None
        error: JobError | None = None
        try:
            result = fn(progress, event.is_set)
        except FkError as exc:
            if exc.code == CANCELLED:
                state = "cancelled"
            else:
                state = "failed"
                error = JobError(code=exc.code, message=exc.message)
                log.warning(
                    "job %s (%s) failed: %s: %s",
                    job_id,
                    snapshot.kind,
                    exc.code,
                    exc.message,
                    exc_info=exc if exc.code == INTERNAL else None,
                )
        except Exception as exc:
            log.exception("job %s (%s) crashed", job_id, snapshot.kind)
            state = "failed"
            error = JobError(code=INTERNAL, message=str(exc))

        log.info(
            "job %s (%s) %s after %.1f s", job_id, snapshot.kind, state, time.monotonic() - started
        )
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return
            j.state = state  # type: ignore[assignment]
            if state == "succeeded":
                j.progress = 1.0
                j.result = result
            j.error = error
            final = j.model_copy(deep=True)
            self._trim()
        self._emit(final)
