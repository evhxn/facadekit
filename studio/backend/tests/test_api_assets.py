from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

API = "/api/v1"


def _project(client: TestClient) -> str:
    resp = client.post(f"{API}/projects", json={"name": "Show"})
    pid: str = resp.json()["id"]
    return pid


def _upload(client: TestClient, pid: str, path: Path, name: str | None = None) -> Any:
    with path.open("rb") as handle:
        return client.post(
            f"{API}/projects/{pid}/assets", files={"file": (name or path.name, handle)}
        )


def _media(client: TestClient, pid: str) -> Path:
    store = client.app.state.fk.store  # type: ignore[attr-defined]
    return Path(store.project_dir(pid)) / "media"


def test_upload_serve_thumb(client: TestClient, sample_video: Path, sample_audio: Path) -> None:
    pid = _project(client)
    resp = _upload(client, pid, sample_video)
    assert resp.status_code == 201
    body = resp.json()
    asset = body["asset"]
    assert asset["kind"] == "video"
    assert asset["filename"].startswith("media/ast_")
    assert len(body["project"]["assets"]) == 1
    assert body["project"]["revision"] == 1
    assert (_media(client, pid).parent / asset["filename"]).is_file()

    url = f"{API}/projects/{pid}/assets/{asset['id']}"
    full = client.get(f"{url}/file")
    assert full.status_code == 200
    assert len(full.content) == sample_video.stat().st_size
    part = client.get(f"{url}/file", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100

    thumb = client.get(f"{url}/thumb")
    assert thumb.status_code == 200
    assert thumb.content[:2] == b"\xff\xd8"

    audio = _upload(client, pid, sample_audio).json()["asset"]
    assert audio["kind"] == "audio"
    assert client.get(f"{API}/projects/{pid}/assets/{audio['id']}/thumb").status_code == 404


def test_upload_unsupported(client: TestClient, tmp_path: Path) -> None:
    pid = _project(client)
    bad = tmp_path / "x.mp4"
    bad.write_text("not a video")
    resp = _upload(client, pid, bad)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA"
    assert list(_media(client, pid).iterdir()) == []


def test_delete(client: TestClient, sample_video: Path, sample_image: Path) -> None:
    pid = _project(client)
    unused = _upload(client, pid, sample_image).json()["asset"]
    used = _upload(client, pid, sample_video).json()["asset"]

    project = client.get(f"{API}/projects/{pid}").json()
    project["timeline"]["tracks"] = [
        {
            "id": "trk_1",
            "name": "T",
            "clips": [
                {
                    "id": "clp_1",
                    "asset_id": used["id"],
                    "surface_id": "canvas",
                    "start_ms": 0,
                    "duration_ms": 1000,
                }
            ],
        }
    ]
    assert client.put(f"{API}/projects/{pid}", json=project).status_code == 200

    resp = client.delete(f"{API}/projects/{pid}/assets/{used['id']}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ASSET_IN_USE"

    resp = client.delete(f"{API}/projects/{pid}/assets/{unused['id']}")
    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()["assets"]] == [used["id"]]
    media = _media(client, pid)
    assert not (media.parent / unused["filename"]).exists()
    assert not (media / f"{unused['id']}.thumb.jpg").exists()


def test_unknown_and_malformed_aid(client: TestClient) -> None:
    pid = _project(client)
    for aid in ("ast_00000000", "nope", "ast_ZZZZZZZZ"):
        for suffix in ("file", "thumb"):
            assert client.get(f"{API}/projects/{pid}/assets/{aid}/{suffix}").status_code == 404
        assert client.delete(f"{API}/projects/{pid}/assets/{aid}").status_code == 404


def test_put_project_cannot_point_an_asset_outside_media(
    client: TestClient, tmp_path: Path
) -> None:
    """A saved project (also from a paired phone) must not turn the file routes into
    arbitrary read/delete: asset.filename is joined to the project folder."""
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    pid = client.post(f"{API}/projects", json={"name": "demo"}).json()["id"]
    project = client.get(f"{API}/projects/{pid}").json()
    project["assets"] = [
        {
            "id": "ast_00000001", "kind": "image", "filename": "../../victim.txt",
            "original_name": "x.png", "width": 1, "height": 1, "duration_ms": None, "fps": None,
        }
    ]  # fmt: skip
    resp = client.put(f"{API}/projects/{pid}", json=project)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_PROJECT"
    assert client.get(f"{API}/projects/{pid}/assets/ast_00000001/file").status_code == 404
    assert client.delete(f"{API}/projects/{pid}/assets/ast_00000001").status_code == 404
    assert victim.read_text() == "keep me"
