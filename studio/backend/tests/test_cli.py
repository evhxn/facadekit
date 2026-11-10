from __future__ import annotations

from pathlib import Path

import pytest

from facadekit.cli import main
from tests.conftest import FakeFpp


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_unknown_subcommand_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == 2


def test_cli_pipeline(
    tmp_path: Path, fake_fpp: FakeFpp, capsys: pytest.CaptureFixture[str]
) -> None:
    proj = str(tmp_path / "demo.fkproj")
    assert main(["new", proj, "--name", "Demo"]) == 0
    assert main(["calibrate", proj, "--manual"]) == 0
    assert main(["render", proj, "--preset", "preview-540p", "--alignment-check"]) == 0
    assert main(["render", proj, "--preset", "preview-540p"]) == 0
    assert main(["publish", proj, "--host", fake_fpp.host, "--start"]) == 0
    assert "FacadeKit - Demo" in capsys.readouterr().out


def test_render_missing_folder_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["render", str(tmp_path / "nope.fkproj")]) == 1
    assert "NOT_FOUND" in capsys.readouterr().err


def test_calibrate_sim_resolution_mismatch(tmp_path: Path) -> None:
    proj = str(tmp_path / "small.fkproj")
    assert main(["new", proj, "--name", "S", "--width", "1280", "--height", "720"]) == 0
    assert main(["calibrate", proj, "--sim"]) == 1


def test_doctor_no_cameras(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--no-cameras"]) == 0
    assert "ffmpeg: " in capsys.readouterr().out


def test_pick_port_default_free() -> None:
    from facadekit.cli import DEFAULT_PORT, pick_port

    port = pick_port("127.0.0.1", DEFAULT_PORT)
    assert port == DEFAULT_PORT or 8138 <= port <= 8147


def test_pick_port_skips_busy_ports() -> None:
    import socket

    from facadekit.cli import DEFAULT_PORT, pick_port

    held: list[socket.socket] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            pass  # already occupied elsewhere: the premise still holds
        held.append(s)
        port = pick_port("127.0.0.1", DEFAULT_PORT)
        assert port != DEFAULT_PORT
        assert 8138 <= port <= 8147
    finally:
        for s in held:
            s.close()


def test_pick_port_zero_is_free_ephemeral() -> None:
    from facadekit.cli import pick_port

    assert pick_port("127.0.0.1", 0) > 0


def test_pick_port_explicit_busy_raises() -> None:
    import socket

    from facadekit.cli import pick_port
    from facadekit.errors import FkError

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        busy = int(s.getsockname()[1])
        with pytest.raises(FkError):
            pick_port("127.0.0.1", busy)
