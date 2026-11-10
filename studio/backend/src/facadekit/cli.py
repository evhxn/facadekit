from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from facadekit import __version__
from facadekit.errors import BAD_REQUEST, FkError
from facadekit.ids import slugify

COMMANDS = ["serve", "new", "calibrate", "render", "publish", "doctor", "sim"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="facadekit")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        sp = sub.add_parser(name)
        if name == "serve":
            sp.add_argument("--host", default="127.0.0.1")
            sp.add_argument("--port", type=int, default=8137)
            sp.add_argument("--data-dir", default=None)
            sp.add_argument("--no-browser", action="store_true")
            sp.add_argument("--sim", action="store_true")
        elif name == "new":
            sp.add_argument("project_dir")
            sp.add_argument("--name", required=True)
            sp.add_argument("--width", type=int, default=1920)
            sp.add_argument("--height", type=int, default=1080)
            sp.add_argument("--fps", type=int, default=30)
    return parser


def _run(body: Callable[[], int]) -> int:
    try:
        return body()
    except FkError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from facadekit.api.app import create_app

    if args.data_dir:
        os.environ["FACADEKIT_DATA"] = args.data_dir
    if not args.no_browser:
        timer = threading.Timer(1.0, webbrowser.open, args=(f"http://127.0.0.1:{args.port}/",))
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(sim=args.sim), host=args.host, port=args.port, log_level="info")
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from facadekit.models.project import Project, ProjectorSpec
    from facadekit.store.project_store import SUBFOLDERS, save_project_dir

    path = Path(args.project_dir)
    if path.suffix != ".fkproj":
        raise FkError(BAD_REQUEST, "PROJECT_DIR must end with .fkproj")
    if path.exists():
        raise FkError(BAD_REQUEST, f"{path} already exists")
    try:
        projector = ProjectorSpec(width=args.width, height=args.height, fps=args.fps)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise FkError(BAD_REQUEST, f"{first['loc'][0]}: {first['msg']}") from exc
    project = Project(id=slugify(path.stem), name=args.name, projector=projector)
    path.mkdir(parents=True)
    for sub in SUBFOLDERS:
        (path / sub).mkdir()
    save_project_dir(path, project)
    print(f"created {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, _ = parser.parse_known_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    if args.command == "serve":
        return _run(lambda: _cmd_serve(args))
    if args.command == "new":
        return _run(lambda: _cmd_new(args))
    print(f"facadekit {args.command}: not implemented", file=sys.stderr)
    return 2
