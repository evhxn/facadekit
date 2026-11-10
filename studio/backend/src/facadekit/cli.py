from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from facadekit import __version__
from facadekit.errors import BAD_REQUEST, INTERNAL, NOT_FOUND, RESOLUTION_MISMATCH, FkError
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
            sp.add_argument("--lan", action="store_true")
        elif name == "new":
            sp.add_argument("project_dir")
            sp.add_argument("--name", required=True)
            sp.add_argument("--width", type=int, default=1920)
            sp.add_argument("--height", type=int, default=1080)
            sp.add_argument("--fps", type=int, default=30)
        elif name == "calibrate":
            sp.add_argument("project_dir")
            mode = sp.add_mutually_exclusive_group(required=True)
            mode.add_argument("--sim", action="store_true")
            mode.add_argument("--manual", action="store_true")
        elif name == "render":
            sp.add_argument("project_dir")
            sp.add_argument("--preset", default="pi3-1080p30")
            sp.add_argument("--out", default=None)
            sp.add_argument("--no-audio", action="store_true")
            sp.add_argument("--alignment-check", action="store_true")
        elif name == "publish":
            sp.add_argument("project_dir")
            sp.add_argument("--host", required=True)
            sp.add_argument("--render", default=None)
            sp.add_argument("--playlist", default=None)
            sp.add_argument("--start", action="store_true")
        elif name == "doctor":
            sp.add_argument("--no-cameras", action="store_true")
        elif name == "sim":
            sim_sub = sp.add_subparsers(dest="sim_command", required=True)
            e2e = sim_sub.add_parser("e2e")
            e2e.add_argument("--workdir", default=None)
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
    host = args.host
    app = create_app(sim=args.sim, lan=args.lan)
    if args.lan:
        from facadekit.api.remote_auth import lan_addresses

        host = "0.0.0.0"  # opt-in only; the default stays loopback
        print("LAN mode is ON: other devices on your network can reach this server.")
        print(f"Pairing code for your phone: {app.state.remote.code}")
        for ip in lan_addresses():
            print(f"  http://{ip}:{args.port}")
    uvicorn.run(app, host=host, port=args.port, log_level="info")
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


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from facadekit.calib.pipeline import create_manual_calibration, run_calibration
    from facadekit.models.calibration import CalibSettings
    from facadekit.sim.devices import SimCamera, SimDisplay
    from facadekit.sim.scene import default_scene
    from facadekit.store.project_store import load_project_dir, save_project_dir

    path = Path(args.project_dir)
    project = load_project_dir(path)
    if args.manual:
        cal = create_manual_calibration(path, project.projector)
        print(cal.id)
    else:
        scene = default_scene()
        if (project.projector.width, project.projector.height) != scene.proj_size:
            raise FkError(
                RESOLUTION_MISMATCH,
                f"Simulated calibration needs a {scene.proj_size[0]}x{scene.proj_size[1]} "
                f"projector, project has {project.projector.width}x{project.projector.height}.",
            )
        display = SimDisplay(scene)
        cal = run_calibration(
            path, display, SimCamera(scene, display), "sim",
            CalibSettings(discard_frames=0, average_frames=1),
            sleep=lambda s: None,
        )  # fmt: skip
        coverage = cal.quality.coverage if cal.quality else 0.0
        print(f"{cal.id} coverage: {coverage * 100:.0f} %")
    save_project_dir(path, load_project_dir(path).model_copy(update={"calibration_id": cal.id}))
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    from facadekit.ids import new_id
    from facadekit.render.outline import render_alignment_check
    from facadekit.render.pipeline import render_project
    from facadekit.store.project_store import load_project_dir

    path = Path(args.project_dir)
    load_project_dir(path)
    out = Path(args.out) if args.out else path / "renders" / f"{new_id('rnd')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    tty = sys.stderr.isatty()

    def progress(fraction: float, _msg: str) -> None:
        if tty:
            print(f"\rRendering {fraction * 100:.0f}%", end="", file=sys.stderr, flush=True)

    try:
        if args.alignment_check:
            render_alignment_check(path, out, preset_id=args.preset, progress=progress)
        else:
            render_project(
                path, args.preset, out, include_audio=not args.no_audio, progress=progress
            )
    finally:
        if tty:
            print(file=sys.stderr)
    print(out)
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    from facadekit.fpp.publish import publish_render
    from facadekit.store.project_store import load_project_dir

    path = Path(args.project_dir)
    project = load_project_dir(path)
    if args.render:
        render = Path(args.render)
    else:
        found = sorted(
            (path / "renders").glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not found:
            raise FkError(NOT_FOUND, f"No renders in {path / 'renders'}")
        render = found[0]
    playlist = args.playlist or f"FacadeKit - {project.name}"
    publish_render(
        args.host, render, media_name=f"fk_{project.id}.mp4", playlist_name=playlist,
        start=args.start, progress=lambda f, m: print(f"{f * 100:3.0f}% {m}"),
    )  # fmt: skip
    print(playlist)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    import cv2

    from facadekit.config import data_dir
    from facadekit.media.ffmpeg import ffmpeg_exe

    print(f"facadekit {__version__}")
    print(f"python {platform.python_version()}")
    print(f"opencv {cv2.__version__}")
    exe = ffmpeg_exe()
    print(f"ffmpeg: {exe}")
    try:
        first = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=30
        ).stdout.splitlines()
        print(first[0] if first else "")
    except (OSError, subprocess.SubprocessError):
        print("ffmpeg -version failed")
    print(f"data dir: {data_dir()}")
    if not args.no_cameras:
        from facadekit.devices.camera_opencv import list_cameras

        try:
            cams = list_cameras()
        except Exception:
            cams = []
        print(f"cameras: {len(cams)}")
        for cam in cams:
            print(f"  {cam.id}: {cam.name} {cam.width}x{cam.height}")
    return 0


def _cmd_sim(args: argparse.Namespace) -> int:
    import tempfile

    from facadekit.sim.e2e import MAX_CORNER_ERROR_PX, run_e2e

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="fk_e2e_"))
    workdir.mkdir(parents=True, exist_ok=True)
    result = run_e2e(workdir, log=lambda m: print(m, file=sys.stderr))
    print(f"max corner error: {result.max_corner_error_px:.2f} px")
    print(f"coverage: {result.coverage * 100:.0f} %")
    print(result.render_path)
    if result.max_corner_error_px >= MAX_CORNER_ERROR_PX:
        raise FkError(INTERNAL, f"corner error {result.max_corner_error_px:.2f} px is too large")
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
    handlers = {
        "calibrate": _cmd_calibrate,
        "render": _cmd_render,
        "publish": _cmd_publish,
        "doctor": _cmd_doctor,
        "sim": _cmd_sim,
    }
    return _run(lambda: handlers[args.command](args))
