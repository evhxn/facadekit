"""Tables for the calib3d simulator evaluation (``sim/calib3d_eval.py``).

Statistics: every scene gives one median and one 95th percentile per mapping (projector px,
over that scene's evaluation points). Tables show the median over scenes of each. Success
criteria (docs/15 section 4) are checked per scene, and a criterion counts as met in
simulation only if it holds in at least ``PASS_SHARE`` of the held-out scenes. That is
stricter than docs/15's wording (which does not say how many scenes); it never loosens it.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

FLAT_KINDS = ("flat", "board")
SOLID_KINDS = ("box", "cylinder", "stepped")
FLAT_MARGIN_PX = 0.1
SOLID_RATIO = 3.0
SOLID_P95_PX = 2.0
PASS_SHARE = 0.9
MODES = ("plane", "dense", "model", "hybrid")


def _get(row: dict[str, Any], regime: str, mode: str, group: str, stat: str) -> float:
    v = row.get(regime, {}).get(mode, {}).get(group, {}).get(stat)
    return float(v) if v is not None else math.nan


def _num(v: Any) -> float:
    return float(v) if v is not None else math.nan


def _row(*cells: str) -> str:
    return "| " + " | ".join(cells) + " |"


def _med(vals: list[float]) -> float:
    a = np.array([v for v in vals if math.isfinite(v)])
    return float(np.median(a)) if len(a) else math.nan


def _f(v: float, nd: int = 2) -> str:
    return "–" if not math.isfinite(v) else f"{v:.{nd}f}"


def flat_ok(row: dict[str, Any], regime: str = "covered") -> bool:
    hm, hp = (
        _get(row, regime, "hybrid", "object", "median"),
        _get(row, regime, "hybrid", "object", "p95"),
    )
    pm, pp = (
        _get(row, regime, "plane", "object", "median"),
        _get(row, regime, "plane", "object", "p95"),
    )
    return bool(hm <= pm + FLAT_MARGIN_PX and hp <= pp + FLAT_MARGIN_PX)


def solid_ok(row: dict[str, Any], regime: str = "covered") -> tuple[bool, bool]:
    hm = _get(row, regime, "hybrid", "object", "median")
    pm = _get(row, regime, "plane", "object", "median")
    ratio_ok = bool(math.isfinite(hm) and math.isfinite(pm) and pm >= SOLID_RATIO * max(hm, 1e-9))
    p95_ok = bool(
        _get(row, regime, "hybrid", "object", "p95") < SOLID_P95_PX
        and _get(row, regime, "hybrid", "all", "p95") < SOLID_P95_PX
    )
    return ratio_ok, p95_ok


def _mapping_table(rows: list[dict[str, Any]], regime: str, kinds: tuple[str, ...]) -> list[str]:
    out = [
        "| Scene | Scenes | plane | dense (current map) | model (depth only) | hybrid (calib3d) |",
        "|---|---|---|---|---|---|",
    ]
    for kind in kinds:
        rs = [r for r in rows if r["kind"] == kind]
        if not rs:
            continue
        cells = []
        for mode in MODES:
            m = _med([_get(r, regime, mode, "object", "median") for r in rs])
            p = _med([_get(r, regime, mode, "object", "p95") for r in rs])
            cells.append(f"{_f(m)} / {_f(p)}")
        out.append(f"| {kind} | {len(rs)} | " + " | ".join(cells) + " |")
    return out


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    main = [r for r in rows if r["tag"] == "main" and r["variant"] == "base"]
    held = [r for r in main if r["heldout"]]
    train = [r for r in main if not r["heldout"]]
    crit: dict[str, Any] = {}
    for kind in FLAT_KINDS:
        rs = [r for r in held if r["kind"] == kind]
        if rs:
            share = float(np.mean([flat_ok(r) for r in rs]))
            crit[f"flat:{kind}"] = {"scenes": len(rs), "share": share, "met": share >= PASS_SHARE}
    for kind in SOLID_KINDS:
        rs = [r for r in held if r["kind"] == kind]
        if rs:
            oks = [solid_ok(r) for r in rs]
            ratio = float(np.mean([a for a, _ in oks]))
            p95 = float(np.mean([b for _, b in oks]))
            both = float(np.mean([a and b for a, b in oks]))
            crit[f"solid:{kind}"] = {
                "scenes": len(rs), "ratio_share": ratio, "p95_share": p95, "share": both,
                "met": both >= PASS_SHARE,
            }  # fmt: skip
    per_kind: dict[str, Any] = {}
    for kind in FLAT_KINDS + SOLID_KINDS:
        rs = [r for r in held if r["kind"] == kind]
        if not rs:
            continue
        entry: dict[str, Any] = {
            "scenes": len(rs),
            "status": dict(Counter(r["status"] for r in rs)),
        }
        for regime in ("covered", "gaps"):
            entry[regime] = {
                mode: {
                    "median": _med([_get(r, regime, mode, "object", "median") for r in rs]),
                    "p95": _med([_get(r, regime, mode, "object", "p95") for r in rs]),
                }
                for mode in MODES
            }
        pe = [r["projector_error"] for r in rs if "projector_error" in r]
        if pe:
            entry["projector_error"] = {k: _med([p[k] for p in pe]) for k in pe[0]}
            solved = [r["solve"] for r in rs if "intrinsics" in r["solve"]]
            entry["solve_reprojection"] = {
                "median": _med([_num(s.get("reprojection_median_px")) for s in solved]),
                "p95": _med([_num(s.get("reprojection_p95_px")) for s in solved]),
                "inlier_fraction": _med([s["inlier_fraction"] for s in solved]),
            }
        per_kind[kind] = entry
    return {"heldout_scenes": len(held), "train_scenes": len(train), "criteria": crit,
            "per_kind": per_kind}  # fmt: skip


def _sens_table(rows: list[dict[str, Any]]) -> list[str]:
    sens = [
        r
        for r in rows
        if r["tag"] == "main" and r["heldout"] and r["kind"] in ("board",) + SOLID_KINDS
    ]
    names: list[str] = []
    for r in sens:
        if r["variant"] not in names:
            names.append(r["variant"])
    ids = {(r["kind"], r["index"]) for r in sens if r["variant"] != "base"}
    out = [
        _row(
            "Variant",
            "Runs",
            "Status (ok/weak/planar/other)",
            "3D objects: plane",
            "model",
            "hybrid",
            "3D criteria met",
            "board: hybrid − plane (median / p95)",
        ),  # fmt: skip
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in names:
        rs = [r for r in sens if r["variant"] == name and (r["kind"], r["index"]) in ids]
        if not rs:
            continue
        st = Counter(r["status"] for r in rs)
        other = sum(v for k, v in st.items() if k not in ("ok", "weak", "planar"))
        solid = [r for r in rs if r["kind"] in SOLID_KINDS]
        cells = []
        for mode in ("plane", "model", "hybrid"):
            m = _med([_get(r, "covered", mode, "object", "median") for r in solid])
            p = _med([_get(r, "covered", mode, "object", "p95") for r in solid])
            cells.append(f"{_f(m)} / {_f(p)}")
        met = [all(solid_ok(r)) for r in solid]
        boards = [r for r in rs if r["kind"] == "board"]
        dm = _med(
            [
                _get(r, "covered", "hybrid", "object", "median")
                - _get(r, "covered", "plane", "object", "median")
                for r in boards
            ]
        )
        dp = _med(
            [
                _get(r, "covered", "hybrid", "object", "p95")
                - _get(r, "covered", "plane", "object", "p95")
                for r in boards
            ]
        )
        out.append(
            f"| {name} | {len(rs)} | "
            f"{st.get('ok', 0)}/{st.get('weak', 0)}/{st.get('planar', 0)}/{other} | "
            + " | ".join(cells)
            + f" | {sum(met)}/{len(met)} | {_f(dm, 3)} / {_f(dp, 3)} |"
        )
    return out


def _range_table(rows: list[dict[str, Any]]) -> list[str]:
    rr = [r for r in rows if r["tag"] == "range"]
    out = [
        _row(
            "Phone to wall",
            "Projector to wall",
            "Runs",
            "Depth coverage",
            "Status (ok/weak/planar/other)",
            "plane",
            "model",
            "hybrid",
            "3D/flat criteria met",
        ),  # fmt: skip
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for dist in sorted({r["distance_m"] for r in rr}):
        rs = [r for r in rr if abs(r["distance_m"] - dist) < 1e-6]
        st = Counter(r["status"] for r in rs)
        other = sum(v for k, v in st.items() if k not in ("ok", "weak", "planar"))
        cells = []
        for mode in ("plane", "model", "hybrid"):
            m = _med([_get(r, "covered", mode, "object", "median") for r in rs])
            p = _med([_get(r, "covered", mode, "object", "p95") for r in rs])
            cells.append(f"{_f(m)} / {_f(p)}")
        met = [all(solid_ok(r)) if r["kind"] in SOLID_KINDS else flat_ok(r) for r in rs]
        cov = _med([r["depth_coverage"] for r in rs])
        pd = _med([r["projector_distance_m"] for r in rs])
        out.append(
            f"| {dist:g} m | {_f(pd, 1)} m | {len(rs)} | {cov * 100:.0f} % | "
            f"{st.get('ok', 0)}/{st.get('weak', 0)}/{st.get('planar', 0)}/{other} | "
            + " | ".join(cells)
            + f" | {sum(met)}/{len(met)} |"
        )
    return out


def report_markdown(rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    s = summarise(rows)
    main = [r for r in rows if r["tag"] == "main" and r["variant"] == "base"]
    held = [r for r in main if r["heldout"]]
    train = [r for r in main if not r["heldout"]]
    kinds = FLAT_KINDS + SOLID_KINDS
    lines = [
        "# calib3d simulator evaluation (spike S-11 Part A)",
        "",
        "**SIMULATED. No real depth sensor, phone, projector or wall was used.** The depth-sensor",
        "model (`sim/scene3d.py`, `DepthSensorModel`) is ASSUMED: about 256x192 depth, centimetre",
        "noise growing with distance plus a smooth correlated error, 5 m range, dropout on dark,",
        "glossy and transparent materials, flying pixels at depth edges, phone pose error 0.15° /",
        "3 mm per shot. The real device decides (docs/15 Part C).",
        "",
        f"Scenes: {len(held)} held out (reported), {len(train)} train (constants chosen on these).",
        "Numbers are projector pixels at 1920x1080: median over scenes of each scene's median /",
        "95th percentile, on the target (the board, the box, the cylinder, the steps; the wall for",
        "`flat`). `covered`: every lit pixel, all scan data used. `gaps`: 96 px camera tiles whose",
        "scan data was removed before fitting (what each mapping predicts where the scan has no",
        "data). `dense` is FacadeKit's current dense map (`calib.maps`), which marks such regions",
        "as unmeasured instead of trusting them.",
        "",
        "## Held-out scenes, covered",
        "",
        *_mapping_table(held, "covered", kinds),
        "",
        "## Held-out scenes, gaps in the scan",
        "",
        *_mapping_table(held, "gaps", kinds),
        "",
        "## Success criteria (docs/15 section 4), held-out scenes, in simulation",
        "",
        f"A criterion is met only if it holds in at least {PASS_SHARE * 100:.0f} % of the held-out",
        "scenes of that kind (stricter than docs/15, which does not say how many).",
        "",
        "| Criterion | Scene | Scenes | Holds in | Met in simulation |",
        "|---|---|---|---|---|",
    ]
    for key, c in s["criteria"].items():
        typ, kind = key.split(":")
        if typ == "flat":
            label = (
                f"3D mapping not worse than plane by more than {FLAT_MARGIN_PX} px (median and p95)"
            )
            holds = f"{c['share'] * 100:.0f} %"
        else:
            label = (
                f"median ≥ {SOLID_RATIO:g}x lower than plane, and p95 < {SOLID_P95_PX:g} px "
                "on the object and on all lit points"
            )
            holds = (f"{c['share'] * 100:.0f} % (ratio {c['ratio_share'] * 100:.0f} %, "
                     f"p95 {c['p95_share'] * 100:.0f} %)")  # fmt: skip
        lines.append(
            f"| {label} | {kind} | {c['scenes']} | {holds} | {'yes' if c['met'] else 'NO'} |"
        )
    lines += [
        "",
        "Not evaluated in Part A (they need the device): maximum useful range and the surfaces",
        "where depth fails, *measured*; move detection (5 mm shift at under 2 % false alarms).",
        "The range and material rows below are the simulator's assumptions played back, not",
        "measurements.",
        "",
        "## Projector self-calibration, held-out scenes",
        "",
        _row(
            "Scene",
            "Status",
            "fx error",
            "cx / cy error",
            "position",
            "rotation",
            "solve reprojection median / p95",
            "inliers",
        ),  # fmt: skip
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind in kinds:
        e = s["per_kind"].get(kind)
        if not e:
            continue
        st = ", ".join(f"{k} {v}" for k, v in sorted(e["status"].items()))
        pe = e.get("projector_error")
        if pe:
            sr = e["solve_reprojection"]
            lines.append(
                _row(
                    kind,
                    st,
                    f"{pe['fx_rel'] * 100:.1f} %",
                    f"{_f(pe['cx_px'], 1)} / {_f(pe['cy_px'], 1)} px",
                    f"{pe['position_m'] * 1000:.0f} mm",
                    f"{_f(pe['rotation_deg'])}°",
                    f"{_f(sr['median'])} / {_f(sr['p95'])} px",
                    f"{sr['inlier_fraction'] * 100:.0f} %",
                )
            )
        else:
            lines.append(f"| {kind} | {st} | – | – | – | – | – | – |")
    lines += [
        "",
        "`planar`: the lit surface is one plane, the projector cannot be solved (under-",
        "constrained) and the plane mapping is used, which is exact there. Pose errors are",
        "measured in the frame of the phone's reported pose.",
        "",
        "## Sensitivity (held-out scenes with sweeps; covered)",
        "",
        "3D objects = box, cylinder, steps (median / p95 as above). Board column: median over",
        "board scenes of (hybrid − plane); the flat criterion allows up to +0.1.",
        "",
        *_sens_table(rows),
        "",
        "## Range (held-out seeds, board, box and cylinder; covered)",
        "",
        "The phone stands at the given distance; the projector further back. Depth beyond the",
        "sensor's 5 m (ASSUMED) is missing, so the 3D model cannot be solved there.",
        "",
        *_range_table(rows),
        "",
        "## Train scenes (for comparison; constants were chosen on these)",
        "",
        *_mapping_table(train, "covered", kinds),
        "",
    ]
    secs = [r["solve_s"] for r in main]
    if secs:
        lines.append(
            "Solve time per scene (decode, fusion, projector, mapping; one CPU core): "
            f"median {_med(secs):.1f} s."
        )
    return "\n".join(lines) + "\n", s


def write_report(rows: list[dict[str, Any]], out: Path) -> str:
    out.mkdir(parents=True, exist_ok=True)
    md, summary = report_markdown(rows)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.json").write_text(json.dumps(summary, indent=1, default=float), encoding="utf-8")
    return md
