"""The sandbox-Pi dev loop: the plugin served as a dumb-HTTP git repository (127.0.0.1 only)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.conftest import _free_port

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "integrations"
    / "fpp-plugin"
    / "dev"
    / "serve_dev_repo.py"
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _clone(url: str, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", url, str(dest)], check=True, capture_output=True,
                   timeout=60)  # fmt: skip


def test_build_and_fast_forward(tmp_path: Path) -> None:
    state = tmp_path / "state"
    args = [sys.executable, "-I", str(SCRIPT), "--no-serve", "--state-dir", str(state),
            "--ip", "127.0.0.1", "--port", "8765"]  # fmt: skip
    bare = Path(subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip())
    assert (bare / "info" / "refs").is_file()  # update-server-info ran
    clone = tmp_path / "clone"
    _clone(str(bare), clone)
    info = json.loads((clone / "pluginInfo.json").read_text("utf-8"))
    assert info["srcURL"] == "http://127.0.0.1:8765/fpp-plugin-FacadeKit.git"
    assert (clone / "callbacks.sh").stat().st_mode & 0o100
    assert (clone / "commands" / "descriptions.json").is_file()
    # A second run with a change is a fast-forward for the Pi.
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True,
                           text=True).stdout  # fmt: skip
    args[-1] = "8766"
    subprocess.run(args, check=True, capture_output=True)
    pull = subprocess.run(["git", "pull", "-q", "--ff-only"], cwd=clone, capture_output=True)
    assert pull.returncode == 0
    second = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True,
                            text=True).stdout  # fmt: skip
    assert first != second


def test_serves_over_dumb_http(tmp_path: Path) -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-I", str(SCRIPT), "--state-dir", str(tmp_path / "s"), "--bind",
         "127.0.0.1", "--ip", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}/fpp-plugin-FacadeKit.git"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/info/refs", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        assert httpx.get(f"http://127.0.0.1:{port}/", timeout=2).status_code == 404  # no listing
        assert httpx.post(f"{url}/info/refs", timeout=2).status_code in (405, 501)
        _clone(url, tmp_path / "c")
        assert (tmp_path / "c" / "lib" / "pjlink.php").is_file()
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=10)
    assert "git clone" in out and "fpp_install.sh" in out and "fake-pjlink" in out


def test_public_repo_export_is_clean(tmp_path: Path) -> None:
    script = SCRIPT.with_name("export_public_repo.py")
    out = tmp_path / "public"
    run = subprocess.run([sys.executable, str(script), "--out", str(out)],
                         capture_output=True, text=True, timeout=60)  # fmt: skip
    assert run.returncode == 0, run.stderr
    assert (out / "pluginInfo.json").is_file() and (out / "scripts" / "fpp_install.sh").is_file()
    assert (out / "LICENSE").is_file() and (out / "docs" / "USER-GUIDE.md").is_file()
    assert not (out / "dev").exists()  # the dev loop talks to the monorepo
    readme = (out / "README.md").read_text("utf-8")
    assert "git clone https://github.com/evhxn/fpp-plugin-FacadeKit.git" in readme
    assert "../../docs" not in readme  # no links into the private monorepo
    info = json.loads((out / "pluginInfo.json").read_text("utf-8"))
    assert info["srcURL"].endswith("/fpp-plugin-FacadeKit.git")
