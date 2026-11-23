"""`facadekit verify`: the surface check without the browser (docs/13-surface-verification.md).

Two ways to reach the devices:

* default: ask a running `facadekit serve` (its projector window and camera) over HTTP on this
  computer;
* `--sim`: run in this process against the simulator (projects calibrated with
  `facadekit calibrate --sim`).
"""

from __future__ import annotations

import base64
import json
import platform
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from facadekit.calib.selftest import format_report, run_self_test
from facadekit.calib.verify import Injection, VerifyOutcome
from facadekit.errors import BAD_REQUEST, FkError
from facadekit.models.verification import VerificationRecord

DEFAULT_SERVER = "http://127.0.0.1:8137"
POLL_S = 0.3
JOB_TIMEOUT_S = 180.0

Runner = Callable[[Injection | None, str | None], VerificationRecord]


def describe(record: VerificationRecord) -> str:
    rig = record.rig
    lines = [
        f"rig: {rig.verdict}  (camera {rig.camera}, projector {rig.projector}; "
        f"camera shift {rig.camera_shift_px} px, projector shift {rig.projector_shift_px} px, "
        f"dots {rig.dots_found}/{rig.dots_expected}, confidence {rig.confidence:.2f})",
        f"  {rig.message}",
        f"  {rig.recommendation}",
    ]
    s = record.surface
    if s is not None:
        lines.append(
            f"surface {s.surface_id}: {s.verdict.upper()}  (worst edge {s.max_edge_px} px, "
            f"light off the object {_pct(s.leakage)}, object left dark {_pct(s.uncovered)}, "
            f"object clarity {s.object_confidence:.2f})"
        )
        lines.append(f"  {s.message}")
        for e in s.edges:
            lines.append(f"  - {e.advice}")
        if s.suggested_quad is not None:
            quad = ", ".join(f"({x:.1f}, {y:.1f})" for x, y in s.suggested_quad)
            lines.append(f"  suggested corners: {quad}")
        elif s.suggestion_note:
            lines.append(f"  no suggested fix: {s.suggestion_note}")
    lines.append(
        f"took {record.duration_s:.1f} s, {record.shots} pictures at {record.level:.2f} of full "
        f"brightness, exposure {'locked' if record.exposure_locked else 'automatic'}"
    )
    return "\n".join(lines)


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f} %" if v is not None else "?"


# --- in-process simulator ------------------------------------------------------------------


def sim_runner(project_dir: Path) -> tuple[Runner, Callable[[], dict[str, bytes]]]:
    from facadekit.calib.verify import run_verify
    from facadekit.sim.devices import SimCamera, SimDisplay
    from facadekit.sim.scene import default_scene
    from facadekit.store.project_store import load_project_dir

    pictures: dict[str, bytes] = {}

    def run(inject: Injection | None, surface_id: str | None) -> VerificationRecord:
        import cv2

        project = load_project_dir(project_dir)
        scene = default_scene()
        display = SimDisplay(scene)
        outcome: VerifyOutcome = run_verify(
            project_dir,
            project,
            display,
            SimCamera(scene, display),
            surface_id=surface_id,
            sleep=lambda _s: None,
            inject=inject,
        )
        if outcome.overlay is not None:
            ok, buf = cv2.imencode(".jpg", outcome.overlay)
            if ok:
                pictures[f"{len(pictures):02d}-{surface_id or 'rig'}.jpg"] = buf.tobytes()
        if inject is None:
            from facadekit.calib.verify import save_record

            save_record(project_dir, outcome)
        return outcome.record

    return run, lambda: pictures


# --- through a running server --------------------------------------------------------------


def server_runner(server: str, project_id: str) -> tuple[Runner, Callable[[], dict[str, bytes]]]:
    import httpx

    pictures: dict[str, bytes] = {}
    client = httpx.Client(base_url=server.rstrip("/") + "/api/v1", timeout=30.0)

    def check(resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            try:
                err = resp.json()["error"]
                raise FkError(str(err["code"]), str(err["message"]))
            except (ValueError, KeyError, TypeError):
                raise FkError("HTTP_ERROR", f"{resp.status_code}: {resp.text[:200]}") from None
        return resp.json()

    def run(inject: Injection | None, surface_id: str | None) -> VerificationRecord:
        body: dict[str, Any] = {"surface_id": surface_id}
        if inject is not None:
            body["inject"] = {
                "proj_shift": list(inject.proj_shift),
                "grow_px": inject.grow_px,
                "cam_shift": list(inject.cam_shift),
            }
        try:
            job = check(client.post(f"/projects/{project_id}/verify", json=body))
        except httpx.HTTPError as exc:
            raise FkError(
                "SERVER_UNREACHABLE",
                f"No FacadeKit server at {server}. Start `facadekit serve`, open the projector "
                "window on the projector, then run this again.",
            ) from exc
        deadline = time.monotonic() + JOB_TIMEOUT_S
        while True:
            state = check(client.get(f"/jobs/{job['job_id']}"))
            if state["state"] in ("succeeded", "failed", "cancelled"):
                break
            if time.monotonic() > deadline:
                client.post(f"/jobs/{job['job_id']}/cancel")
                raise FkError("TIMEOUT", "The check took too long and was cancelled.")
            time.sleep(POLL_S)
        if state["state"] != "succeeded":
            err = state.get("error") or {"code": "CANCELLED", "message": "cancelled"}
            raise FkError(str(err["code"]), str(err["message"]))
        result = state["result"]
        record = VerificationRecord.model_validate(result["verification"])
        b64 = result.get("overlay_jpg_b64")
        name = f"{len(pictures):02d}-{surface_id or 'rig'}.jpg"
        if b64:
            pictures[name] = base64.b64decode(b64)
        elif record.overlay and surface_id is not None:
            pic = client.get(f"/projects/{project_id}/verifications/{surface_id}/overlay.jpg")
            if pic.status_code == 200:
                pictures[name] = pic.content
        return record

    return run, lambda: pictures


def write_bundle(
    path: Path, text: str, records: list[VerificationRecord], pictures: dict[str, bytes]
) -> Path:
    from facadekit import logs
    from facadekit.api.routes_diagnostics import system_info
    from facadekit.config import data_dir

    if path.suffix != ".zip":
        raise FkError(BAD_REQUEST, "--bundle: the file name must end with .zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("report.txt", logs.redact(text))
        bundle.writestr("system.json", json.dumps(system_info(), indent=2))
        bundle.writestr("platform.txt", platform.platform())
        for i, rec in enumerate(records):
            bundle.writestr(
                f"records/{i:02d}-{rec.surface_id or 'rig'}.json", rec.model_dump_json(indent=2)
            )
        for name, data in pictures.items():
            bundle.writestr(f"pictures/{name}", data)
        log_file = logs.log_path(data_dir())
        for candidate in sorted(log_file.parent.glob(logs.LOG_NAME + "*")):
            body = candidate.read_text(encoding="utf-8", errors="replace")
            bundle.writestr(f"logs/{candidate.name}", logs.redact(body))
    return path


def run_cli(
    project: str,
    *,
    sim: bool,
    server: str,
    surface: str | None,
    self_test: bool,
    bundle: str | None,
    apply: bool,
    as_json: bool,
) -> int:
    target = Path(project)
    if sim:
        if not target.is_dir():
            raise FkError(BAD_REQUEST, f"{target} is not a project folder")
        run, pictures = sim_runner(target)
    else:
        project_id = target.name.removesuffix(".fkproj")
        run, pictures = server_runner(server, project_id)
    records: list[VerificationRecord] = []

    def recorded(inject: Injection | None, sid: str | None) -> VerificationRecord:
        rec = run(inject, sid)
        records.append(rec)
        return rec

    if self_test:
        report = run_self_test(recorded, surface)
        text = format_report(report)
        print(text)
        if bundle:
            print(f"diagnostic bundle: {write_bundle(Path(bundle), text, records, pictures())}")
        if not report.passed:
            raise FkError("SELF_TEST_FAILED", "At least one self-test case failed (see above).")
        return 0

    rec = recorded(None, surface)
    text = rec.model_dump_json(indent=2) if as_json else describe(rec)
    print(text)
    if bundle:
        print(f"diagnostic bundle: {write_bundle(Path(bundle), text, records, pictures())}")
    if apply:
        _apply(target, rec, sim=sim, server=server)
    return 0


def _apply(target: Path, rec: VerificationRecord, *, sim: bool, server: str) -> None:
    s = rec.surface
    if s is None or (s.suggested_quad is None and s.suggested_mask is None):
        print("nothing to apply: no suggested fix", file=sys.stderr)
        return

    def patch(project_json: dict[str, Any]) -> dict[str, Any]:
        for surf in project_json["surfaces"]:
            if surf["id"] == s.surface_id:
                if s.suggested_quad is not None:
                    surf["quad"] = [list(p) for p in s.suggested_quad]
                if s.suggested_mask is not None and surf.get("mask") is not None:
                    surf["mask"] = s.suggested_mask.model_dump(mode="json")
        return project_json

    if sim:
        from facadekit.models.project import parse_project
        from facadekit.store.project_store import load_project_dir, save_project_dir

        current = load_project_dir(target).model_dump(mode="json")
        save_project_dir(target, parse_project(patch(current)))
    else:
        import httpx

        pid = target.name.removesuffix(".fkproj")
        with httpx.Client(base_url=server.rstrip("/") + "/api/v1", timeout=30.0) as client:
            current = client.get(f"/projects/{pid}").json()
            resp = client.put(f"/projects/{pid}", json=patch(current))
            if resp.status_code >= 400:
                raise FkError("BAD_REQUEST", f"could not save the project: {resp.text[:200]}")
    print(f"applied the suggested outline to surface {s.surface_id}")
