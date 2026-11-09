from __future__ import annotations

import argparse
import sys

from facadekit import __version__

COMMANDS = ["serve", "new", "calibrate", "render", "publish", "doctor", "sim"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="facadekit")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        sub.add_parser(name)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, _ = parser.parse_known_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    print(f"facadekit {args.command}: not implemented", file=sys.stderr)
    return 2
