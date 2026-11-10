import numpy as np
import pytest

from facadekit.errors import FkError
from facadekit.geom.homography import (
    apply_h,
    canvas_surface,
    clip_homography,
    estimate_aspect,
    fit_rects,
    quad_homography,
)
from facadekit.models.project import ProjectorSpec, Surface

Q = ((100.0, 80.0), (700.0, 120.0), (650.0, 500.0), (60.0, 450.0))


def _norm(h: np.ndarray) -> np.ndarray:
    return h / h[2, 2]


def test_corners_map_to_quad() -> None:
    h = quad_homography(100, 50, Q)
    src = np.array([(0, 0), (100, 0), (100, 50), (0, 50)], dtype=float)
    assert np.allclose(apply_h(h, src), np.array(Q), atol=1e-6)


def test_identity() -> None:
    h = quad_homography(100, 50, ((0, 0), (100, 0), (100, 50), (0, 50)))
    assert np.allclose(_norm(h), np.eye(3), atol=1e-9)


def test_estimate_aspect() -> None:
    assert estimate_aspect(((0, 0), (200, 0), (200, 100), (0, 100))) == pytest.approx(2.0)


def test_fit_rects_fill() -> None:
    assert fit_rects(1920, 1080, 1.0, "fill") == ((420, 0, 1500, 1080), (0, 0, 1, 1))


def test_fit_rects_fit() -> None:
    src, dst = fit_rects(1920, 1080, 1.0, "fit")
    assert src == (0, 0, 1920, 1080)
    assert dst == pytest.approx((0, 0.21875, 1, 0.78125))


def test_fit_rects_fit_tall_surface() -> None:
    _, dst = fit_rects(100, 100, 2.0, "fit")
    assert dst == pytest.approx((0.25, 0, 0.75, 1))


def test_fit_rects_unknown() -> None:
    with pytest.raises(FkError) as e:
        fit_rects(10, 10, 1.0, "bogus")
    assert e.value.code == "BAD_REQUEST"


def _surface() -> Surface:
    return Surface(id="s", name="S", quad=Q, aspect=1.0)


def test_clip_stretch() -> None:
    h, _ = clip_homography(1920, 1080, _surface(), "stretch")
    assert np.allclose(_norm(h), _norm(quad_homography(1920, 1080, Q)), atol=1e-9)


def test_clip_fill_centre() -> None:
    h, rect = clip_homography(1920, 1080, _surface(), "fill")
    c = np.array([[(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2]])
    expected = apply_h(quad_homography(1, 1, Q), np.array([[0.5, 0.5]]))
    assert np.allclose(apply_h(h, c), expected, atol=1e-6)


@pytest.mark.parametrize(
    "quad",
    [
        ((0, 0), (1, 0), (2, 0), (0, 1)),
        ((0, 0), (1, 0), (1, 1)),
    ],
)
def test_degenerate(quad: tuple) -> None:  # type: ignore[type-arg]
    with pytest.raises(FkError) as e:
        quad_homography(10, 10, quad)
    assert e.value.code == "BAD_REQUEST"


def test_canvas_surface() -> None:
    s = canvas_surface(ProjectorSpec(width=1920, height=1080))
    assert s.id == "canvas"
    assert s.quad[2] == (1920, 1080)
    assert s.aspect == pytest.approx(16 / 9)
