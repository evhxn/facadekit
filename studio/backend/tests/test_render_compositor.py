from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from facadekit.errors import FkError
from facadekit.models.project import Asset, Clip, Project, ProjectorSpec, Surface, Timeline, Track
from facadekit.render.compositor import Compositor

SQUARE = ((100.0, 40.0), (220.0, 40.0), (220.0, 140.0), (100.0, 140.0))


def _surface(quad: tuple = SQUARE, aspect: float = 1.0, sid: str = "s1") -> Surface:
    return Surface(id=sid, name=sid, quad=quad, aspect=aspect)


def _png(tmp_path: Path, name: str, img: np.ndarray) -> Asset:
    cv2.imwrite(str(tmp_path / f"{name}.png"), img)
    return Asset(
        id=name,
        kind="image",
        filename=f"{name}.png",
        original_name=f"{name}.png",
        width=img.shape[1],
        height=img.shape[0],
        duration_ms=None,
        fps=None,
        has_alpha=img.shape[2] == 4,
    )


def _solid(tmp_path: Path, name: str, bgr: tuple[int, int, int]) -> Asset:
    img = np.zeros((50, 50, 3), np.uint8)
    img[:] = bgr
    return _png(tmp_path, name, img)


def _clip(cid: str, asset: str, **kw: object) -> Clip:
    base: dict[str, object] = {
        "id": cid,
        "asset_id": asset,
        "surface_id": "s1",
        "start_ms": 0,
        "duration_ms": 2000,
        "fit": "stretch",
    }
    base.update(kw)
    return Clip.model_validate(base)


def _project(
    assets: list[Asset], tracks: list[list[Clip]], surfaces: list[Surface] | None = None
) -> Project:
    return Project(
        id="p",
        name="p",
        # ProjectorSpec enforces height >= 240; the ticket's 320x180 fixture bypasses validation.
        projector=ProjectorSpec(width=320, height=180, fps=30),
        surfaces=[_surface()] if surfaces is None else surfaces,
        assets=assets,
        timeline=Timeline(
            duration_ms=5000,
            tracks=[Track(id=f"t{i}", name=f"t{i}", clips=c) for i, c in enumerate(tracks)],
        ),
    )


def _px(frame: np.ndarray, x: int, y: int) -> tuple[int, ...]:
    return tuple(int(v) for v in frame[y, x])


def _render(project: Project, tmp_path: Path, t: int = 0, scale: float = 1.0) -> np.ndarray:
    with Compositor(project, tmp_path, scale=scale) as comp:
        return comp.frame(t)


def test_red_clip(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    f = _render(_project([red], [[_clip("c", "red")]]), tmp_path)
    assert f.shape == (180, 320, 3)
    assert _px(f, 160, 90) == (0, 0, 255)
    assert _px(f, 20, 20) == (0, 0, 0)


def test_opacity(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    f = _render(_project([red], [[_clip("c", "red", opacity=0.5)]]), tmp_path)
    assert all(abs(a - b) <= 2 for a, b in zip(_px(f, 160, 90), (0, 0, 128), strict=True))


def test_fade_in(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    p = _project([red], [[_clip("c", "red", fade_in_ms=1000)]])
    with Compositor(p, tmp_path) as comp:
        mid = _px(comp.frame(500), 160, 90)
        full = _px(comp.frame(1000), 160, 90)
    assert abs(mid[2] - 128) <= 6 and mid[:2] == (0, 0)
    assert full == (0, 0, 255)


def test_fade_out(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    p = _project([red], [[_clip("c", "red", fade_out_ms=1000)]])
    assert abs(_px(_render(p, tmp_path, 1500), 160, 90)[2] - 128) <= 6


def test_add_and_normal(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    green = _solid(tmp_path, "green", (0, 255, 0))
    for blend, expected in (("add", (0, 255, 255)), ("normal", (0, 255, 0))):
        p = _project([red, green], [[_clip("a", "red")], [_clip("b", "green", blend=blend)]])
        assert _px(_render(p, tmp_path), 160, 90) == expected


def test_screen(tmp_path: Path) -> None:
    gray = _solid(tmp_path, "gray", (128, 128, 128))
    p = _project([gray], [[_clip("a", "gray")], [_clip("b", "gray", blend="screen")]])
    assert all(abs(v - 191) <= 3 for v in _px(_render(p, tmp_path), 160, 90))


def test_outside_time_range(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    p = _project([red], [[_clip("c", "red", start_ms=1000, duration_ms=1000)]])
    with Compositor(p, tmp_path) as comp:
        assert _px(comp.frame(999), 160, 90) == (0, 0, 0)
        assert _px(comp.frame(1000), 160, 90) == (0, 0, 255)
        assert _px(comp.frame(2000), 160, 90) == (0, 0, 0)


def test_fit_letterbox(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    quad = ((50.0, 40.0), (250.0, 40.0), (250.0, 140.0), (50.0, 140.0))
    p = _project([red], [[_clip("c", "red", fit="fit")]], [_surface(quad, aspect=2.0)])
    f = _render(p, tmp_path)
    assert _px(f, 150, 90) == (0, 0, 255)
    assert _px(f, 60, 90) == (0, 0, 0)


def test_fill_crop(tmp_path: Path) -> None:
    img = np.zeros((100, 200, 3), np.uint8)
    img[:, :100] = (255, 0, 0)
    img[:, 100:] = (255, 255, 255)
    asset = _png(tmp_path, "split", img)
    f = _render(_project([asset], [[_clip("c", "split", fit="fill")]]), tmp_path)
    assert _px(f, 100 + 30, 90) == (255, 0, 0)
    assert _px(f, 220 - 30, 90) == (255, 255, 255)


def test_perspective(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    quad = ((100.0, 40.0), (220.0, 40.0), (250.0, 140.0), (70.0, 140.0))
    p = _project([red], [[_clip("c", "red")]], [_surface(quad)])
    f = _render(p, tmp_path)
    assert _px(f, 160, 90) == (0, 0, 255)
    assert _px(f, 160, 36) == (0, 0, 0)
    assert _px(f, 160, 144) == (0, 0, 0)
    # left edge at y=90 is x=85; right edge x=235
    assert _px(f, 80, 90) == (0, 0, 0)
    assert _px(f, 240, 90) == (0, 0, 0)


def test_canvas(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    p = _project([red], [[_clip("c", "red", surface_id="canvas")]], [])
    f = _render(p, tmp_path)
    assert _px(f, 10, 10) == (0, 0, 255)
    assert _px(f, 310, 170) == (0, 0, 255)


def test_scale(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    p = _project([red], [[_clip("c", "red")]])
    with Compositor(p, tmp_path, scale=0.5) as comp:
        assert comp.size == (160, 90)
        f = comp.frame(0)
    assert f.shape == (90, 160, 3)
    assert _px(f, 80, 45) == (0, 0, 255)
    assert _px(f, 10, 10) == (0, 0, 0)


def test_video_loop(tmp_path: Path, sample_video: Path) -> None:
    (tmp_path / "v.mp4").write_bytes(sample_video.read_bytes())
    asset = Asset(
        id="v",
        kind="video",
        filename="v.mp4",
        original_name="v.mp4",
        width=320,
        height=180,
        duration_ms=2000,
        fps=30.0,
    )
    p = _project(
        [asset],
        [[_clip("c", "v", duration_ms=5000, loop=True, fit="fill")]],
        [_surface(((0.0, 0.0), (320.0, 0.0), (320.0, 180.0), (0.0, 180.0)), aspect=320 / 180)],
    )
    with Compositor(p, tmp_path) as comp:
        a = comp.frame(500).astype(np.int16)
        b = comp.frame(2500).astype(np.int16)
    assert float(np.abs(a - b).mean()) < 8


def test_source_alpha(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    img = np.zeros((50, 50, 4), np.uint8)
    img[:, :] = (0, 255, 0, 255)
    img[:, :25, 3] = 0
    half = _png(tmp_path, "half", img)
    p = _project([red, half], [[_clip("a", "red")], [_clip("b", "half")]])
    f = _render(p, tmp_path)
    assert _px(f, 120, 90) == (0, 0, 255)
    assert _px(f, 200, 90) == (0, 255, 0)


def test_missing_media(tmp_path: Path) -> None:
    red = _solid(tmp_path, "red", (0, 0, 255))
    (tmp_path / "red.png").unlink()
    p = _project([red], [[_clip("c", "red")]])
    with Compositor(p, tmp_path) as comp, pytest.raises(FkError) as exc:
        comp.frame(0)
    assert exc.value.code == "NOT_FOUND"
