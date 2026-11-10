from __future__ import annotations

import numpy as np
import pytest

from facadekit.errors import FkError
from facadekit.geom.mask import rasterize_mask

SQUARE = [(10.0, 10.0), (50.0, 10.0), (50.0, 50.0), (10.0, 50.0)]
HOLE = [(20.0, 20.0), (40.0, 20.0), (40.0, 40.0), (20.0, 40.0)]


def test_square_is_filled_and_outside_is_empty() -> None:
    mask = rasterize_mask(64, 64, SQUARE)
    assert mask.dtype == np.float32
    assert mask.shape == (64, 64)
    assert mask[30, 30] == pytest.approx(1.0)
    assert mask[5, 5] == pytest.approx(0.0)
    assert abs(float(mask.sum()) - 1600.0) <= 0.08 * 1600.0


def test_hole_is_cleared() -> None:
    mask = rasterize_mask(64, 64, SQUARE, holes=[HOLE])
    assert mask[30, 30] == pytest.approx(0.0)
    assert mask[15, 15] == pytest.approx(1.0)


def test_feather_softens_edge_and_stays_in_range() -> None:
    mask = rasterize_mask(64, 64, SQUARE, feather_px=8)
    edge = float(mask[30, 10])
    assert 0.3 < edge < 0.7
    assert float(mask.min()) >= 0.0
    assert float(mask.max()) <= 1.0


def test_fewer_than_three_points_raises_bad_request() -> None:
    with pytest.raises(FkError) as excinfo:
        rasterize_mask(64, 64, [(0.0, 0.0), (10.0, 10.0)])
    assert excinfo.value.code == "BAD_REQUEST"
