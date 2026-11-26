#!/usr/bin/env python3
"""Build the contents of the public ``fpp-plugin-FacadeKit`` repository from this monorepo.

FPP installs a plugin by ``git clone`` of a repository whose root is the plugin, so the plugin
is published as its own small repository. The monorepo stays the source of truth; this script
writes a clean tree (the plugin, the monorepo's LICENSE, a README and the user guide) that is
committed there as one snapshot. It does not push anything.

    python3 integrations/fpp-plugin/dev/export_public_repo.py --out /tmp/fpp-plugin-FacadeKit

Left out on purpose: ``dev/`` (talks to the monorepo), the internal design document
(docs/20, which describes one owner's network) and the monorepo's history. A last check
refuses to export if the tree contains anything that looks like an email address, a local
path, a pairing token or a stored password. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROOT = PLUGIN_DIR.parents[1]
SKIP_DIRS = {"dev", ".git", "__pycache__"}
SKIP_FILES = {".DS_Store", "README.md"}  # README is written below for the public repo

README = """# FacadeKit Projectors (FPP plugin)

A [Falcon Player](https://github.com/FalconChristmas/fpp) (FPP 10.4+) plugin that switches
**PJLink class 1** projectors on and off from FPP Commands, playlists and the scheduler,
shows their state, and lists the projection shows [FacadeKit](https://github.com/evhxn/facadekit)
sent to the player. It is optional: FacadeKit can send and play shows without it.

- PHP and shell only; installs nothing from the network.
- Several projectors at once, each switched in parallel; waits for warm-up or cool-down only
  when a projector itself reports it (laser projectors normally need none).
- Optional automatic power on at show start and off at show stop, and a fault guard. Both are
  off until you turn them on.
- A small token-protected REST API for FacadeKit (`/api/plugin/fpp-plugin-FacadeKit/...`),
  answering the local network only.

See [docs/USER-GUIDE.md](docs/USER-GUIDE.md) for setup. Licensed under the GNU GPL v3 (see
[LICENSE](LICENSE)).

## Install

On the player (FPP 10.4 or newer), as the `fpp` user:

```bash
cd /home/fpp/media/plugins
git clone https://github.com/evhxn/fpp-plugin-FacadeKit.git fpp-plugin-FacadeKit
sudo FPPDIR=/opt/fpp fpp-plugin-FacadeKit/scripts/fpp_install.sh
curl -X POST http://localhost/api/fppd/plugin/fpp-plugin-FacadeKit/load
```

Then open **Status/Control > FacadeKit Projectors**. To update, run `git pull` in the plugin
folder, then the install script and the `load` call again.

Remove it from **Content Setup > Plugin Manager > FacadeKit Projectors > Uninstall**, or with
`sudo /opt/fpp/scripts/uninstall_plugin fpp-plugin-FacadeKit`. That deletes the projector
list, the pairing token and the plugin's log from the player.

## Status

Tested on FPP 10.4 (Raspberry Pi 4) against fake PJLink projectors. Behaviour with specific
real projector models is reported in the issue tracker as people try it.
"""

LEAKS = {
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "local path": re.compile(r"/Users/[A-Za-z0-9._-]+"),
    "pairing token": re.compile(r"fkp_[A-Za-z0-9_-]{16,}"),
    "stored password": re.compile(r"""['"]password['"]\s*=>\s*['"][^'"]{4,}['"]"""),
}


def build(out: Path) -> list[Path]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written: list[Path] = []
    for src in sorted(PLUGIN_DIR.rglob("*")):
        rel = src.relative_to(PLUGIN_DIR)
        if src.is_dir() or SKIP_DIRS & set(rel.parts) or rel.name in SKIP_FILES:
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(dst)
    extras = {
        out / "LICENSE": (ROOT / "LICENSE").read_text("utf-8"),
        out / "README.md": README,
        out / "docs" / "USER-GUIDE.md": user_guide(),
    }
    for path, text in extras.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        written.append(path)
    return written


def user_guide() -> str:
    text = (ROOT / "docs" / "user" / "fpp-plugin.md").read_text("utf-8")
    start = text.index("## 1. Install")
    end = text.index("## 2. Add the projectors")
    install = (
        "## 1. Install\n\nFollow the install steps in the [README](../README.md#install). "
        "It takes four commands.\n\n"
    )
    return text[:start] + install + text[end:]


def check(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.suffix in {".png", ".jpg", ".gif", ".ico"}:
            continue
        text = path.read_text("utf-8", errors="replace")
        for label, pattern in LEAKS.items():
            for match in pattern.finditer(text):
                problems.append(f"{path.name}: {label}: {match.group(0)[:40]}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="directory to (re)create")
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve()
    if out == ROOT or ROOT in out.parents and ".git" in out.parts:
        print("refusing to write inside the monorepo's git directory", file=sys.stderr)
        return 2
    files = build(out)
    problems = check(files)
    if problems:
        shutil.rmtree(out)
        print("not exported, the tree contains:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    print(f"{len(files)} files written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
