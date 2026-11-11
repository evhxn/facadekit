"""Small helpers shared by the learning logs (scans.jsonl, realign.jsonl).

* `read_lines` treats a damaged file as damaged history: undecodable bytes never raise.
* `atomic_write` writes a unique temp file in the same directory and renames it over the log.
* `file_lock` is a best-effort cross-process lock (an exclusive lock file, standard library
  only, same on Windows, macOS and Linux). A lock that cannot be taken in time, or that is
  left behind by a crashed process, is ignored after a bounded wait: learning must never block
  a scan. In that rare case two writers can still lose one record.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

LOCK_TIMEOUT_S = 2.0  # how long to wait for another process before going ahead anyway
LOCK_STALE_S = 10.0  # a lock file older than this belongs to a crashed process


def read_lines(path: Path) -> list[str]:
    """Lines of the file; [] when it is missing or unreadable. Bad bytes become U+FFFD."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def atomic_write(path: Path, body: str) -> None:
    """Replace `path` with `body`; the temp file is unique per call and never left behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold `<path>.lock` while the block runs (best effort, see the module docstring)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    held = False
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            held = True
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                    lock.unlink()
                    continue
            except OSError:
                continue  # it vanished between the two calls: try again
        except OSError:
            break  # cannot create lock files here: go without
        if time.monotonic() >= deadline:
            break
        time.sleep(0.005)
    try:
        yield
    finally:
        if held:
            with contextlib.suppress(OSError):
                lock.unlink()
