"""Reference photo for manual mode: a picture of the house or wall to place surfaces on by eye.

It is not measured: nothing here maps the photo to the projector. The photo is stored once per
project as `reference/photo.jpg` (decoded and re-encoded, so what is kept is a plain JPEG whatever
was sent) and shown, letterboxed into the projector frame, as the Surfaces background of a
manual calibration.
"""

from __future__ import annotations

import logging
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from facadekit.api.deps import AppState, get_state
from facadekit.errors import BAD_REQUEST, NOT_FOUND, FkError

log = logging.getLogger(__name__)

router = APIRouter()

State = Annotated[AppState, Depends(get_state)]

CHUNK = 1024 * 1024
MAX_PHOTO_BYTES = (
    25 * 1024 * 1024
)  # a phone photo is 1 to 8 MB; one upload, like the phone video cap
MAX_PHOTO_PIXELS = 80_000_000  # refuse decompression bombs before decoding
MIN_SIDE = 64
STORED_LONG_SIDE = 2560  # larger photos are scaled down when stored
JPEG_QUALITY = 90
PHOTO_DIR = "reference"
PHOTO_FILE = "photo.jpg"
MULTIPART_OVERHEAD = 64 * 1024

MSG_TOO_LARGE = "This photo is larger than 25 MB. Use a smaller photo."
MSG_NOT_IMAGE = (
    "This file is not a photo FacadeKit can read. Use a JPEG, PNG or WebP picture "
    "(a HEIC photo from an iPhone: export or share it as JPEG first)."
)
MSG_TOO_BIG_PIXELS = "This photo has too many pixels. Use a photo under 80 megapixels."
MSG_TOO_SMALL = (
    "This photo is too small to place surfaces on. Use one at least 64 pixels wide and tall."
)


class ReferencePhotoInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int
    height: int
    size_bytes: int
    updated_at: str


def _photo_path(project_dir: Path) -> Path:
    return project_dir / PHOTO_DIR / PHOTO_FILE


def sniff_dimensions(data: bytes) -> tuple[str, int, int] | None:
    """(format, width, height) read from the header of a PNG, JPEG or WebP, else None.

    The file name and the declared content type are never used: only the bytes decide.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            (length,) = struct.unpack(">H", data[i + 2 : i + 4])
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return "jpeg", w, h
            i += 2 + length
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return "webp", w, h
        if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", data[26:30])
            return "webp", w & 0x3FFF, h & 0x3FFF
        if kind == b"VP8L" and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def prepare_photo(data: bytes) -> tuple[bytes, int, int]:
    """Validate that `data` is a real image and return it as a re-encoded JPEG with its size."""
    header = sniff_dimensions(data)
    if header is None:
        raise FkError(BAD_REQUEST, MSG_NOT_IMAGE)
    _fmt, hw, hh = header
    if hw * hh > MAX_PHOTO_PIXELS:
        raise FkError(BAD_REQUEST, MSG_TOO_BIG_PIXELS)
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)  # applies EXIF turn
    if image is None or image.ndim != 3:
        raise FkError(BAD_REQUEST, MSG_NOT_IMAGE)
    h, w = image.shape[:2]
    if min(h, w) < MIN_SIDE:
        raise FkError(BAD_REQUEST, MSG_TOO_SMALL)
    scale = STORED_LONG_SIDE / max(h, w)
    if scale < 1:
        image = cv2.resize(
            image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA
        )
        h, w = image.shape[:2]
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:  # pragma: no cover
        raise FkError(BAD_REQUEST, MSG_NOT_IMAGE)
    return bytes(encoded.tobytes()), w, h


def _info(path: Path) -> ReferencePhotoInfo:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FkError(NOT_FOUND, "This project has no reference photo.")
    h, w = image.shape[:2]
    stat = path.stat()
    updated = datetime.fromtimestamp(stat.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ReferencePhotoInfo(width=w, height=h, size_bytes=stat.st_size, updated_at=updated)


def letterbox(photo: np.ndarray, width: int, height: int) -> np.ndarray:
    """The photo scaled to fit the projector frame, centred, on dark grey. Aspect is kept."""
    canvas = np.full((height, width, 3), (40, 40, 40), np.uint8)
    h, w = photo.shape[:2]
    scale = min(width / w, height / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(photo, (new_w, new_h), interpolation=cv2.INTER_AREA)
    x0, y0 = (width - new_w) // 2, (height - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


async def _read_capped(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(CHUNK):
        total += len(chunk)
        if total > MAX_PHOTO_BYTES:
            raise FkError(BAD_REQUEST, MSG_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def _store(project_dir: Path, jpeg: bytes) -> None:
    folder = project_dir / PHOTO_DIR
    folder.mkdir(exist_ok=True)
    final = _photo_path(project_dir)
    part = final.with_name(final.name + ".part")
    try:
        part.write_bytes(jpeg)
        part.replace(final)
    finally:
        part.unlink(missing_ok=True)


@router.post("/projects/{pid}/reference-photo", status_code=201)
async def put_reference_photo(
    pid: str, request: Request, state: State, file: Annotated[UploadFile, File()]
) -> ReferencePhotoInfo:
    project_dir = state.store.project_dir(pid)
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and int(declared) > MAX_PHOTO_BYTES + MULTIPART_OVERHEAD
    ):
        raise FkError(BAD_REQUEST, MSG_TOO_LARGE)
    data = await _read_capped(file)
    jpeg, _w, _h = await run_in_threadpool(prepare_photo, data)
    await run_in_threadpool(_store, project_dir, jpeg)
    return await run_in_threadpool(_info, _photo_path(project_dir))


@router.get("/projects/{pid}/reference-photo")
def get_reference_photo(pid: str, state: State) -> ReferencePhotoInfo:
    path = _photo_path(state.store.project_dir(pid))
    if not path.is_file():
        raise FkError(NOT_FOUND, "This project has no reference photo.")
    return _info(path)


@router.get("/projects/{pid}/reference-photo/view.jpg")
def reference_photo_view(pid: str, state: State) -> Response:
    """The photo letterboxed into the projector frame: the Surfaces background in manual mode."""
    project = state.store.load(pid)
    path = _photo_path(state.store.project_dir(pid))
    photo = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.is_file() else None
    if photo is None:
        raise FkError(NOT_FOUND, "This project has no reference photo.")
    view = letterbox(photo, project.projector.width, project.projector.height)
    ok, encoded = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:  # pragma: no cover
        raise FkError(NOT_FOUND, "This project has no reference photo.")
    return Response(
        encoded.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-cache"}
    )


@router.delete("/projects/{pid}/reference-photo", status_code=204)
def delete_reference_photo(pid: str, state: State) -> Response:
    _photo_path(state.store.project_dir(pid)).unlink(missing_ok=True)
    return Response(status_code=204)
