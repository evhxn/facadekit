from __future__ import annotations

import numpy as np

from facadekit.geom.homography import apply_h, quad_homography
from facadekit.geom.mesh import identity_mesh, mesh_control_points, mesh_inverse_map
from facadekit.models.project import Mesh, Surface

TRAP = ((120.0, 60.0), (300.0, 80.0), (320.0, 220.0), (100.0, 200.0))


def _surface(quad: tuple = TRAP, mesh: Mesh | None = None) -> Surface:
    return Surface(id="s", name="s", quad=quad, aspect=1.0, mesh=mesh)


def test_identity_mesh() -> None:
    m = identity_mesh(3, 2)
    assert (m.cols, m.rows, len(m.offsets)) == (3, 2, 12)
    assert all(o == (0.0, 0.0) for o in m.offsets)


def test_control_points_zero_square() -> None:
    quad = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    pts = mesh_control_points(_surface(quad, identity_mesh(2, 2)))
    assert pts.shape == (3, 3, 2)
    lattice = np.array([0.0, 50.0, 100.0])
    np.testing.assert_allclose(pts[:, :, 0], np.tile(lattice, (3, 1)), atol=1e-6)
    np.testing.assert_allclose(pts[:, :, 1], np.tile(lattice[:, None], (1, 3)), atol=1e-6)


def test_zero_mesh_matches_inverse_homography() -> None:
    surface = _surface(mesh=identity_mesh(2, 2))
    size = (400, 300)
    map_u, map_v = mesh_inverse_map(surface, size)
    assert map_u.dtype == np.float32 and map_u.shape == (300, 400)
    h_inv = np.linalg.inv(quad_homography(1, 1, TRAP))
    inside = map_u >= 0
    assert inside.sum() > 10000
    ys, xs = np.nonzero(inside)
    # Interior pixels (away from the boundary) agree with the inverse homography.
    rng = np.random.default_rng(1)
    pts = []
    while len(pts) < 200:
        k = int(rng.integers(len(xs)))
        x, y = xs[k], ys[k]
        uv = apply_h(h_inv, np.array([[x + 0.5, y + 0.5]]))[0]
        if 0.05 < uv[0] < 0.95 and 0.05 < uv[1] < 0.95:
            pts.append((x, y, uv))
    for x, y, uv in pts:
        assert abs(map_u[y, x] - uv[0]) < 0.01
        assert abs(map_v[y, x] - uv[1]) < 0.01
    # Outside the quad: -1 (checked well away from the boundary).
    assert (map_u[:40, :] == -1).all() and (map_v[:40, :] == -1).all()
    assert (map_u[:, :60] == -1).all()
    assert (map_u[:, 340:] == -1).all()
    assert (map_u[240:, :] == -1).all()
    # No cracks: every pixel whose centre is clearly inside the quad is covered.
    for y in range(0, 300, 7):
        for x in range(0, 400, 7):
            uv = apply_h(h_inv, np.array([[x + 0.5, y + 0.5]]))[0]
            if 0.02 < uv[0] < 0.98 and 0.02 < uv[1] < 0.98:
                assert map_u[y, x] >= 0


def test_moved_centre_point() -> None:
    quad = ((50.0, 50.0), (250.0, 50.0), (250.0, 250.0), (50.0, 250.0))
    mesh = identity_mesh(2, 2)
    offsets = list(mesh.offsets)
    offsets[4] = (20.0, 0.0)  # centre control point (row 1, col 1)
    surface = _surface(quad, Mesh(cols=2, rows=2, offsets=offsets))
    base_centre = apply_h(quad_homography(1, 1, quad), np.array([[0.5, 0.5]]))[0]
    pts = mesh_control_points(surface)
    np.testing.assert_allclose(pts[1, 1], base_centre + (20.0, 0.0), atol=1e-6)
    map_u, map_v = mesh_inverse_map(surface, (300, 300))
    x, y = (int(round(c - 0.5)) for c in (base_centre[0] + 20.0, base_centre[1]))
    assert abs(map_u[y, x] - 0.5) < 0.01 and abs(map_v[y, x] - 0.5) < 0.01
    # Corners unchanged: the corner pixels map to the unit-square corners.
    assert abs(map_u[51, 51]) < 0.01 and abs(map_v[51, 51]) < 0.01
    assert abs(map_u[248, 248] - 1.0) < 0.01 and abs(map_v[248, 248] - 1.0) < 0.01
    assert abs(map_u[51, 248] - 1.0) < 0.01 and abs(map_v[51, 248]) < 0.01
