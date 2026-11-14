from __future__ import annotations

import argparse
import os
import platform
import socket
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from facadekit import __version__
from facadekit.errors import BAD_REQUEST, INTERNAL, NOT_FOUND, RESOLUTION_MISMATCH, FkError
from facadekit.ids import slugify

DEFAULT_PORT = 8137

COMMANDS = ["serve", "new", "calibrate", "render", "publish", "doctor", "sim", "phone-scan"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="facadekit")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        sp = sub.add_parser(name)
        if name == "serve":
            sp.add_argument("--host", default="127.0.0.1")
            sp.add_argument("--port", type=int, default=DEFAULT_PORT)
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
            sp.add_argument("--preset", default=None)  # None: default_preset_id()
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
            sp.add_argument("--bundle", default=None, metavar="ZIP")
        elif name == "sim":
            sim_sub = sp.add_subparsers(dest="sim_command", required=True)
            e2e = sim_sub.add_parser("e2e")
            e2e.add_argument("--workdir", default=None)
        elif name == "phone-scan":
            phone_sub = sp.add_subparsers(dest="phone_command", required=True)
            analyse = phone_sub.add_parser("analyse")
            analyse.add_argument("project_dir")
            analyse.add_argument("video")
            analyse.add_argument("--skip-lsb", type=int, default=2)
    return parser


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Match uvicorn: TIME_WAIT leftovers from a previous run must not count as busy.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, requested: int) -> int:
    """Return the port ``serve`` should listen on.

    0 means any free port. Any other port is used as-is or reported as busy:
    ``serve`` never silently moves to a different port.
    """
    if requested == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])
    if _port_free(host, requested):
        return requested
    raise FkError(BAD_REQUEST, f"Port {requested} is already in use. Try --port 0 for a free port.")


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
    host = "0.0.0.0" if args.lan else args.host  # LAN is opt-in only
    port = pick_port(host, args.port)
    url = f"http://127.0.0.1:{port}/"
    print(f"FacadeKit is running at {url}", flush=True)
    if not args.no_browser:
        timer = threading.Timer(1.0, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    from facadekit import logs
    from facadekit.config import data_dir

    log_file = logs.setup_logging(data_dir())
    print(f"Log file: {log_file}", flush=True)
    logs.log.info(
        "FacadeKit %s starting on port %d (sim=%s, lan=%s)", __version__, port, args.sim, args.lan
    )
    app = create_app(sim=args.sim, lan=args.lan, log_file=log_file)
    if args.lan:
        from facadekit.api.remote_auth import lan_addresses

        print("LAN mode is ON: other devices on your network can reach this server.")
        print(f"Pairing code for your phone: {app.state.remote.code}")
        for ip in lan_addresses():
            print(f"  http://{ip}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
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
    from facadekit.render.presets import default_preset_id
    from facadekit.store.project_store import load_project_dir

    path = Path(args.project_dir)
    load_project_dir(path)
    preset_id = args.preset or default_preset_id()
    out = Path(args.out) if args.out else path / "renders" / f"{new_id('rnd')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    tty = sys.stderr.isatty()

    def progress(fraction: float, _msg: str) -> None:
        if tty:
            print(f"\rRendering {fraction * 100:.0f}%", end="", file=sys.stderr, flush=True)

    try:
        if args.alignment_check:
            render_alignment_check(path, out, preset_id=preset_id, progress=progress)
        else:
            render_project(path, preset_id, out, include_audio=not args.no_audio, progress=progress)
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

    from facadekit import logs
    from facadekit.config import data_dir, load_overrides
    from facadekit.media.ffmpeg import ffmpeg_exe

    lines: list[str] = []

    def say(text: str) -> None:
        print(text)
        lines.append(text)

    say(f"facadekit {__version__}")
    say(f"python {platform.python_version()}")
    say(f"opencv {cv2.__version__}")
    exe = ffmpeg_exe()
    say(f"ffmpeg: {exe}")
    try:
        first = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=30
        ).stdout.splitlines()
        say(first[0] if first else "")
    except (OSError, subprocess.SubprocessError):
        say("ffmpeg -version failed")
    say(f"data dir: {data_dir()}")
    overrides = load_overrides()
    say(f"config.json overrides: {len(overrides)}")
    for key in sorted(overrides):
        say(f"  {key} = {overrides[key]!r}")
    if not args.no_cameras:
        from facadekit.devices.camera_opencv import list_cameras

        try:
            cams = list_cameras()
        except Exception:
            cams = []
        say(f"cameras: {len(cams)}")
        for cam in cams:
            say(f"  {cam.id}: {cam.name} {cam.width}x{cam.height}")
    log_file = logs.log_path(data_dir())
    say(f"log file: {log_file}" + ("" if log_file.is_file() else " (not written yet)"))
    if args.bundle:
        bundle = _write_bundle(Path(args.bundle), report="\n".join(lines) + "\n")
        print(f"diagnostic bundle: {bundle}")
    return 0


def _write_bundle(path: Path, *, report: str) -> Path:
    """Zip the doctor report and the log files, with the home directory written as `~`."""
    import zipfile

    from facadekit import logs
    from facadekit.config import data_dir

    if path.suffix != ".zip":
        raise FkError(BAD_REQUEST, "--bundle: the file name must end with .zip")
    log_file = logs.log_path(data_dir())
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("doctor.txt", logs.redact(report))
        for candidate in sorted(log_file.parent.glob(logs.LOG_NAME + "*")):
            text = candidate.read_text(encoding="utf-8", errors="replace")
            bundle.writestr(f"logs/{candidate.name}", logs.redact(text))
    return path


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


def _cmd_phone_scan(args: argparse.Namespace) -> int:
    import numpy as np

    from facadekit.calib.phone import check_upload_name, run_phone_calibration
    from facadekit.models.calibration import CalibSettings
    from facadekit.store.project_store import load_project_dir

    path = Path(args.project_dir)
    video = Path(args.video)
    check_upload_name(video.name)
    project = load_project_dir(path)
    proj_size = (project.projector.width, project.projector.height)
    cal = run_phone_calibration(
        path, video, CalibSettings(skip_lsb=args.skip_lsb),
        proj_size=proj_size, strategy=None, advisor=None,
        progress=lambda _f, msg: print(msg, file=sys.stderr),
    )  # fmt: skip
    p2c = np.load(path / "calibration" / cal.id / "p2c.npy")
    q = cal.quality
    roi = cal.settings.roi if cal.settings is not None else None
    print(f"calibration: {cal.id}")
    print(f"camera: {cal.camera_width}x{cal.camera_height}")
    if roi is not None:
        print(f"crop: x={roi.x} y={roi.y} w={roi.w} h={roi.h} bin={roi.bin}")
    if q is not None:
        print(f"coverage: {q.coverage * 100:.1f} %")
        if q.px_per_proj_px is not None:
            print(f"camera px per projector px: {q.px_per_proj_px:.2f}")
        print(f"verdict: {q.verdict}")
    w, h = proj_size
    for name, (x, y) in {
        "top-left": (0, 0), "top-right": (w - 1, 0),
        "bottom-right": (w - 1, h - 1), "bottom-left": (0, h - 1),
    }.items():  # fmt: skip
        cx, cy = (float(v) for v in p2c[y, x])
        print(f"corner {name}: camera ({cx:.1f}, {cy:.1f})")
    if cal.warnings:
        print("warnings: " + ", ".join(cal.warnings))
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
        "phone-scan": _cmd_phone_scan,
    }
    return _run(lambda: handlers[args.command](args))
