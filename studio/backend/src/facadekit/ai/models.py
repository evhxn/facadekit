from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable, Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import httpx

from facadekit.config import models_dir
from facadekit.errors import INTERNAL, MODEL_DOWNLOAD_FAILED, NOT_FOUND, FkError

REGISTRY_FILE = "model_registry.json"
REGISTRY_ENV = "FACADEKIT_MODEL_REGISTRY"
PLACEHOLDER_PREFIX = "TO_BE_FILLED"
CHUNK_SIZE = 64 * 1024
HTTP_TIMEOUT_S = 30.0

# One lock per model name so two callers never download the same file at once.
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(name: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(name, threading.Lock())


def registry() -> dict[str, dict[str, Any]]:
    """Return the model registry.

    The bundled ``model_registry.json`` is used unless env ``FACADEKIT_MODEL_REGISTRY``
    names a replacement file (for tests and for users who mirror models).
    """
    override = os.environ.get(REGISTRY_ENV)
    try:
        if override:
            text = Path(override).expanduser().read_text(encoding="utf-8")
        else:
            text = (
                resources.files("facadekit.ai").joinpath(REGISTRY_FILE).read_text(encoding="utf-8")
            )
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        raise FkError(INTERNAL, "Model registry could not be read.") from exc
    if not isinstance(data, dict):
        raise FkError(INTERNAL, "Model registry must be a JSON object.")
    entries: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            raise FkError(INTERNAL, f"Model registry entry {key} must be a JSON object.")
        entries[str(key)] = value
    return entries


def is_published(name: str) -> bool:
    """True when registry entry ``name`` exists and has a real checksum (not a placeholder)."""
    entry = registry().get(name)
    if entry is None:
        return False
    return not str(entry.get("sha256", "")).upper().startswith(PLACEHOLDER_PREFIX.upper())


def ensure_model(name: str, progress: Callable[[float, str], None] | None = None) -> Path:
    """Return the local path of model ``name``, downloading and verifying it if needed.

    A file already in ``models_dir()`` whose SHA-256 matches is returned without any
    request. Otherwise the file is fetched to ``<filename>.part``, verified, and moved
    into place. Failures raise ``FkError`` and leave no partial file behind.
    """
    entry = _entry(name)
    filename = str(entry.get("filename", ""))
    url = str(entry.get("url", ""))
    expected = str(entry.get("sha256", "")).lower()
    size_hint = _int_or_zero(entry.get("size_bytes", 0))

    if Path(filename).name != filename or filename in ("", ".", ".."):
        raise FkError(INTERNAL, f"Model registry filename for {name} is invalid.")
    if expected.upper().startswith(PLACEHOLDER_PREFIX.upper()):
        raise FkError(MODEL_DOWNLOAD_FAILED, f"Model {name} is not published yet.")

    dest = models_dir() / filename
    part = dest.with_name(f"{filename}.part")
    with _lock_for(name):
        if dest.is_file() and _sha256_file(dest) == expected:
            return dest
        try:
            actual = _fetch(name, url, part, size_hint, progress)
            if actual != expected:
                raise FkError(MODEL_DOWNLOAD_FAILED, f"Checksum mismatch for {name}")
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    return dest


def _entry(name: str) -> dict[str, Any]:
    entries = registry()
    if name not in entries:
        raise FkError(NOT_FOUND, f"Unknown model: {name}")
    return entries[name]


def _network_error(name: str) -> FkError:
    return FkError(
        MODEL_DOWNLOAD_FAILED,
        f"Could not download {name}. Check your internet connection.",
    )


def _fetch(
    name: str,
    url: str,
    part: Path,
    size_hint: int,
    progress: Callable[[float, str], None] | None,
) -> str:
    """Write the file at ``url`` to ``part`` and return its SHA-256 hex digest."""
    scheme = urlparse(url).scheme
    if scheme == "https":
        try:
            return _download_https(name, url, part, size_hint, progress)
        except (httpx.HTTPError, OSError) as exc:
            raise _network_error(name) from exc
    if scheme == "file" and os.environ.get(REGISTRY_ENV):
        try:
            return _copy_local(name, url, part, size_hint, progress)
        except OSError as exc:
            raise _network_error(name) from exc
    raise FkError(MODEL_DOWNLOAD_FAILED, f"Model {name} must be served over https.")


def _download_https(
    name: str,
    url: str,
    part: Path,
    size_hint: int,
    progress: Callable[[float, str], None] | None,
) -> str:
    with httpx.stream("GET", url, follow_redirects=True, timeout=HTTP_TIMEOUT_S) as response:
        if response.status_code != 200:
            raise _network_error(name)
        total = _int_or_zero(response.headers.get("content-length")) or size_hint
        return _write_chunks(response.iter_bytes(CHUNK_SIZE), part, name, total, progress)


def _copy_local(
    name: str,
    url: str,
    part: Path,
    size_hint: int,
    progress: Callable[[float, str], None] | None,
) -> str:
    source = Path(url2pathname(urlparse(url).path))
    total = source.stat().st_size if source.is_file() else size_hint
    return _write_chunks(_file_chunks(source), part, name, total, progress)


def _file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        yield from iter(lambda: handle.read(CHUNK_SIZE), b"")


def _write_chunks(
    chunks: Iterable[bytes],
    part: Path,
    name: str,
    total: int,
    progress: Callable[[float, str], None] | None,
) -> str:
    hasher = hashlib.sha256()
    done = 0
    with part.open("wb") as out:
        for chunk in chunks:
            out.write(chunk)
            hasher.update(chunk)
            done += len(chunk)
            if progress is not None and total > 0:
                progress(min(done / total, 1.0), f"Downloading {name}")
    return hasher.hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _int_or_zero(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0
