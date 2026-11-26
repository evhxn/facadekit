from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from facadekit.api import app as app_module
from facadekit.api.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dist = tmp_path / "webdist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    monkeypatch.setattr(app_module, "WEBDIST", dist)
    return TestClient(create_app(data_dir=tmp_path / "data"), base_url="http://localhost")


def test_index_and_spa_fallback_revalidate(client: TestClient) -> None:
    assert client.get("/").headers["cache-control"] == "no-cache"
    deep = client.get("/some/route")
    assert deep.text == "<html>app</html>"
    assert deep.headers["cache-control"] == "no-cache"
    assert client.get("/favicon.svg").headers["cache-control"] == "no-cache"


def test_hashed_assets_are_immutable(client: TestClient) -> None:
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
