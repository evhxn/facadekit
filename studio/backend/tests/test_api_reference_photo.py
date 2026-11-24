from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi.testclient import TestClient

from facadekit.api import routes_reference as ref

API = "/api/v1"


def _project(client: TestClient, projector: dict[str, Any] | None = None) -> str:
    body: dict[str, Any] = {"name": "House"}
    if projector is not None:
        body["projector"] = projector
    resp = client.post(f"{API}/projects", json=body)
    assert resp.status_code == 201, resp.text
    pid: str = resp.json()["id"]
    return pid


def _picture(w: int = 400, h: int = 300) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    img[:, : w // 2] = (0, 0, 255)  # red left half (BGR)
    img[:, w // 2 :] = (255, 0, 0)  # blue right half
    return img


def _encode(ext: str, img: np.ndarray | None = None) -> bytes:
    ok, buf = cv2.imencode(ext, _picture() if img is None else img)
    assert ok
    return bytes(buf.tobytes())


def _post(
    client: TestClient, pid: str, data: bytes, name: str = "house.jpg", mime: str = "image/jpeg"
) -> Any:
    return client.post(f"{API}/projects/{pid}/reference-photo", files={"file": (name, data, mime)})


def _dir(client: TestClient, pid: str) -> Path:
    store = client.app.state.fk.store  # type: ignore[attr-defined]
    return Path(store.project_dir(pid))


def test_no_photo_until_one_is_uploaded(client: TestClient) -> None:
    pid = _project(client)
    assert client.get(f"{API}/projects/{pid}/reference-photo").status_code == 404
    assert client.get(f"{API}/projects/{pid}/reference-photo/view.jpg").status_code == 404
    assert client.delete(f"{API}/projects/{pid}/reference-photo").status_code == 204


def test_upload_stores_a_jpeg_inside_the_project_and_leaves_project_json_alone(
    client: TestClient,
) -> None:
    pid = _project(client)
    before = client.get(f"{API}/projects/{pid}").json()
    for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")):
        resp = _post(client, pid, _encode(ext), f"photo{ext}", mime)
        assert resp.status_code == 201, resp.text
        info = resp.json()
        assert (info["width"], info["height"]) == (400, 300)
        assert info["size_bytes"] > 0
    stored = _dir(client, pid) / "reference" / "photo.jpg"
    assert stored.read_bytes()[:3] == b"\xff\xd8\xff"  # re-encoded: always a JPEG
    assert sorted(p.name for p in stored.parent.iterdir()) == ["photo.jpg"]
    assert client.get(f"{API}/projects/{pid}").json() == before
    assert client.get(f"{API}/projects/{pid}/reference-photo").json()["width"] == 400


def test_file_name_is_never_trusted(client: TestClient) -> None:
    pid = _project(client)
    # Wrong extension and a path in the name: the bytes decide, the name is not used.
    resp = _post(client, pid, _encode(".png"), "../../../evil.html", "text/html")
    assert resp.status_code == 201
    names = sorted(
        p.relative_to(_dir(client, pid)).as_posix() for p in _dir(client, pid).rglob("*")
    )
    assert "reference/photo.jpg" in names
    assert not any("evil" in n for n in names)
    assert not (_dir(client, pid).parent / "evil.html").exists()
    # A real name with a picture extension on something that is not a picture.
    for payload in (b"<html><script>alert(1)</script></html>", b"MZ" + b"\0" * 500, b""):
        bad = _post(client, pid, payload, "house.jpg", "image/jpeg")
        assert bad.status_code == 422, bad.text
        assert bad.json()["error"]["code"] == "BAD_REQUEST"


def test_truncated_or_broken_image_is_refused(client: TestClient) -> None:
    pid = _project(client)
    good = _encode(".jpg")
    bad = _post(client, pid, good[:40] + b"\0" * 200)  # header kept, data damaged
    assert bad.status_code == 422
    assert not (_dir(client, pid) / "reference" / "photo.jpg").exists()


def test_a_bad_upload_keeps_the_previous_photo(client: TestClient) -> None:
    pid = _project(client)
    assert _post(client, pid, _encode(".jpg")).status_code == 201
    stored = _dir(client, pid) / "reference" / "photo.jpg"
    first = stored.read_bytes()
    assert _post(client, pid, b"not a picture").status_code == 422
    assert stored.read_bytes() == first


def test_size_cap(client: TestClient, monkeypatch: Any) -> None:
    pid = _project(client)
    monkeypatch.setattr(ref, "MAX_PHOTO_BYTES", 2000)
    big = _encode(".png", np.random.default_rng(1).integers(0, 255, (200, 200, 3), np.uint8))
    assert len(big) > 2000
    resp = _post(client, pid, big, "big.png", "image/png")
    assert resp.status_code == 422
    assert "25 MB" in resp.json()["error"]["message"]


def test_too_many_pixels_is_refused_before_decoding(client: TestClient) -> None:
    pid = _project(client)
    png = bytearray(_encode(".png"))
    png[16:24] = struct.pack(">II", 20000, 20000)  # header now claims 400 megapixels
    resp = _post(client, pid, bytes(png), "huge.png", "image/png")
    assert resp.status_code == 422
    assert "megapixels" in resp.json()["error"]["message"]


def test_tiny_picture_is_refused(client: TestClient) -> None:
    pid = _project(client)
    resp = _post(client, pid, _encode(".png", _picture(20, 20)), "tiny.png", "image/png")
    assert resp.status_code == 422


def test_large_photo_is_scaled_down_when_stored(client: TestClient) -> None:
    pid = _project(client)
    resp = _post(client, pid, _encode(".jpg", _picture(4000, 3000)))
    info = resp.json()
    assert (info["width"], info["height"]) == (2560, 1920)


def test_view_is_letterboxed_into_the_projector_frame(client: TestClient) -> None:
    pid = _project(client, {"width": 1280, "height": 720, "fps": 30})
    assert (
        _post(client, pid, _encode(".png", _picture(300, 300)), "sq.png", "image/png").status_code
        == 201
    )
    resp = client.get(f"{API}/projects/{pid}/reference-photo/view.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    view = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
    assert view.shape[:2] == (720, 1280)
    assert abs(int(view[360, 100].mean()) - 40) < 8  # empty side bar, not stretched
    assert view[360, 560, 2] > 200  # left half of the square photo is red (BGR index 2)
    assert view[360, 720, 0] > 200  # right half is blue


def test_delete_removes_the_photo(client: TestClient) -> None:
    pid = _project(client)
    assert _post(client, pid, _encode(".jpg")).status_code == 201
    assert client.delete(f"{API}/projects/{pid}/reference-photo").status_code == 204
    assert client.get(f"{API}/projects/{pid}/reference-photo").status_code == 404
    assert not (_dir(client, pid) / "reference" / "photo.jpg").exists()


def test_unknown_project(client: TestClient) -> None:
    assert _post(client, "nope", _encode(".jpg")).status_code == 404
    assert client.get(f"{API}/projects/nope/reference-photo").status_code == 404
