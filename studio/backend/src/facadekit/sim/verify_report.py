"""Tables, threshold choice and the learned-model comparison for `verify_eval` (docs/13).

Everything is computed from the stored runs. Thresholds are chosen on the train scenes only
and then scored once on the held-out scenes. The learned alternative is a logistic
regression (NumPy, no new dependency); it ships only if it beats the rule on held-out scenes
with a 95 % paired-bootstrap interval that excludes zero.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from facadekit.calib import rigcheck as rc
from facadekit.calib import surfacecheck as sc

Row = dict[str, Any]
MOVED_PX = 0.75  # a true move this large (or larger) should be reported
TRUE_PASS_PX = 2.5  # product classes from the true worst edge (projector px)
TRUE_FAIL_PX = 6.0
BOOT = 2000


def _ok(r: Row) -> bool:
    return "rig" in r and r.get("error") is None


def _split(rows: list[Row], held: bool) -> list[Row]:
    return [r for r in rows if _ok(r) and bool(r["held_out"]) == held]


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f} % ({n}/{d})" if d else "–"


def _q(values: Sequence[float], q: float) -> float:
    return float(np.percentile(values, q)) if len(values) else float("nan")


def _table(head: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


# --- rig ----------------------------------------------------------------------------------


def proj_detected(r: Row, t: float = rc.PROJ_MOVED_PX) -> bool:
    rig = r["rig"]
    if rig["verdict"] == "unknown":
        return True  # "cannot judge" is never a silent pass
    shift = rig["projector_shift_px"]
    if shift is None:
        shift = rig["total_shift_px"]
    return bool(shift is not None and shift > t)


def _bucket_rows(rows: list[Row], key: str, edges: list[float]) -> list[tuple[str, list[Row]]]:
    out = [("0 (none)", [r for r in rows if r["truth"][key] < 1e-6])]
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        label = f"{lo:g}–{hi:g}" if hi < 1e9 else f">= {lo:g}"
        out.append(
            (label, [r for r in rows if lo <= r["truth"][key] < hi and r["truth"][key] > 1e-6])
        )
    return out


def rig_tables(rows: list[Row]) -> tuple[str, dict[str, Any]]:
    edges = [1e-6, 0.75, 1.5, 3.0, 6.0, 1e10]
    data: dict[str, Any] = {}
    lines = []
    proj_rows = [r for r in rows if r["truth"]["cam_shift"] < 1e-6]
    table = []
    for label, sel in _bucket_rows(proj_rows, "proj_shift", edges):
        hit = sum(proj_detected(r) for r in sel)
        table.append([label, str(len(sel)), _pct(hit, len(sel))])
        data[f"projector {label}"] = [hit, len(sel)]
    lines.append("Projector moved (camera still), by true shift in projector px:\n")
    lines.append(_table(["true shift", "runs", "reported as moved"], table))
    cam_rows = [r for r in rows if r["truth"]["proj_shift"] < 1e-6]
    table = []
    for label, sel in _bucket_rows(cam_rows, "cam_shift", edges):
        hit = sum(_cam_moved(r) for r in sel)
        table.append([label, str(len(sel)), _pct(hit, len(sel))])
        data[f"camera {label}"] = [hit, len(sel)]
    lines.append("\nCamera moved (projector still), by true shift in camera px:\n")
    lines.append(_table(["true shift", "runs", "reported as moved"], table))

    # which device: truth class against the verdict, moves of at least 1 px only
    def truth_class(r: Row) -> str | None:
        c, p = r["truth"]["cam_shift"], r["truth"]["proj_shift"]
        c = 0.0 if c < 1e-6 else c
        p = 0.0 if p < 1e-6 else p
        if (0 < c < 1.0) or (0 < p < 1.0):
            return None
        return {
            (False, False): "stable",
            (True, False): "camera",
            (False, True): "projector",
            (True, True): "both",
        }[(c >= 1.0, p >= 1.0)]

    verdicts = ["stable", "camera_moved", "projector_moved", "both_moved", "moved", "unknown"]
    conf = {k: dict.fromkeys(verdicts, 0) for k in ("stable", "camera", "projector", "both")}
    for r in rows:
        k = truth_class(r)
        if k is not None:
            conf[k][r["rig"]["verdict"]] += 1
    table = [[k] + [str(conf[k][v]) for v in verdicts] for k in conf]
    lines.append("\nWhich device moved (moves of 1 px or more; rows: truth, columns: verdict):\n")
    lines.append(_table(["truth"] + verdicts, table))
    data["confusion"] = conf
    pe = [
        abs(r["rig"]["projector_shift_px"] - r["truth"]["proj_shift"])
        for r in rows
        if r["rig"]["projector_shift_px"] is not None and r["truth"]["proj_shift"] >= 0.75
    ]
    ce = [
        abs(r["rig"]["camera_shift_px"] - r["truth"]["cam_shift"])
        for r in rows
        if r["rig"]["camera_shift_px"] is not None and r["truth"]["cam_shift"] >= 0.75
    ]
    unknown_tex = sum(r["rig"]["texture_method"] is None for r in rows)
    lines.append(
        f"\nShift estimate error: projector median {_q(pe, 50):.2f} px, 95th pct {_q(pe, 95):.2f} "
        f"px ({len(pe)} runs); camera median {_q(ce, 50):.2f} px, 95th pct {_q(ce, 95):.2f} px "
        f"({len(ce)} runs). Camera motion unknown (too little wall texture): "
        f"{_pct(unknown_tex, len(rows))}."
    )
    data["proj_err"] = [_q(pe, 50), _q(pe, 95), len(pe)]
    data["cam_err"] = [_q(ce, 50), _q(ce, 95), len(ce)]
    data["texture_unknown"] = [unknown_tex, len(rows)]
    return "\n".join(lines), data


def _cam_moved(r: Row, t: float = rc.CAM_MOVED_PX) -> bool:
    rig = r["rig"]
    if rig["verdict"] in ("moved", "unknown"):
        return True
    shift = rig["camera_shift_px"]
    return bool(shift is not None and shift > t)


# --- surface ------------------------------------------------------------------------------


def true_worst(r: Row) -> float:
    e = r["truth"]["edges"]
    return max(max(abs(v["lo"]), abs(v["hi"])) for v in e.values()) if e else 0.0


def true_class(r: Row) -> str:
    w = true_worst(r)
    return "pass" if w <= TRUE_PASS_PX else "warn" if w <= TRUE_FAIL_PX else "fail"


def est_verdict(r: Row, t_pass: float = sc.PASS_EDGE_PX, t_warn: float = sc.WARN_EDGE_PX) -> str:
    """The product's verdict re-derived from the stored numbers with other thresholds."""
    s = r["surface"]
    worst = s["max_edge_px"]
    if s["object_confidence"] < sc.OBJECT_CONF_MIN or worst is None:
        return str(s["verdict"])
    v = "pass" if worst <= t_pass else "warn" if worst <= t_warn else "fail"
    if v == "pass" and (s["flatness_warning"] or any(e["spill_px"] is None for e in s["edges"])):
        v = "warn"
    return v


def side_errors(rows: list[Row]) -> tuple[list[float], int, int]:
    errs: list[float] = []
    measured = total = 0
    for r in rows:
        truth = r["truth"]["edges"]
        for e in r["surface"]["edges"]:
            if e["side"] not in truth:
                continue
            total += 1
            if e["spill_px"] is None:
                continue
            measured += 1
            errs.append(abs(e["spill_px"] - truth[e["side"]]["median"]))
    return errs, measured, total


def surface_tables(rows: list[Row]) -> tuple[str, dict[str, Any]]:
    data: dict[str, Any] = {}
    lines = []
    groups: list[tuple[str, Callable[[Row], bool]]] = [
        ("all", lambda r: True),
        ("flat door", lambda r: not r["params"]["raised"]),
        ("door standing out (parallax)", lambda r: bool(r["params"]["raised"])),
        ("textured door", lambda r: r["params"]["texture"] > 0),
        ("plain wall (no brick)", lambda r: not r["params"]["mortar"]),
        (
            "rig stable",
            lambda r: r["truth"]["cam_shift"] < 1e-6 and r["truth"]["proj_shift"] < 1e-6,
        ),
        ("projector moved", lambda r: r["truth"]["proj_shift"] > 1e-6),
        ("camera moved", lambda r: r["truth"]["cam_shift"] > 1e-6),
        ("auto-exposure camera", lambda r: r["case"]["kind"] == "autoexposure"),
        ("dim (ambient 0.35)", lambda r: r["case"]["kind"] == "dim"),
        ("bright (gain 1.6)", lambda r: r["case"]["kind"] == "bright"),
    ]
    table = []
    for name, pred in groups:
        sel = [r for r in rows if pred(r)]
        errs, m, t = side_errors(sel)
        table.append(
            [
                name,
                str(len(sel)),
                _pct(m, t),
                f"{_q(errs, 50):.2f}",
                f"{_q(errs, 90):.2f}",
                f"{_q(errs, 95):.2f}",
            ]
        )
        data[f"edge_error {name}"] = [_q(errs, 50), _q(errs, 90), _q(errs, 95), m, t]
    lines.append("Edge position error, |measured - true| spill per side (projector px):\n")
    lines.append(
        _table(["scenes", "runs", "sides measured", "median", "90th pct", "95th pct"], table)
    )

    classes = ["pass", "warn", "fail"]
    conf = {k: dict.fromkeys(classes, 0) for k in classes}
    for r in rows:
        conf[true_class(r)][r["surface"]["verdict"]] += 1
    table = [[k] + [str(conf[k][v]) for v in classes] for k in classes]
    lines.append(
        f"\nVerdict (rows: truth from the true worst edge, pass <= {TRUE_PASS_PX} px, fail > "
        f"{TRUE_FAIL_PX} px; columns: the check):\n"
    )
    lines.append(_table(["truth"] + classes, table))
    n = len(rows)
    false_pass = conf["fail"]["pass"]
    false_fail = conf["pass"]["fail"]
    lines.append(
        f"\nFalse pass (truly off by more than {TRUE_FAIL_PX} px, reported pass): "
        f"{_pct(false_pass, sum(conf['fail'].values()))}. False fail (truly within "
        f"{TRUE_PASS_PX} px, reported fail): {_pct(false_fail, sum(conf['pass'].values()))}. "
        f"Agreement: {_pct(sum(conf[k][k] for k in classes), n)}."
    )
    data["verdict_confusion"] = conf

    offered = [r for r in rows if r["surface"].get("suggested_quad")]
    good = []
    for r in offered:
        sug = np.asarray(r["surface"]["suggested_quad"])
        truth = np.asarray(r["truth"]["quad_now"])
        drawn = np.asarray(r["truth"]["drawn"])
        good.append(
            (
                float(np.max(np.linalg.norm(sug - truth, axis=1))),
                float(np.max(np.linalg.norm(drawn - truth, axis=1))),
            )
        )
    after = [g[0] for g in good]
    worse = sum(g[0] > g[1] for g in good)
    need = [r for r in rows if true_class(r) != "pass"]
    lines.append(
        f"\nSuggested fix offered in {_pct(len(offered), len(need))} of the runs that needed one; "
        f"corner error after the fix: median {_q(after, 50):.2f} px, "
        f"90th pct {_q(after, 90):.2f} px, "
        f"max {max(after) if after else float('nan'):.2f} px; the fix was worse than the drawn "
        f"outline in {_pct(worse, len(good))}."
    )
    data["suggestion"] = {
        "offered": len(offered),
        "needed": len(need),
        "after_median": _q(after, 50),
        "after_p90": _q(after, 90),
        "worse": worse,
    }
    return "\n".join(lines), data


# --- thresholds from the train scenes -----------------------------------------------------


def choose_rig_threshold(train: list[Row], kind: str) -> float:
    """Smallest threshold (0.3..3 px, 0.05 steps) with no false alarm on train runs where that
    device did not move."""
    if kind == "projector":
        still = [
            r for r in train if r["truth"]["proj_shift"] < 1e-6 and r["rig"]["verdict"] != "unknown"
        ]
        values = [
            r["rig"]["projector_shift_px"]
            if r["rig"]["projector_shift_px"] is not None
            else r["rig"]["total_shift_px"]
            for r in still
        ]
    else:
        still = [r for r in train if r["truth"]["cam_shift"] < 1e-6 and r["rig"]["texture_method"]]
        values = [r["rig"]["camera_shift_px"] for r in still]
    vals = [v for v in values if v is not None]
    for t in np.arange(0.3, 3.0001, 0.05):
        if all(v <= t for v in vals):
            return float(round(t, 2))
    return 3.0


def choose_pass_threshold(train: list[Row]) -> float:
    """Largest PASS threshold (estimated worst edge) with no false pass on train and at most
    1 % of truly-off outlines (worse than TRUE_PASS_PX) passed."""
    best = 1.0
    for t in np.arange(1.0, 4.0001, 0.1):
        fp = sum(true_class(r) == "fail" and est_verdict(r, t) == "pass" for r in train)
        off = [r for r in train if true_class(r) != "pass"]
        lenient = sum(est_verdict(r, t) == "pass" for r in off)
        if fp == 0 and lenient <= 0.01 * max(len(off), 1):
            best = float(round(t, 2))
    return best


# --- learned alternative ------------------------------------------------------------------


def _logit_fit(
    x: np.ndarray, y: np.ndarray, l2: float = 1.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu, sd = x.mean(axis=0), x.std(axis=0) + 1e-9
    z = np.hstack([np.ones((len(x), 1)), (x - mu) / sd])
    w = np.zeros(z.shape[1])
    for _ in range(50):  # Newton steps
        p = 1 / (1 + np.exp(-np.clip(z @ w, -30, 30)))
        g = z.T @ (p - y) + l2 * np.r_[0, w[1:]]
        hess = z.T @ (z * (p * (1 - p))[:, None]) + l2 * np.diag(np.r_[0, np.ones(len(w) - 1)])
        w -= np.linalg.lstsq(hess, g, rcond=None)[0]
    return w, mu, sd


def _logit_predict(model: tuple[np.ndarray, np.ndarray, np.ndarray], x: np.ndarray) -> np.ndarray:
    w, mu, sd = model
    z = np.hstack([np.ones((len(x), 1)), (x - mu) / sd])
    return np.asarray(1 / (1 + np.exp(-np.clip(z @ w, -30, 30))))


def _paired_ci(
    a: np.ndarray, b: np.ndarray, groups: np.ndarray, seed: int = 0
) -> tuple[float, float, float]:
    """Mean of (a - b) and its 95 % bootstrap interval, resampling whole scenes."""
    rng = np.random.default_rng(seed)
    d = a.astype(float) - b.astype(float)
    ids = np.unique(groups)
    sums = np.array([d[groups == g].sum() for g in ids])
    cnts = np.array([(groups == g).sum() for g in ids])
    pick = rng.integers(0, len(ids), (BOOT, len(ids)))
    means = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi)


def _rig_features(r: Row) -> list[float]:
    g = r["rig"]
    p = g["projector_shift_px"] if g["projector_shift_px"] is not None else g["total_shift_px"]
    return [
        p or 0.0,
        g["total_shift_px"] or 0.0,
        g["camera_shift_px"] or 0.0,
        g["dots_found"] / max(g["dots_expected"], 1),
        1.0 if g["texture_method"] else 0.0,
    ]


def _surf_features(r: Row) -> list[float]:
    s = r["surface"]
    return [
        s["max_edge_px"] if s["max_edge_px"] is not None else 10.0,
        s["object_confidence"],
        s["parallax_px"] or 0.0,
        s["inner_residual_px"] or 0.0,
        s["leakage"] or 0.0,
        s["uncovered"] or 0.0,
        s["outline_measured"],
    ]


def ml_comparison(train: list[Row], held: list[Row]) -> tuple[str, dict[str, Any]]:
    lines = []
    data: dict[str, Any] = {}

    # 1. projector moved (>= MOVED_PX) or not; ambiguous tiny moves left out
    def proj_label(r: Row) -> int | None:
        t = r["truth"]["proj_shift"]
        return 1 if t >= MOVED_PX else 0 if t < 1e-6 else None

    def fit_eval(
        label: Callable[[Row], int | None],
        feats: Callable[[Row], list[float]],
        rule: Callable[[Row], bool],
        name: str,
    ) -> None:
        tr = [r for r in train if label(r) is not None]
        te = [r for r in held if label(r) is not None]
        if not tr or not te:
            return
        model = _logit_fit(
            np.array([feats(r) for r in tr]), np.array([label(r) for r in tr], float)
        )
        ml = _logit_predict(model, np.array([feats(r) for r in te])) > 0.5
        y = np.array([label(r) for r in te]) == 1
        rule_pred = np.array([rule(r) for r in te])
        acc_ml = ml == y
        acc_rule = rule_pred == y
        mean, lo, hi = _paired_ci(acc_ml, acc_rule, np.array([r["index"] for r in te]))
        lines.append(
            f"- {name}: held-out accuracy rule {acc_rule.mean() * 100:.1f} %, logistic "
            f"{acc_ml.mean() * 100:.1f} %; difference {mean * 100:+.1f} points "
            f"(95 % CI {lo * 100:+.1f} to {hi * 100:+.1f}), {len(te)} runs in "
            f"{len(set(r['index'] for r in te))} scenes."
        )
        miss_rule = int((~rule_pred & y).sum())
        miss_ml = int((~ml & y).sum())
        lines.append(
            f"  - missed (truly positive, called negative): rule {miss_rule}, logistic {miss_ml} "
            f"of {int(y.sum())}; false alarms: rule {int((rule_pred & ~y).sum())}, logistic "
            f"{int((ml & ~y).sum())} of {int((~y).sum())}"
        )
        flat = np.array([not r["params"]["raised"] for r in te])
        if flat.any() and (~flat).any():
            lines.append(
                f"  - flat doors only: rule {acc_rule[flat].mean() * 100:.1f} %, logistic "
                f"{acc_ml[flat].mean() * 100:.1f} %; doors standing out: rule "
                f"{acc_rule[~flat].mean() * 100:.1f} %, logistic {acc_ml[~flat].mean() * 100:.1f} %"
            )
        data[name] = {
            "rule": float(acc_rule.mean()),
            "ml": float(acc_ml.mean()),
            "diff": mean,
            "ci": [lo, hi],
            "n": len(te),
        }

    fit_eval(proj_label, _rig_features, proj_detected, "projector moved")

    def surf_label(r: Row) -> int:
        return 1 if true_class(r) != "pass" else 0

    fit_eval(
        surf_label,
        _surf_features,
        lambda r: r["surface"]["verdict"] != "pass",
        "surface needs fixing (rule: verdict is not pass)",
    )
    fit_eval(
        surf_label,
        _surf_features,
        lambda r: (
            (r["surface"]["max_edge_px"] if r["surface"]["max_edge_px"] is not None else 99.0)
            > sc.PASS_EDGE_PX
        ),
        "surface needs fixing (rule: worst edge > PASS_EDGE_PX)",
    )
    return "\n".join(lines), data


def report(rows: list[Row], json_out: Path | None = None) -> str:
    skipped = [r for r in rows if "skipped" in r]
    errors = [r for r in rows if r.get("error")]
    good = [r for r in rows if _ok(r)]
    train, held = _split(rows, False), _split(rows, True)
    scenes = sorted({r["index"] for r in rows})
    out = [
        "# Verify surface: simulator evaluation",
        "",
        f"{len(scenes)} scenes ({len({r['index'] for r in train})} train, "
        f"{len({r['index'] for r in held})} held out), {len(skipped)} skipped because their scan "
        f"failed; {len(good)} checks run, {len(errors)} refused with an error "
        f"({', '.join(sorted({str(r['error']) for r in errors})) or 'none'}).",
        "",
    ]
    data: dict[str, Any] = {"scenes": len(scenes), "runs": len(good), "errors": len(errors)}
    t_proj = choose_rig_threshold(train, "projector")
    t_cam = choose_rig_threshold(train, "camera")
    t_pass = choose_pass_threshold(train)
    out.append(
        "## Thresholds chosen on the train scenes\n\n"
        f"- projector moved: smallest threshold with no false alarm on train: {t_proj} px "
        f"(shipped: {rc.PROJ_MOVED_PX} px)\n"
        f"- camera moved: {t_cam} px (shipped: {rc.CAM_MOVED_PX} px)\n"
        f"- surface pass: largest estimated worst edge with no false pass on train: {t_pass} px "
        f"(shipped: {sc.PASS_EDGE_PX} px)\n"
    )
    data["thresholds"] = {"proj": t_proj, "cam": t_cam, "pass": t_pass}
    for name, sel in (("held-out scenes", held), ("train scenes", train)):
        rig_text, rig_data = rig_tables(sel)
        surf_text, surf_data = surface_tables(sel)
        out += [
            f"## Rig check, {name}",
            "",
            rig_text,
            "",
            f"## Surface check, {name}",
            "",
            surf_text,
            "",
        ]
        data[name] = {"rig": rig_data, "surface": surf_data}
    ml_text, ml_data = ml_comparison(train, held)
    out += ["## Learned model against the rule (held-out scenes)", "", ml_text, ""]
    data["ml"] = ml_data
    secs = [r["seconds"] for r in good]
    out.append(
        f"Wall-clock time per check in the simulator (rendering included): median "
        f"{_q(secs, 50):.2f} s, 95th pct {_q(secs, 95):.2f} s."
    )
    if json_out is not None:
        json_out.write_text(json.dumps(data, indent=2, default=float))
    return "\n".join(out)
