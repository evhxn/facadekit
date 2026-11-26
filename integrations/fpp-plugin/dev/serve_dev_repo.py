#!/usr/bin/env python3
"""Serve the FPP plugin from this Mac as a read-only git repository, for a sandbox Pi.

FPP installs plugins with ``git clone``. This script turns ``integrations/fpp-plugin`` into
its own small repository (the plugin must sit at the root of a repository, which the
FacadeKit monorepo is not), and serves it over plain ("dumb") HTTP with Python's
http.server, so the Pi can clone and pull it without anything being published to GitHub.

    python3 integrations/fpp-plugin/dev/serve_dev_repo.py            # build and serve
    python3 integrations/fpp-plugin/dev/serve_dev_repo.py --no-serve # only build

Each run commits a new snapshot on top of the previous one, so ``git pull`` on the Pi is a
fast-forward. The state lives in ~/.cache/facadekit-fpp-plugin-dev (``--state-dir``).
The server answers GET only, shows nothing but that repository, and stops with Ctrl+C.
Standard library and the ``git`` command only. It contacts nothing; the Pi connects to it.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = "fpp-plugin-FacadeKit"
PLUGIN_DIR = Path(__file__).resolve().parents[1]
SKIP = {".git", "__pycache__", ".DS_Store"}


def git(*args: str, cwd: Path) -> str:
    who = {"NAME": "FacadeKit dev", "EMAIL": "dev@localhost"}
    env = {
        **os.environ,
        **{f"GIT_{r}_{k}": v for r in ("AUTHOR", "COMMITTER") for k, v in who.items()},
    }
    out = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def lan_ip() -> str:
    """This computer's address on the network that has the default route.

    A UDP "connect" sends no packet; it only asks the OS which interface it would use.
    192.0.2.1 is a documentation address (TEST-NET-1) that is never contacted.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
            return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def source_revision() -> str:
    try:
        sha = git("rev-parse", "--short", "HEAD", cwd=PLUGIN_DIR)
        dirty = git("status", "--porcelain", "--", ".", cwd=PLUGIN_DIR)
    except SystemExit:
        return "unknown"
    return sha + ("-dirty" if dirty else "")


def copy_plugin(work: Path) -> None:
    for item in work.iterdir():
        if item.name == ".git":
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    for item in PLUGIN_DIR.iterdir():
        if item.name in SKIP:
            continue
        target = work / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns(*SKIP))
        else:
            shutil.copy2(item, target)


def build(state: Path, url: str) -> Path:
    """Refresh the work repository and the bare repository that is served. Returns it."""
    work = state / "work"
    serve = state / "serve"
    bare = serve / f"{REPO}.git"
    work.mkdir(parents=True, exist_ok=True)
    if not (work / ".git").is_dir():
        git("init", "-q", "-b", "main", cwd=work)
    copy_plugin(work)
    info_path = work / "pluginInfo.json"
    info = json.loads(info_path.read_text("utf-8"))
    info["srcURL"] = url
    info["versions"] = [{"minFPPVersion": "10.4", "maxFPPVersion": "0", "branch": "main",
                         "sha": ""}]  # fmt: skip
    info_path.write_text(json.dumps(info, indent="\t", ensure_ascii=False) + "\n", "utf-8")
    git("add", "-A", cwd=work)
    if git("status", "--porcelain", cwd=work):
        git("commit", "-q", "-m", f"dev snapshot of integrations/fpp-plugin at {source_revision()}",
            cwd=work)  # fmt: skip
    if not bare.is_dir():
        serve.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", "-b", "main", str(bare), cwd=serve)
    git("push", "-q", "--force", str(bare), "main:main", cwd=work)
    git("update-server-info", cwd=bare)
    return bare


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self.send_error(405)

    def list_directory(self, path: str | os.PathLike[str]) -> None:  # type: ignore[override]
        self.send_error(404)
        return None


INSTRUCTIONS = """
Plugin repository: {url}

On the sandbox Pi (over SSH, as the fpp user):

  cd /home/fpp/media/plugins
  git clone {url} {repo}
  sudo FPPDIR=/opt/fpp {repo}/scripts/fpp_install.sh
  curl -X POST http://localhost/api/fppd/plugin/{repo}/load    # or restart fppd in FPP's UI

Then open FPP's UI: Status/Control > FacadeKit Projectors.
No projector hardware? Run fake projectors on this Mac and add a projector with this Mac's
address ({ip}) and port 4352 (and 4353 for a second one):

  cd studio/backend && uv run facadekit fpp fake-pjlink --host 0.0.0.0 --port 4352 --count 2

Update after changing the plugin: run this script again, then on the Pi
  cd /home/fpp/media/plugins/{repo} && git pull && sudo FPPDIR=/opt/fpp scripts/fpp_install.sh
  curl -X POST http://localhost/api/fppd/plugin/{repo}/load

Remove: FPP's UI > Content Setup > Plugin Manager > FacadeKit Projectors > Uninstall, or
  sudo /opt/fpp/scripts/uninstall_plugin {repo}
which runs the plugin's own uninstall (it deletes plugindata/{repo} and the plugin log).

Serving on {bind}:{port}. Ctrl+C to stop.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="0.0.0.0", help="listen address (default all)")
    parser.add_argument("--ip", default=None, help="address the Pi uses (default: detected)")
    parser.add_argument("--state-dir", default=str(Path.home() / ".cache" / f"{REPO}-dev"))
    parser.add_argument("--no-serve", action="store_true", help="build the repository and exit")
    args = parser.parse_args(argv)
    if shutil.which("git") is None:
        print("git is not installed", file=sys.stderr)
        return 1
    ip = args.ip or lan_ip()
    url = f"http://{ip}:{args.port}/{REPO}.git"
    state = Path(args.state_dir).expanduser()
    bare = build(state, url)
    if args.no_serve:
        print(bare)
        return 0
    handler = functools.partial(_Handler, directory=str(bare.parent))
    server = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
    print(
        INSTRUCTIONS.format(url=url, repo=REPO, ip=ip, bind=args.bind, port=server.server_port),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
