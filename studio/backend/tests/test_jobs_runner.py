from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from facadekit.errors import NOT_FOUND, FkError
from facadekit.jobs.runner import JobRunner
from facadekit.models.jobs import Job

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]


def wait_for(pred: Callable[[], bool], timeout: float = 5) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.005)
    raise AssertionError("timed out")


DONE = {"succeeded", "failed", "cancelled"}


@pytest.fixture
def updates() -> list[Job]:
    return []


@pytest.fixture
def runner(updates: list[Job]) -> Iterator[JobRunner]:
    r = JobRunner(on_update=updates.append)
    yield r
    r.shutdown()


def test_success(runner: JobRunner) -> None:
    job = runner.submit("render", lambda p, c: {"x": 1})
    assert job.state == "queued"
    wait_for(lambda: runner.get(job.id).state in DONE)
    got = runner.get(job.id)
    assert got.state == "succeeded"
    assert got.result == {"x": 1}
    assert got.progress == 1.0


def test_fkerror_fails(runner: JobRunner) -> None:
    def fn(p: Progress, c: Cancel) -> dict[str, Any]:
        raise FkError("SCAN_FAILED", "m")

    job = runner.submit("calibration", fn)
    wait_for(lambda: runner.get(job.id).state in DONE)
    got = runner.get(job.id)
    assert got.state == "failed"
    assert got.error is not None
    assert got.error.code == "SCAN_FAILED"
    assert got.error.message == "m"


def test_other_exception_internal(runner: JobRunner) -> None:
    def fn(p: Progress, c: Cancel) -> dict[str, Any]:
        raise ValueError("boom")

    job = runner.submit("publish", fn)
    wait_for(lambda: runner.get(job.id).state in DONE)
    got = runner.get(job.id)
    assert got.state == "failed"
    assert got.error is not None
    assert got.error.code == "INTERNAL"
    assert got.error.message == "boom"


def test_cancel_running(runner: JobRunner) -> None:
    def fn(p: Progress, cancel: Cancel) -> dict[str, Any]:
        while not cancel():
            time.sleep(0.01)
        raise FkError("CANCELLED", "")

    job = runner.submit("render", fn)
    wait_for(lambda: runner.get(job.id).state == "running")
    runner.cancel(job.id)
    wait_for(lambda: runner.get(job.id).state in DONE)
    assert runner.get(job.id).state == "cancelled"


def test_sequential(runner: JobRunner) -> None:
    log: list[tuple[str, str, float]] = []

    def make(name: str) -> Callable[[Progress, Cancel], dict[str, Any]]:
        def fn(p: Progress, c: Cancel) -> dict[str, Any]:
            log.append((name, "start", time.monotonic()))
            time.sleep(0.05)
            log.append((name, "end", time.monotonic()))
            return {}

        return fn

    a = runner.submit("render", make("a"))
    b = runner.submit("render", make("b"))
    wait_for(lambda: runner.get(b.id).state in DONE)
    assert runner.get(a.id).state == "succeeded"
    times = {(n, k): t for n, k, t in log}
    assert times[("b", "start")] >= times[("a", "end")]


def test_cancel_queued_never_runs(runner: JobRunner) -> None:
    gate = threading.Event()
    called: list[str] = []

    def first(p: Progress, c: Cancel) -> dict[str, Any]:
        gate.wait(5)
        return {}

    def second(p: Progress, c: Cancel) -> dict[str, Any]:
        called.append("second")
        return {}

    a = runner.submit("render", first)
    b = runner.submit("render", second)
    wait_for(lambda: runner.get(a.id).state == "running")
    assert runner.cancel(b.id).state == "cancelled"
    gate.set()
    wait_for(lambda: runner.get(a.id).state in DONE)
    runner.submit("render", lambda p, c: {})  # flush the queue past b
    time.sleep(0.1)
    assert called == []
    assert runner.get(b.id).state == "cancelled"


def test_cancel_finished_noop(runner: JobRunner) -> None:
    job = runner.submit("render", lambda p, c: {"x": 1})
    wait_for(lambda: runner.get(job.id).state in DONE)
    assert runner.cancel(job.id).state == "succeeded"


def test_update_order_and_rate_limit(runner: JobRunner, updates: list[Job]) -> None:
    def fn(progress: Progress, c: Cancel) -> dict[str, Any]:
        for i in range(1000):
            progress(i / 1000, "x")
        return {}

    job = runner.submit("render", fn)
    wait_for(lambda: runner.get(job.id).state in DONE)
    wait_for(lambda: updates[-1].state == "succeeded")
    mine = [u for u in updates if u.id == job.id]
    assert mine[0].state == "queued"
    assert mine[1].state == "running"
    assert mine[-1].state == "succeeded"
    assert len(mine) < 20


def test_not_found(runner: JobRunner) -> None:
    with pytest.raises(FkError) as ei:
        runner.get("job_missing")
    assert ei.value.code == NOT_FOUND
    with pytest.raises(FkError):
        runner.cancel("job_missing")


def test_get_returns_copy(runner: JobRunner) -> None:
    job = runner.submit("render", lambda p, c: {})
    wait_for(lambda: runner.get(job.id).state in DONE)
    runner.get(job.id).message = "mutated"
    assert runner.get(job.id).message == ""


def test_on_update_raising_is_survived() -> None:
    def bad(job: Job) -> None:
        raise RuntimeError("cb")

    r = JobRunner(on_update=bad)
    try:
        job = r.submit("render", lambda p, c: {"ok": True})
        wait_for(lambda: r.get(job.id).state in DONE)
        assert r.get(job.id).state == "succeeded"
    finally:
        r.shutdown()


def test_retention(runner: JobRunner) -> None:
    ids = [runner.submit("render", lambda p, c: {}).id for _ in range(60)]
    wait_for(lambda: runner.get(ids[-1]).state in DONE)
    with pytest.raises(FkError):
        runner.get(ids[0])
    assert runner.get(ids[-1]).state == "succeeded"
