from __future__ import annotations

from pathlib import Path

from facadekit.sim.e2e import run_e2e
from tests.conftest import FakeFpp


def test_e2e(tmp_path: Path) -> None:
    result = run_e2e(tmp_path, log=lambda m: None)
    assert result.max_corner_error_px < 1.0
    assert result.coverage > 0.85
    assert result.render_path.is_file()


def test_e2e_publish(tmp_path: Path, fake_fpp: FakeFpp) -> None:
    run_e2e(tmp_path, publish_host=fake_fpp.host, log=lambda m: None)
    assert (fake_fpp.state_dir / "videos" / "fk_e2e.mp4").exists() or any(
        fake_fpp.state_dir.rglob("fk_e2e.mp4")
    )
