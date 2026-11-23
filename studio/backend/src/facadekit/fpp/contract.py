"""Spike S-1 recorder: check a real FPP against what FacadeKit's client expects.

``facadekit spike fpp-contract HOST`` runs :func:`run_contract`. By default it only reads
(GET requests). Each reply is saved, with personal and network details removed, as a JSON
fixture, and compared with the shape ``fpp/client.py`` relies on (docs/05-interfaces.md
section 7, docs/17-fpp-integration-audit.md). The optional write test uploads a tiny test
clip and a throwaway playlist, reads them back and deletes both; it never starts playback
and needs the user to type ``yes`` first.
"""

from __future__ import annotations

import json
import re
import secrets
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from facadekit.errors import FkError
from facadekit.fpp.client import FppClient, FppError
from facadekit.fpp.publish import MIN_FPP_MAJOR, media_entry, sequences_dir, video_dir

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
TEST_PREFIX = "fk_s1_contract_test_"
TEST_PLAYLIST_PREFIX = "fk_s1_contract_test_"

# What each verdict means for FacadeKit, printed under the table.
MEANINGS = {
    PASS: "matches what FacadeKit expects",
    WARN: "works, but differs from the recorded contract: look at the details",
    FAIL: "FacadeKit cannot publish to this player until this is fixed",
    SKIP: "not run (an earlier step failed, or the write test was not asked for)",
}

# Shapes fpp/client.py relies on: key -> accepted JSON types. "?" suffix = optional.
_NUM = (int, float, str)
EXPECTED: dict[str, dict[str, tuple[type, ...]]] = {
    "version": {
        "version": (str,),
        "majorVersion": (str, int),
        "minorVersion": (str, int),
        "branch?": (str,),
        "fppdAPI?": (str, int),
    },
    "system_info": {"Platform?": (str,), "Variant?": (str,), "HostName?": (str,), "Mode?": (str,)},
    "files": {"status": (str,), "files": (list,)},
    "file_entry": {"name": (str,), "sizeBytes?": _NUM},
    "status": {
        "status_name": (str,),
        "current_playlist?": (dict,),
        "seconds_played?": _NUM,
        "mode_name?": (str,),
    },
    "playlist": {"name?": (str,), "mainPlaylist": (list,), "playlistInfo?": (dict,)},
}
# Paths FacadeKit calls; looked up in FPP's own OpenAPI document.
OPENAPI_PATHS = (
    "/fppd/version",
    "/fppd/status",
    "/system/info",
    "/files/{DirName}",
    "/file/{DirName}",
    "/media/{MediaName}/duration",
    "/playlist/{PlaylistName}",
    "/playlist/{PlaylistName}/start",
    "/playlists",
    "/playlists/stop",
)


@dataclass
class Step:
    name: str
    verdict: str
    detail: str
    differences: list[str] = field(default_factory=list)
    fixture: str | None = None


# -- redaction ---------------------------------------------------------------------------

_SECRET_KEYS = re.compile(
    r"(pass(word|wd|phrase)?|secret|token|api_?key|psk|email|ssid|wifi|serial|uuid|"
    r"^mac$|macaddr|mac_?address|hostname|host_name|hostdescription|host_description|"
    r"^ips?$|ipaddress|ip_address|^address$|gateway|dns|netmask|latitude|longitude|"
    r"location|timezone|^name_?server)",
    re.IGNORECASE,
)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")
# Keys whose values are the user's own file or playlist names: kept only as a shape.
_NAME_KEYS = {"name", "mediaName", "sequenceName", "playlist", "imagePath", "media_filename",
              "sequence_filename", "current_song", "current_sequence", "description", "desc",
              "NextPlaylist", "scriptName"}  # fmt: skip


class Redactor:
    """Removes network details, serials and secrets; replaces user file names consistently."""

    def __init__(self, host: str) -> None:
        self.host = host.split("://")[-1].strip("/").split(":")[0]
        self._names: dict[str, str] = {}

    def name(self, value: str) -> str:
        if value == "" or value.startswith(("fk_", "FacadeKit")) or value.startswith("--"):
            return value
        if value not in self._names:
            suffix = Path(value).suffix if "." in value and len(Path(value).suffix) <= 6 else ""
            self._names[value] = f"<name-{len(self._names) + 1}>{suffix}"
        return self._names[value]

    def text(self, value: str) -> str:
        if self.host and self.host in value:
            value = value.replace(self.host, "<host>")
        value = _MAC.sub("<mac>", value)
        value = _IPV4.sub("<ip>", value)
        return _IPV6.sub("<ip6>", value)

    def __call__(self, data: Any, key: str = "") -> Any:
        if isinstance(data, dict):
            out: dict[str, Any] = {}
            for k, v in data.items():
                if _SECRET_KEYS.search(str(k)):
                    out[k] = "<redacted>"
                else:
                    out[k] = self(v, str(k))
            return out
        if isinstance(data, list):
            return [self(item, key) for item in data]
        if isinstance(data, str):
            if key in _NAME_KEYS:
                return self.name(data)
            return self.text(data)
        return data


# -- comparison --------------------------------------------------------------------------


def compare(kind: str, data: Any) -> list[str]:
    """Differences between ``data`` and the shape FacadeKit expects for ``kind``."""
    spec = EXPECTED[kind]
    if not isinstance(data, dict):
        return [f"expected a JSON object, got {type(data).__name__}"]
    out: list[str] = []
    for raw_key, types in spec.items():
        optional = raw_key.endswith("?")
        key = raw_key.rstrip("?")
        if key not in data:
            if not optional:
                out.append(f"missing key {key!r}")
            continue
        if not isinstance(data[key], types) or isinstance(data[key], bool):
            wanted = "/".join(t.__name__ for t in types)
            out.append(f"{key!r} is {type(data[key]).__name__}, expected {wanted}")
    return out


def _has_required_difference(kind: str, diffs: list[str]) -> bool:
    required = {k for k in EXPECTED[kind] if not k.endswith("?")}
    return any(any(f"'{k}'" in d for k in required) for d in diffs) or any(
        d.startswith("expected a JSON object") for d in diffs
    )


# -- the run ------------------------------------------------------------------------------


class _Recorder:
    def __init__(self, client: FppClient, out_dir: Path, redact: Redactor) -> None:
        self.client = client
        self.out_dir = out_dir
        self.redact = redact
        self.steps: list[Step] = []

    def save(self, name: str, data: Any, *, name_list: bool = False) -> str:
        """Write a redacted fixture. ``name_list``: ``data`` is a list of file/playlist names."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{name}.json"
        if name_list and isinstance(data, list):
            clean: Any = [self.redact.name(v) if isinstance(v, str) else v for v in data]
        else:
            clean = self.redact(data)
        path.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", "utf-8")
        return path.name

    def get(self, path: str) -> tuple[int, Any, str]:
        response = self.client.get_raw(path)
        try:
            data = response.json()
        except ValueError:
            data = None
        return response.status_code, data, response.headers.get("content-type", "")

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step


def run_contract(
    host: str,
    out_dir: Path,
    *,
    password: str | None = None,
    write_test: bool = False,
    confirm: Callable[[str], str] | None = None,
    make_clip: Callable[[Path], None] | None = None,
    timeout: float = 10.0,
) -> list[Step]:
    """Run the S-1 checks against ``host``; write fixtures and a report into ``out_dir``."""
    client = FppClient(host, timeout=timeout, password=password)
    rec = _Recorder(client, out_dir, Redactor(host))
    try:
        if _step_reach(rec, host):
            _read_steps(rec)
            if write_test:
                _write_test(rec, confirm, make_clip)
            else:
                rec.add(Step("write test", SKIP, "not asked for (add --write-test)"))
    finally:
        client.close()
    # The files never name the host: messages such as "No route to <address>" are redacted.
    clean = [
        Step(
            s.name,
            s.verdict,
            rec.redact.text(s.detail),
            [rec.redact.text(d) for d in s.differences],
            s.fixture,
        )
        for s in rec.steps
    ]
    report = {"host": "<host>", "steps": [asdict(step) for step in clean], "meanings": MEANINGS}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    (out_dir / "report.txt").write_text(format_table(clean) + "\n", "utf-8")
    return rec.steps


def _step_reach(rec: _Recorder, host: str) -> bool:
    try:
        version = rec.client.version()
    except FppError as exc:
        if exc.reason in ("auth_required", "auth_rejected"):
            rec.add(Step("reachable", PASS, "FPP answered over HTTP"))
            rec.add(Step("password", FAIL, exc.message))
            return False
        if exc.code == "FPP_UNREACHABLE":
            rec.add(Step("reachable", FAIL, f"[{exc.reason}] {exc.message}"))
            return False
        rec.add(Step("reachable", PASS, "something answered over HTTP"))
        rec.add(Step("version", FAIL, f"[{exc.reason}] {exc.message}"))
        return False
    rec.add(Step("reachable", PASS, f"FPP answered at {rec.client.scheme}://<host>"))
    rec.add(
        Step(
            "password",
            PASS,
            "password accepted" if rec.client.has_password else "no password needed",
        )
    )
    _, data, _ = rec.get("/api/fppd/version")
    diffs = compare("version", data)
    fixture = rec.save("fppd_version", data)
    if version.major < MIN_FPP_MAJOR:
        verdict, detail = FAIL, f"FPP {version.version} is older than {MIN_FPP_MAJOR}.x"
    elif version.major > MIN_FPP_MAJOR:
        verdict = WARN
        detail = f"FPP {version.version}: newer major than the audited 10.x, re-run the audit"
    else:
        verdict = WARN if diffs else PASS
        detail = f"FPP {version.version} (branch {version.branch or '?'})"
    rec.add(Step("version", verdict, detail, diffs, fixture))
    return version.major >= MIN_FPP_MAJOR


def _json_step(
    rec: _Recorder,
    name: str,
    path: str,
    kind: str | None,
    fixture: str,
    *,
    essential: bool = True,
    name_list: bool = False,
) -> Any:
    """GET ``path``; a failure is FAIL when FacadeKit needs the endpoint, else WARN."""
    bad = FAIL if essential else WARN
    try:
        status, data, ctype = rec.get(path)
    except FppError as exc:
        rec.add(Step(name, bad, f"[{exc.reason}] {exc.message}"))
        return None
    if status >= 400 or data is None:
        what = f"HTTP {status}" if status >= 400 else f"not JSON ({ctype or 'no type'})"
        rec.add(Step(name, bad, f"GET {path}: {what}"))
        return None
    saved = rec.save(fixture, data, name_list=name_list)
    diffs = compare(kind, data) if kind else []
    verdict = PASS
    if diffs:
        verdict = FAIL if kind and _has_required_difference(kind, diffs) else WARN
    rec.add(Step(name, verdict, f"GET {path}", diffs, saved))
    return data


def _read_steps(rec: _Recorder) -> None:
    info = _json_step(rec, "system info", "/api/system/info", "system_info", "system_info")
    if isinstance(info, dict):
        mode = info.get("Mode")
        if isinstance(mode, str) and mode.lower() not in ("player", "master"):
            step = rec.steps[-1]
            step.verdict = WARN
            step.detail += f"; FPP is in {mode!r} mode, publishing needs player mode"
        else:
            rec.steps[
                -1
            ].detail += f"; {info.get('Platform', '?')} {info.get('Variant', '')}".rstrip()

    for label, key in (("videos folder", video_dir()), ("sequences folder", sequences_dir())):
        data = _json_step(rec, label, f"/api/files/{key}", "files", f"files_{key}")
        if isinstance(data, dict):
            step = rec.steps[-1]
            if "files" not in data:
                step.verdict = FAIL
                step.detail = f"FPP has no folder {key!r}: {data.get('status')}"
                continue
            entries = [e for e in data["files"] if isinstance(e, dict)]
            for entry in entries[:1]:
                step.differences += [f"file entry: {d}" for d in compare("file_entry", entry)]
            if step.differences and step.verdict == PASS:
                step.verdict = WARN
            step.detail += f"; {len(entries)} entries"

    # FacadeKit does not call /api/media; it is recorded for reference only.
    _json_step(rec, "media list", "/api/media", None, "media", essential=False, name_list=True)
    playlists = _json_step(rec, "playlists", "/api/playlists", None, "playlists", name_list=True)
    if playlists is not None and not isinstance(playlists, list):
        rec.steps[-1].verdict = FAIL
        rec.steps[-1].differences.append(f"expected a JSON list, got {type(playlists).__name__}")
    elif isinstance(playlists, list):
        _sample_playlist(rec, [p for p in playlists if isinstance(p, str)])

    status = _json_step(rec, "status", "/api/fppd/status", "status", "fppd_status")
    if isinstance(status, dict):
        current = status.get("current_playlist")
        if isinstance(current, dict) and not isinstance(current.get("playlist"), str):
            rec.steps[-1].differences.append("current_playlist.playlist is not a string")
            if rec.steps[-1].verdict == PASS:
                rec.steps[-1].verdict = WARN
        rec.steps[-1].detail += f"; {status.get('status_name')}, mode {status.get('mode_name')}"

    _api_help(rec)


def _sample_playlist(rec: _Recorder, names: list[str]) -> None:
    """Record one playlist saved by FPP's own UI, preferring one with a media entry."""
    step = rec.steps[-1]
    step.detail += f"; {len(names)} playlists"
    if not names:
        rec.add(Step("playlist entry shape", SKIP, "no playlists on FPP to read"))
        return
    best: tuple[str, dict[str, Any]] | None = None
    for name in names[:20]:
        data = rec.client.get_playlist(name)
        if not isinstance(data, dict):
            continue
        entries = [e for e in data.get("mainPlaylist", []) if isinstance(e, dict)]
        if best is None or any(e.get("type") in ("media", "both") for e in entries):
            best = (name, data)
            if any(e.get("type") in ("media", "both") for e in entries):
                break
    if best is None:
        rec.add(Step("playlist entry shape", WARN, "no playlist could be read"))
        return
    data = best[1]
    saved = rec.save("playlist_sample", data)
    diffs = compare("playlist", data)
    ours = media_entry("fk_example.mp4")
    found = [e for e in data.get("mainPlaylist", []) if isinstance(e, dict)]
    media = [e for e in found if e.get("type") in ("media", "both")]
    detail = f"entry types: {sorted({str(e.get('type')) for e in found})}"
    if media:
        theirs = media[0]
        missing = sorted(k for k in ours if k not in theirs)
        extra = sorted(k for k in theirs if k not in ours and k != "sequenceName")
        if missing:
            diffs.append(f"FPP's own media entry lacks keys FacadeKit sends: {missing}")
        detail += f"; FPP's media entry also has {extra}" if extra else ""
    else:
        detail += "; no media entry to compare (make one in FPP's UI and run again)"
    verdict = WARN if diffs or not media else PASS
    rec.add(Step("playlist entry shape", verdict, detail, diffs, saved))


def _api_help(rec: _Recorder) -> None:
    try:
        status, data, _ = rec.get("/api/openapi.json")
    except FppError as exc:
        rec.add(Step("API help", WARN, f"[{exc.reason}] {exc.message}"))
        return
    if status >= 400 or not isinstance(data, dict):
        rec.add(Step("API help", WARN, f"GET /api/openapi.json: HTTP {status}, not readable"))
        return
    paths = data.get("paths", {})
    known = {str(p).replace("/api", "", 1) if str(p).startswith("/api/") else str(p) for p in paths}
    missing = [p for p in OPENAPI_PATHS if p not in known]
    info = data.get("info", {}) if isinstance(data.get("info"), dict) else {}
    rec.save("openapi_summary", {"info": info, "paths": sorted(known)})
    verdict = WARN if missing else PASS
    rec.add(
        Step(
            "API help",
            verdict,
            f"openapi.json lists {len(known)} paths",
            [f"not in FPP's API document: {p}" for p in missing],
            "openapi_summary.json",
        )
    )


def make_test_clip(path: Path) -> None:
    """A 2 s silent test clip for the write test only (not a show render)."""
    from facadekit.media.ffmpeg import run_ffmpeg

    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path),
        ]
    )  # fmt: skip


WRITE_TEST_WARNING = (
    "WRITE TEST: this uploads a 2-second test clip named {clip} to FPP's {folder!r} folder,\n"
    "creates a playlist named {playlist}, reads both back and then deletes both.\n"
    "Nothing is played. Type yes to go ahead: "
)


def _write_test(
    rec: _Recorder,
    confirm: Callable[[str], str] | None,
    make_clip: Callable[[Path], None] | None,
) -> None:
    tag = secrets.token_hex(3)
    clip_name = f"{TEST_PREFIX}{tag}.mp4"
    playlist = f"{TEST_PLAYLIST_PREFIX}{tag}"
    folder = video_dir()
    prompt = WRITE_TEST_WARNING.format(clip=clip_name, folder=folder, playlist=playlist)
    answer = confirm(prompt) if confirm is not None else ""
    if answer.strip().lower() != "yes":
        rec.add(Step("write test", SKIP, "not confirmed (type yes to run it)"))
        return
    client = rec.client
    uploaded = created = False
    with tempfile.TemporaryDirectory(prefix="fk_s1_") as tmp:
        clip = Path(tmp) / clip_name
        try:
            (make_clip or make_test_clip)(clip)
            client.upload(folder, clip, clip_name)
            uploaded = True
            listed = clip_name in client.list_files(folder)
            duration = client.media_duration(clip_name)
            rec.add(
                Step(
                    "write: upload",
                    PASS if listed else FAIL,
                    f"{clip.stat().st_size} bytes to {folder!r}; listed={listed}; "
                    f"duration={duration}",
                )
            )
            entry = media_entry(clip_name)
            client.put_playlist(playlist, [entry])
            created = True
            back = client.get_playlist(playlist)
            saved = rec.save("write_playlist_readback", back)
            diffs: list[str] = []
            got = (back or {}).get("mainPlaylist", [])
            first = got[0] if isinstance(got, list) and got and isinstance(got[0], dict) else {}
            for key, value in entry.items():
                if first.get(key) != value:
                    diffs.append(f"entry {key!r}: sent {value!r}, read back {first.get(key)!r}")
            added = sorted(k for k in (back or {}) if k not in ("name", "mainPlaylist"))
            rec.add(
                Step(
                    "write: playlist",
                    WARN if diffs else PASS,
                    f"saved and read back; FPP added top-level {added}",
                    diffs,
                    saved,
                )
            )
        except FkError as exc:
            rec.add(Step("write test", FAIL, f"{exc.code}: {exc.message}"))
        finally:
            cleanup: list[str] = []
            if created:
                try:
                    client.delete_playlist(playlist)
                    cleanup.append("playlist deleted")
                except FkError as exc:
                    cleanup.append(f"playlist NOT deleted ({exc.message}); delete {playlist}")
            if uploaded:
                try:
                    client.delete_file(folder, clip_name)
                    cleanup.append("clip deleted")
                except FkError as exc:
                    cleanup.append(f"clip NOT deleted ({exc.message}); delete {clip_name}")
            if cleanup:
                ok = all("NOT" not in c for c in cleanup)
                rec.add(Step("write: clean up", PASS if ok else FAIL, "; ".join(cleanup)))


# -- output -------------------------------------------------------------------------------


def format_table(steps: list[Step]) -> str:
    width = max((len(s.name) for s in steps), default=4)
    lines = [f"{'step'.ljust(width)}  result  detail", f"{'-' * width}  ------  ------"]
    for step in steps:
        lines.append(f"{step.name.ljust(width)}  {step.verdict.ljust(6)}  {step.detail}")
        for diff in step.differences:
            lines.append(f"{' ' * width}          - {diff}")
    lines.append("")
    used = [v for v in (PASS, WARN, FAIL, SKIP) if any(s.verdict == v for s in steps)]
    lines += [f"{v}: {MEANINGS[v]}" for v in used]
    return "\n".join(lines)


def write_bundle(zip_path: Path, out_dir: Path) -> Path:
    if zip_path.suffix != ".zip":
        raise FkError("BAD_REQUEST", "--bundle: the file name must end with .zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(out_dir.glob("*")):
            if path.is_file():
                bundle.write(path, f"fpp-contract/{path.name}")
    return zip_path
