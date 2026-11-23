"""Smart-select engines described by the model registry (spike S-6, docs/16).

An *engine* is one promptable segmentation model made of several registry files (an
encoder, a decoder, and sometimes their external ``.onnx_data`` weights). Registry entries
name their engine and role::

    "efficientsam_ti_encoder": {"filename": ..., "url": ..., "sha256": ..., "size_bytes": ...,
                                "engine": "efficientsam_ti", "role": "encoder",
                                "adapter": "efficientsam", "label": "EfficientSAM-Ti",
                                "licence": "Apache-2.0", "source": "https://..."}

Entries without ``engine``/``role`` fall back to the name: ``<engine>_<role>`` with the
``sam1`` adapter, so registries written before S-6 (``mobilesam_encoder``/``_decoder``)
keep working. Nothing here downloads; ``facadekit.ai.models.ensure_model`` does that, and
only when the user confirms a download.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from facadekit.ai import models

GRABCUT = "grabcut"
DEFAULT_ADAPTER = "sam1"
_ROLE_SUFFIXES = ("encoder_data", "decoder_data", "encoder", "decoder")


@dataclass
class EngineFile:
    name: str  # registry key
    role: str
    url: str
    size_bytes: int
    published: bool
    installed: bool


@dataclass
class EngineInfo:
    id: str
    label: str
    adapter: str
    licence: str
    source: str
    files: list[EngineFile] = field(default_factory=list)
    order: int = 0

    @property
    def published(self) -> bool:
        return bool(self.files) and all(f.published for f in self.files)

    @property
    def installed(self) -> bool:
        return bool(self.files) and all(f.installed for f in self.files)

    @property
    def download_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files if not f.installed)

    def roles(self) -> set[str]:
        return {f.role for f in self.files}


def _split_name(key: str) -> tuple[str, str] | None:
    for role in _ROLE_SUFFIXES:
        suffix = f"_{role}"
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)], role
    return None


def _engine_and_role(key: str, entry: dict[str, Any]) -> tuple[str, str] | None:
    engine = entry.get("engine")
    role = entry.get("role")
    if isinstance(engine, str) and isinstance(role, str) and engine and role:
        return engine, role
    return _split_name(key)


def list_engines(*, check_files: bool = True) -> list[EngineInfo]:
    """All model engines in the registry, in registry order (the first is preferred).

    ``check_files=False`` skips hashing local files (``installed`` is then always False).
    """
    found: dict[str, EngineInfo] = {}
    for index, (key, entry) in enumerate(models.registry().items()):
        split = _engine_and_role(key, entry)
        if split is None:
            continue
        engine_id, role = split
        info = found.get(engine_id)
        if info is None:
            info = EngineInfo(
                id=engine_id,
                label=engine_id,
                adapter=DEFAULT_ADAPTER,
                licence="",
                source="",
                order=index,
            )
            found[engine_id] = info
        for attr in ("label", "adapter", "licence", "source"):
            value = entry.get(attr)
            if isinstance(value, str) and value:
                setattr(info, attr, value)
        published = models.is_published(key)
        installed = check_files and published and models.local_model(key) is not None
        info.files.append(
            EngineFile(
                name=key,
                role=role,
                url=str(entry.get("url", "")),
                size_bytes=models._int_or_zero(entry.get("size_bytes", 0)),
                published=published,
                installed=installed,
            )
        )
    return sorted(found.values(), key=lambda e: e.order)


def get_engine(engine_id: str, *, check_files: bool = True) -> EngineInfo | None:
    return next((e for e in list_engines(check_files=check_files) if e.id == engine_id), None)


def onnxruntime_available() -> bool:
    # find_spec only locates the module; it does not import it.
    return importlib.util.find_spec("onnxruntime") is not None


def usable_engines() -> list[str]:
    """Engines that can run once their files are present: published, and onnxruntime exists.

    This is what ``GET /ai/capabilities`` lists (followed by ``grabcut``).
    """
    if not onnxruntime_available():
        return []
    return [e.id for e in list_engines(check_files=False) if e.published]


def auto_engine() -> str:
    """The engine ``prefer="auto"`` uses: the first installed model engine, else GrabCut.

    Installed means every file is on disk with the registry checksum. Never downloads.
    """
    if not onnxruntime_available():
        return GRABCUT
    for info in list_engines():
        if info.published and info.installed:
            return info.id
    return GRABCUT


def host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def engine_status(info: EngineInfo) -> dict[str, Any]:
    """JSON shape used by ``GET /ai/segment/engines``."""
    return {
        "id": info.id,
        "label": info.label,
        "adapter": info.adapter,
        "licence": info.licence,
        "source": info.source,
        "published": info.published,
        "installed": info.installed,
        "download_bytes": info.download_bytes,
        "files": [
            {
                "name": f.name,
                "role": f.role,
                "url": f.url,
                "host": host_of(f.url),
                "size_bytes": f.size_bytes,
                "installed": f.installed,
            }
            for f in info.files
        ],
    }
