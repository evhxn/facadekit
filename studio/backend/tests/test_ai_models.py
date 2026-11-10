from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from facadekit.ai import models
from facadekit.ai.models import ensure_model, registry
from facadekit.errors import MODEL_DOWNLOAD_FAILED, NOT_FOUND, FkError

PAYLOAD = b"fake onnx model bytes\n" * 2000


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FACADEKIT_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("FACADEKIT_MODEL_REGISTRY", raising=False)
    return tmp_path / "data" / "models"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "demo.onnx"
    src.parent.mkdir()
    src.write_bytes(PAYLOAD)
    return src


def _use_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: dict[str, dict[str, Any]]
) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setenv("FACADEKIT_MODEL_REGISTRY", str(path))


def _entry(url: str, sha: str, filename: str = "demo.onnx") -> dict[str, Any]:
    return {"filename": filename, "url": url, "sha256": sha, "size_bytes": len(PAYLOAD)}


def _mock_https(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Route httpx.stream through a MockTransport so no socket is ever opened."""

    def stream(method: str, url: str, **kwargs: Any) -> Any:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, **kwargs)

    monkeypatch.setattr(models.httpx, "stream", stream)


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def stream(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no network request expected")

    monkeypatch.setattr(models.httpx, "stream", stream)


def test_first_call_downloads_verifies_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path, data_home: Path
) -> None:
    _use_registry(tmp_path, monkeypatch, {"demo": _entry(source.as_uri(), _sha(PAYLOAD))})
    seen: list[tuple[float, str]] = []

    path = ensure_model("demo", progress=lambda frac, msg: seen.append((frac, msg)))

    assert path == data_home / "demo.onnx"
    assert path.read_bytes() == PAYLOAD
    assert not (data_home / "demo.onnx.part").exists()
    assert seen and seen[-1][0] == 1.0
    assert all(0.0 <= frac <= 1.0 for frac, _ in seen)
    assert seen[0][1] == "Downloading demo"


def test_good_file_is_never_downloaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path
) -> None:
    _use_registry(tmp_path, monkeypatch, {"demo": _entry(source.as_uri(), _sha(PAYLOAD))})
    first = ensure_model("demo")

    source.unlink()  # a second download would now fail
    second = ensure_model("demo")

    assert second == first
    assert second.read_bytes() == PAYLOAD


def test_corrupt_local_file_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path, data_home: Path
) -> None:
    _use_registry(tmp_path, monkeypatch, {"demo": _entry(source.as_uri(), _sha(PAYLOAD))})
    data_home.mkdir(parents=True)
    (data_home / "demo.onnx").write_bytes(b"truncated")

    path = ensure_model("demo")

    assert path.read_bytes() == PAYLOAD


def test_wrong_hash_raises_and_leaves_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path, data_home: Path
) -> None:
    _use_registry(tmp_path, monkeypatch, {"demo": _entry(source.as_uri(), _sha(b"other"))})

    with pytest.raises(FkError) as info:
        ensure_model("demo")

    assert info.value.code == MODEL_DOWNLOAD_FAILED
    assert "Checksum mismatch" in info.value.message
    assert not (data_home / "demo.onnx").exists()
    assert not (data_home / "demo.onnx.part").exists()


def test_unknown_name_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path
) -> None:
    _use_registry(tmp_path, monkeypatch, {"demo": _entry(source.as_uri(), _sha(PAYLOAD))})

    with pytest.raises(FkError) as info:
        ensure_model("nope")

    assert info.value.code == NOT_FOUND


def test_placeholder_hash_is_not_published_and_makes_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_registry(
        tmp_path,
        monkeypatch,
        {"demo": _entry("https://example.invalid/demo.onnx", "TO_BE_FILLED_BY_SPIKE_S6")},
    )
    _forbid_network(monkeypatch)

    with pytest.raises(FkError) as info:
        ensure_model("demo")

    assert info.value.code == MODEL_DOWNLOAD_FAILED
    assert "not published" in info.value.message


def test_unreachable_host_raises_download_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_home: Path
) -> None:
    _use_registry(
        tmp_path, monkeypatch, {"demo": _entry("https://example.invalid/demo.onnx", _sha(PAYLOAD))}
    )

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_https(monkeypatch, unreachable)

    with pytest.raises(FkError) as info:
        ensure_model("demo")

    assert info.value.code == MODEL_DOWNLOAD_FAILED
    assert "internet connection" in info.value.message
    assert not (data_home / "demo.onnx.part").exists()


def test_non_200_status_raises_download_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_home: Path
) -> None:
    _use_registry(
        tmp_path, monkeypatch, {"demo": _entry("https://example.invalid/demo.onnx", _sha(PAYLOAD))}
    )
    _mock_https(monkeypatch, lambda request: httpx.Response(404))

    with pytest.raises(FkError) as info:
        ensure_model("demo")

    assert info.value.code == MODEL_DOWNLOAD_FAILED
    assert not (data_home / "demo.onnx").exists()
    assert not (data_home / "demo.onnx.part").exists()


def test_https_download_writes_verified_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_home: Path
) -> None:
    _use_registry(
        tmp_path, monkeypatch, {"demo": _entry("https://example.invalid/demo.onnx", _sha(PAYLOAD))}
    )
    requested: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=PAYLOAD, headers={"content-length": str(len(PAYLOAD))})

    _mock_https(monkeypatch, serve)
    seen: list[float] = []

    path = ensure_model("demo", progress=lambda frac, _msg: seen.append(frac))

    assert requested == ["https://example.invalid/demo.onnx"]
    assert path.read_bytes() == PAYLOAD
    assert seen and seen[-1] == 1.0


def test_plain_http_url_is_rejected_without_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_registry(
        tmp_path, monkeypatch, {"demo": _entry("http://example.invalid/demo.onnx", _sha(PAYLOAD))}
    )
    _forbid_network(monkeypatch)

    with pytest.raises(FkError) as info:
        ensure_model("demo")

    assert info.value.code == MODEL_DOWNLOAD_FAILED


def test_bundled_registry_lists_mobilesam_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACADEKIT_MODEL_REGISTRY", raising=False)

    entries = registry()

    assert {"mobilesam_encoder", "mobilesam_decoder"} <= set(entries)
    for name in ("mobilesam_encoder", "mobilesam_decoder"):
        assert entries[name]["sha256"].startswith("TO_BE_FILLED")
        assert str(entries[name]["url"]).startswith("https://")
