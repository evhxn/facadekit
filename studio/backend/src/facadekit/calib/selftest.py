"""`facadekit verify --self-test`: put known errors into the check and see them come back.

Nothing is moved by hand. The projected pictures are shifted in software (a projector that
moved), the camera pictures are shifted in software (a camera that was bumped), and the
surface outline is grown or shrunk (a selection that is too big or too small). On a real rig
this runs the whole optical path: projector, wall, camera, exposure, the scan's map.
Surface cases are compared with the run without an error, so the user's own outline does not
need to be perfect for the self-test to pass.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from facadekit.calib.verify import Injection
from facadekit.models.verification import VerificationRecord

Runner = Callable[[Injection | None, str | None], VerificationRecord]

SHIFT_TOL = 0.25  # relative tolerance on an injected rig shift
SHIFT_TOL_PX = 1.0  # ... and at least this many px
GROW_TOL_PX = 2.5  # tolerance on an injected outline change, projector px


@dataclass
class Row:
    name: str
    expected: str
    measured: str
    status: str  # "PASS" | "FAIL" | "SKIP" | "INFO"
    record: VerificationRecord | None = None


@dataclass
class SelfTestReport:
    rows: list[Row] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.status != "FAIL" for r in self.rows) and any(
            r.status == "PASS" for r in self.rows
        )


def _near(value: float | None, want: float) -> bool:
    return value is not None and abs(value - want) <= max(SHIFT_TOL_PX, SHIFT_TOL * want)


def _fmt(v: float | None, unit: str = "px") -> str:
    return f"{v:.2f} {unit}" if v is not None else "?"


def run_self_test(run: Runner, surface_id: str | None) -> SelfTestReport:
    rep = SelfTestReport()

    def attempt(name: str, inject: Injection | None, sid: str | None) -> VerificationRecord | None:
        try:
            return run(inject, sid)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a FAIL row
            code = getattr(exc, "code", type(exc).__name__)
            message = getattr(exc, "message", str(exc))
            rep.rows.append(Row(name, "a result", f"error {code}: {message}", "FAIL"))
            return None

    base = attempt("rig, nothing changed", None, None)
    if base is None:
        return rep
    rep.rows.append(
        Row(
            "rig, nothing changed",
            "stable",
            f"{base.rig.verdict} (projector {_fmt(base.rig.projector_shift_px)}, camera "
            f"{_fmt(base.rig.camera_shift_px)}, dots {base.rig.dots_found}/"
            f"{base.rig.dots_expected})",
            "PASS" if base.rig.verdict == "stable" else "FAIL",
            base,
        )
    )
    for dx, dy in ((6.0, 0.0), (0.0, -3.0)):
        want = float((dx**2 + dy**2) ** 0.5)
        name = f"projector moved {dx:+.0f},{dy:+.0f} px (software)"
        rec = attempt(name, Injection(proj_shift=(dx, dy)), None)
        if rec is None:
            continue
        got = rec.rig.projector_shift_px
        if got is None:  # camera motion unknown: only the total can be checked
            got = rec.rig.total_shift_px
        ok = rec.rig.verdict in ("projector_moved", "moved") and _near(got, want)
        rep.rows.append(
            Row(
                name,
                f"projector moved, {want:.1f} px",
                f"{rec.rig.verdict}, {_fmt(got)}",
                "PASS" if ok else "FAIL",
                rec,
            )
        )
    name = "camera moved 5 px (software)"
    rec = attempt(name, Injection(cam_shift=(5.0, 0.0)), None)
    if rec is not None:
        if rec.rig.texture_method is None:
            rep.rows.append(
                Row(
                    name,
                    "camera moved, 5.0 px",
                    f"{rec.rig.verdict}: the wall has too little texture to tell the camera "
                    "from the projector",
                    "SKIP",
                    rec,
                )
            )
        else:
            ok = (
                rec.rig.verdict == "camera_moved"
                and _near(rec.rig.camera_shift_px, 5.0)
                and rec.rig.projector == "stable"
            )
            rep.rows.append(
                Row(
                    name,
                    "camera moved 5.0 px, projector stable",
                    f"{rec.rig.verdict}, camera {_fmt(rec.rig.camera_shift_px)}, projector "
                    f"{_fmt(rec.rig.projector_shift_px)}",
                    "PASS" if ok else "FAIL",
                    rec,
                )
            )
    if surface_id is None:
        rep.rows.append(Row("surface cases", "-", "no surface chosen", "SKIP"))
        return rep
    sbase = attempt("surface as drawn", None, surface_id)
    if sbase is None or sbase.surface is None:
        return rep
    s0 = sbase.surface
    usable = s0.object_confidence >= 0.8 and any(e.spill_px is not None for e in s0.edges)
    rep.rows.append(
        Row(
            "surface as drawn",
            "a measurement",
            f"{s0.verdict}, worst edge {_fmt(s0.max_edge_px)}, object clarity "
            f"{s0.object_confidence:.2f}",
            "INFO" if usable else "SKIP",
            sbase,
        )
    )
    if not usable:
        return rep
    base_spill = {e.side: e.spill_px for e in s0.edges if e.spill_px is not None}
    for grow in (8.0, -6.0):
        name = f"outline {'grown' if grow > 0 else 'shrunk'} {abs(grow):.0f} px"
        rec = attempt(name, Injection(grow_px=grow), surface_id)
        if rec is None or rec.surface is None:
            continue
        deltas = [
            e.spill_px - base_spill[e.side]
            for e in rec.surface.edges
            if e.spill_px is not None and e.side in base_spill
        ]
        if not deltas:
            rep.rows.append(Row(name, f"{grow:+.0f} px", "no edge measured", "FAIL", rec))
            continue
        worst = max(abs(d - grow) for d in deltas)
        mean = sum(deltas) / len(deltas)
        ok = worst <= GROW_TOL_PX
        rep.rows.append(
            Row(
                name,
                f"every edge {grow:+.0f} px",
                f"{mean:+.2f} px (worst side off by {worst:.2f})",
                "PASS" if ok else "FAIL",
                rec,
            )
        )
    return rep


def format_report(rep: SelfTestReport) -> str:
    widths = [34, 30, 58, 6]
    head = ["case", "expected", "measured", "result"]
    lines = ["  ".join(h.ljust(w) for h, w in zip(head, widths, strict=True))]
    lines.append("  ".join("-" * w for w in widths))
    for r in rep.rows:
        cells = [r.name, r.expected, r.measured, r.status]
        lines.append("  ".join(c[:w].ljust(w) for c, w in zip(cells, widths, strict=True)))
    lines.append("")
    lines.append("SELF-TEST " + ("PASSED" if rep.passed else "FAILED"))
    return "\n".join(lines)
