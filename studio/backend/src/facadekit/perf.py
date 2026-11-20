"""`facadekit doctor --perf`: how long this computer takes for the work FacadeKit does itself.

Every workload runs on built-in test data, so no camera, projector or project is needed. The
waiting a real scan does (projector and camera delays, docs/09 and `calib/timing.py`) is not
part of it: that depends on the devices, not on this computer.

Numbers per workload: wall time, CPU time of this process and of ffmpeg, and the peak memory
of the process when it finished (the peak only ever grows, so later rows include earlier ones).
"""

from __future__ import annotations

import os
import resource
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# Budgets for a computer to feel fine (chosen, not measured on a reference machine): a scan
# result within 5 s of the last picture, magic select within 2 s of a click, the live
# preview using under a quarter of one core at 5 pictures a second, a 30 s show rendering in
# under 3 minutes.
BUDGET_S: dict[str, float] = {
    "scan_decode": 5.0,
    "magic_select": 2.0,
    "preview_jpeg": 0.05,  # per picture
    "preview_frame": 0.5,  # per picture
    "render_1s": 6.0,  # per second of show (30 frames)
}
LOW_POWER_ENV = "FACADEKIT_LOW_POWER"
LOW_POWER_CORES = 4
LOW_POWER_RAM_BYTES = 8 * 1024**3 + 512 * 1024**2  # "8 GB" machines report a little less


@dataclass
class Result:
    name: str
    label: str
    wall_s: float
    cpu_s: float
    child_cpu_s: float
    peak_rss_mb: float
    per: str = ""  # "" = the whole workload; else what wall_s is per

    @property
    def budget_s(self) -> float:
        return BUDGET_S[self.name]

    @property
    def ok(self) -> bool:
        return self.wall_s <= self.budget_s


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1024**2 if sys.platform == "darwin" else peak / 1024  # bytes vs KiB


def _measure(
    name: str, label: str, fn: Callable[[], None], *, repeat: int = 1, per: str = ""
) -> Result:
    child0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu0, wall0 = time.process_time(), time.perf_counter()
    for _ in range(repeat):
        fn()
    wall = (time.perf_counter() - wall0) / repeat
    cpu = (time.process_time() - cpu0) / repeat
    child1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    child = (child1.ru_utime + child1.ru_stime - child0.ru_utime - child0.ru_stime) / repeat
    return Result(name, label, wall, cpu, child, _peak_rss_mb(), per)


def run(workdir: Path | None = None, log: Callable[[str], None] = lambda _m: None) -> list[Result]:
    from facadekit.ai.segment import GrabCutSegmenter
    from facadekit.calib.capture import run_capture
    from facadekit.calib.patterns import PatternSpec
    from facadekit.calib.pipeline import run_calibration_from_capture
    from facadekit.models.calibration import CalibSettings
    from facadekit.render.compositor import Compositor
    from facadekit.render.pipeline import render_project
    from facadekit.sim.devices import SimCamera, SimDisplay
    from facadekit.sim.e2e import add_demo_show, new_project_dir
    from facadekit.sim.scene import default_scene

    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="fk_perf_"))
    results: list[Result] = []

    # Pictures of a simulated 1920x1080 scan; making them is the simulator's work, not timed.
    log("preparing test pictures")
    scene = default_scene()
    display = SimDisplay(scene)
    camera = SimCamera(scene, display)
    settings = CalibSettings(settle_ms=1, discard_frames=0, average_frames=1)
    spec = PatternSpec(*scene.proj_size, skip_lsb=settings.skip_lsb)
    camera.open()
    capture = run_capture(display, camera, spec, settings, sleep=lambda _s: None)
    camera.close()
    scan_dir = new_project_dir(root / "show.fkproj")

    log("scan: decoding and building the map")
    cal_ids: list[str] = []

    def decode() -> None:
        cal_ids.append(run_calibration_from_capture(scan_dir, capture, "sim", settings).id)

    results.append(_measure("scan_decode", "Scan: decode the pictures, build the map", decode))

    view = cv2.imread(str(scan_dir / "calibration" / cal_ids[-1] / "proj_view.png"))
    assert view is not None
    log("magic select")
    door = scene.feature_quad_proj("garage_door")
    click = (sum(p[0] for p in door) / 4, sum(p[1] for p in door) / 4, 1)

    def select() -> None:
        seg = GrabCutSegmenter()
        seg.set_image(view)
        seg.segment([click])

    results.append(_measure("magic_select", "Magic select: one click (GrabCut)", select))

    log("live preview pictures")
    frame = camera_frame = np.ascontiguousarray(capture.white_bgr)
    assert camera_frame.shape[:2] == (720, 1280)

    def jpeg() -> None:
        ok, _buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        assert ok

    results.append(
        _measure("preview_jpeg", "Live camera preview: one 1280x720 picture", jpeg,
                 repeat=30, per="picture")
    )  # fmt: skip

    log("making a demo show")
    show_dir = scan_dir
    project, _quad = add_demo_show(show_dir, cal_ids[-1])
    with Compositor(project, show_dir, scale=0.5) as comp:
        comp.frame(0)  # opens the media once, as the Arrange page does on its first picture
        t = [0]

        def preview() -> None:
            t[0] += 33
            comp.frame(t[0] % 2000)

        results.append(
            _measure("preview_frame", "Arrange preview: one picture at half size", preview,
                     repeat=10, per="picture")
        )  # fmt: skip

    log("rendering 2 s of show")
    out = show_dir / "renders" / "perf.mp4"

    def render() -> None:
        render_project(show_dir, "pi3-1080p30", out, include_audio=False)

    r = _measure("render_1s", "Render for the Pi: one second of show (1080p30)", render)
    seconds = project.timeline.duration_ms / 1000
    results.append(
        Result(r.name, r.label, r.wall_s / seconds, r.cpu_s / seconds,
               r.child_cpu_s / seconds, r.peak_rss_mb, "second of show")
    )  # fmt: skip
    return results


def machine() -> dict[str, str | int | float]:
    from facadekit.sysinfo import total_ram_bytes

    ram = total_ram_bytes()
    return {
        "cores": os.cpu_count() or 1,
        "ram_gb": round(ram / 1024**3, 1) if ram else 0.0,
        "cv2_threads": cv2.getNumThreads(),
    }


def low_power(cores: int | None = None, ram_bytes: int | None = None) -> tuple[bool, str]:
    """Whether to run in low-power mode, and why.

    `FACADEKIT_LOW_POWER=1` or `0` decides; otherwise a computer with at most 4 cores or
    about 8 GB of memory or less gets it.
    """
    from facadekit.sysinfo import total_ram_bytes

    forced = os.environ.get(LOW_POWER_ENV, "").strip()
    if forced in ("1", "true", "on"):
        return True, f"{LOW_POWER_ENV} is set"
    if forced in ("0", "false", "off"):
        return False, f"{LOW_POWER_ENV}=0"
    cores = cores if cores is not None else (os.cpu_count() or 1)
    ram = ram_bytes if ram_bytes is not None else total_ram_bytes()
    if cores <= LOW_POWER_CORES:
        return True, f"this computer has {cores} cores"
    if ram and ram <= LOW_POWER_RAM_BYTES:
        return True, f"this computer has {ram / 1024**3:.0f} GB of memory"
    return False, f"{cores} cores, {ram / 1024**3:.0f} GB of memory" if ram else f"{cores} cores"


def report(results: list[Result]) -> list[str]:
    lines = [f"{'workload':<52} {'time':>9} {'budget':>8} {'cpu':>7} {'ffmpeg':>7} {'peak MB':>8}"]
    for r in results:
        per = f" per {r.per}" if r.per else ""
        lines.append(
            f"{r.label:<52} {r.wall_s:>8.2f}s {r.budget_s:>7.2f}s {r.cpu_s:>6.2f}s "
            f"{r.child_cpu_s:>6.2f}s {r.peak_rss_mb:>8.0f}  {'ok' if r.ok else 'SLOW'}{per}"
        )
    slow = [r for r in results if not r.ok]
    if slow:
        lines.append(
            "Slower than the budget: " + ", ".join(r.label.split(":")[0] for r in slow) + ". "
            "FacadeKit still works; those steps take longer. Low-power mode helps the live "
            "preview only (docs/11-performance.md)."
        )
    else:
        lines.append("Everything is within budget on this computer.")
    return lines
