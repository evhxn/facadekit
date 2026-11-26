"""Checks of the FPP plugin (integrations/fpp-plugin) that need no PHP and no FPP.

The PHP itself is linted and run in test_fpp_plugin_php.py when ``php`` is installed. Here:
pluginInfo.json against the FPP template's schema rules, the FPP Command descriptions, the
shell scripts (syntax, executable bit, arguments passed without the shell evaluating them),
and a static pass over the PHP (balanced brackets outside strings and comments, every
``fk_`` function and PJLink method that is called is defined, no shell-executing functions).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[3] / "integrations" / "fpp-plugin"
PHP_FILES = sorted(p for p in PLUGIN.rglob("*") if p.suffix in (".php", ".inc"))
SH_FILES = sorted(PLUGIN.rglob("*.sh"))


def _json(name: str) -> object:
    return json.loads((PLUGIN / name).read_text("utf-8"))


# -- pluginInfo.json -----------------------------------------------------------------------

TOP_KEYS = {"repoName", "name", "author", "description", "homeURL", "srcURL", "bugURL",
            "iconURL", "allowUpdates", "releaseNotesStyle", "minMemoryMB", "minCpuCores",
            "private", "linkName", "delist", "submissionToken", "versions", "dependencies",
            "documentation", "version", "requires", "privacy"}  # fmt: skip
PRIVACY_KEYS = {"summary", "sends", "collects", "sensors", "remoteAccess", "systemChanges",
                "closedCode", "other"}  # fmt: skip


def test_plugin_info_follows_the_fpp_schema() -> None:
    info = _json("pluginInfo.json")
    assert isinstance(info, dict)
    assert set(info) <= TOP_KEYS
    for key in ("repoName", "name", "author", "description", "homeURL", "srcURL", "bugURL"):
        assert isinstance(info[key], str) and info[key]
    assert re.fullmatch(r"[A-Za-z0-9._-]+", info["repoName"])
    assert info["repoName"] == "fpp-plugin-FacadeKit"
    assert info["srcURL"].startswith("https://github.com/")
    assert info["srcURL"].endswith(f"/{info['repoName']}.git")
    for entry in info["versions"]:
        assert set(entry) <= {"minFPPVersion", "maxFPPVersion", "branch", "sha", "allowUpdates",
                              "platforms", "dependencies"}  # fmt: skip
        assert re.fullmatch(r"[0-9]+(\.[0-9]+)*", entry["minFPPVersion"])
        assert entry["maxFPPVersion"] == "0" and entry["sha"] == ""
    assert info["versions"][-1]["minFPPVersion"].startswith("10.")
    deps = info["dependencies"]
    assert all(deps[k] == [] for k in ("packages", "python", "scripts", "plugins"))


def test_privacy_block_is_complete() -> None:
    privacy = _json("pluginInfo.json")["privacy"]  # type: ignore[index]
    assert set(privacy) == PRIVACY_KEYS
    assert privacy["remoteAccess"] in ("none", "lan")
    assert privacy["closedCode"] is False
    for send in privacy["sends"]:
        assert set(send) == {"to", "what", "why", "alwaysOn"}
        assert send["alwaysOn"] is False
        assert send["why"][0].islower()
    assert privacy["collects"] == [] and privacy["sensors"] == []


def test_api_docs_match_the_registered_routes() -> None:
    docs = _json("apiDocs.json")
    assert isinstance(docs, dict)
    documented = {(m.upper(), path) for path, ops in docs["paths"].items() for m in ops}
    api = (PLUGIN / "api.php").read_text("utf-8")
    routes = re.findall(r"array\('(GET|POST|DELETE)', '([^']+)', 'fppplugin", api)
    registered = {
        (m, "/api/plugin/fpp-plugin-FacadeKit/" + re.sub(r":(\w+)", r"{\1}", e)) for m, e in routes
    }
    assert registered == documented
    for callback in re.findall(r"'(fpppluginFacadeKit\w+)'\)", api):
        assert f"function {callback}(" in api


# -- FPP Command descriptions --------------------------------------------------------------

ARG_TYPES = {"string", "int", "bool", "datalist"}


def test_command_descriptions() -> None:
    commands = _json("commands/descriptions.json")
    assert isinstance(commands, list)
    names = [c["name"] for c in commands]
    assert names == [
        "FacadeKit Projectors On",
        "FacadeKit Projectors Off",
        "FacadeKit Select Input",
        "FacadeKit Status Snapshot",
    ]
    for cmd in commands:
        assert set(cmd) <= {"name", "description", "script", "args"}
        script = PLUGIN / "commands" / cmd["script"]
        assert script.is_file()
        for arg in cmd["args"]:
            assert set(arg) <= {"name", "description", "type", "optional", "default", "min", "max",
                                "contents", "contentListUrl", "allowBlanks"}  # fmt: skip
            assert arg["type"] in ARG_TYPES
            assert re.fullmatch(r"[a-z][a-zA-Z]*", arg["name"])
            if "default" in arg:
                assert isinstance(arg["default"], str)  # fppd passes strings
            if arg["type"] == "int":
                assert arg["min"] < arg["max"]
                assert arg["min"] <= int(arg["default"]) <= arg["max"]
            if arg["type"] == "bool":
                assert arg["default"] in ("true", "false")
            if arg["type"] == "datalist":
                assert "contents" in arg or "contentListUrl" in arg
    # Every input code offered is valid PJLink (first two characters).
    inputs = next(a for a in commands[2]["args"] if a["name"] == "input")["contents"]
    assert all(re.match(r"[1-5][1-9] ", item) for item in inputs)


# -- shell scripts -------------------------------------------------------------------------


@pytest.mark.parametrize("path", SH_FILES, ids=lambda p: p.name)
def test_shell_scripts(path: Path) -> None:
    text = path.read_text("utf-8")
    assert text.startswith("#!/bin/bash\n")
    assert path.stat().st_mode & stat.S_IXUSR, "fppd runs commands directly: needs +x"
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0
    assert "sudo" not in text
    assert "eval " not in text


def test_callbacks_list() -> None:
    out = subprocess.run(["bash", str(PLUGIN / "callbacks.sh"), "--list"], capture_output=True,
                         text=True, check=True)  # fmt: skip
    assert out.stdout.strip() == "playlist"


NASTY = ["all; touch /tmp/x", "$(touch /tmp/y)", "`id`", "a b", "'\"", "--flag", ""]


def _stub_php(tmp_path: Path) -> tuple[Path, Path]:
    record = tmp_path / "argv.json"
    stub = tmp_path / "php"
    stub.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        f"open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n",
        "utf-8",
    )
    stub.chmod(0o755)
    return stub, record


@pytest.mark.parametrize("command", ["on", "off", "input", "status"])
def test_command_scripts_pass_arguments_untouched(tmp_path: Path, command: str) -> None:
    stub, record = _stub_php(tmp_path)
    script = PLUGIN / "commands" / f"{command}.sh"
    env = {"FK_PHP": str(stub), "SCRIPTDIR": str(PLUGIN / "commands") + "/"}
    subprocess.run([str(script), *NASTY], env=env, check=True, cwd=tmp_path)
    argv = json.loads(record.read_text("utf-8"))
    assert Path(argv[0]).resolve() == (PLUGIN / "lib" / "cli.php").resolve()
    assert argv[1:] == [command, *NASTY]


def test_callbacks_pass_event_json_untouched(tmp_path: Path) -> None:
    stub, record = _stub_php(tmp_path)
    data = json.dumps({"Action": "start", "name": "$(reboot); `id` 'x'"})
    subprocess.run(["bash", str(PLUGIN / "callbacks.sh"), "--type", "playlist", "--data", data],
                   env={"FK_PHP": str(stub)}, check=True)  # fmt: skip
    argv = json.loads(record.read_text("utf-8"))
    assert argv[1:] == ["playlist-event", data]


def test_install_scripts_never_fetch() -> None:
    for name in ("fpp_install.sh", "fpp_uninstall.sh"):
        text = (PLUGIN / "scripts" / name).read_text("utf-8")
        for word in ("curl", "wget", "apt-get", "pip", "cpan", "git ", "systemctl", "reboot"):
            assert word not in text, f"{name} uses {word}"
    uninstall = (PLUGIN / "scripts" / "fpp_uninstall.sh").read_text("utf-8")
    assert 'FPP_PLUGIN_REINSTALL:-0}" != "1"' in uninstall


# -- PHP static checks ---------------------------------------------------------------------


def php_code(text: str) -> str:
    """The PHP parts of a file with strings and comments blanked (newlines kept)."""
    out: list[str] = []
    i, n, in_php = 0, len(text), False
    while i < n:
        if not in_php:
            j = text.find("<?php", i)
            if j < 0:
                break
            out.append("\n" * text.count("\n", i, j))
            i, in_php = j + 5, True
            continue
        c = text[i]
        if text.startswith("?>", i):
            in_php = False
            i += 2
        elif c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            assert j < n, "unterminated string"
            out.append(c + c + "\n" * text.count("\n", i, j))
            i = j + 1
        elif text.startswith("//", i) or c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            assert j >= 0, "unterminated comment"
            out.append("\n" * text.count("\n", i, j))
            i = j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


@pytest.mark.parametrize("path", PHP_FILES, ids=lambda p: str(p.relative_to(PLUGIN)))
def test_php_brackets_balance(path: Path) -> None:
    code = php_code(path.read_text("utf-8"))
    stack: list[tuple[str, int]] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for line_no, line in enumerate(code.split("\n"), 1):
        for ch in line:
            if ch in "([{":
                stack.append((ch, line_no))
            elif ch in pairs:
                assert stack and stack[-1][0] == pairs[ch], f"unbalanced {ch} at line {line_no}"
                stack.pop()
    assert not stack, f"unclosed {stack[-1]}"


def _all_code() -> str:
    return "\n".join(php_code(p.read_text("utf-8")) for p in PHP_FILES)


def test_php_functions_called_are_defined() -> None:
    code = _all_code()
    defined = set(re.findall(r"\bfunction\s+(\w+)\s*\(", code))
    called = set(re.findall(r"(?<![\w>$:])(fk_\w+)\s*\(", code))
    assert called - defined == set()
    # Callables passed by name (array_map('fk_public_projector', ...)).
    for text in (p.read_text("utf-8") for p in PHP_FILES):
        for name in re.findall(r"'(fk_\w+)'", text):
            assert name in defined, name


def test_pjlink_methods_called_exist() -> None:
    code = _all_code()
    pjlink = php_code((PLUGIN / "lib" / "pjlink.php").read_text("utf-8"))
    methods = set(re.findall(r"function\s+(\w+)\s*\(", pjlink))
    for name in set(re.findall(r"\$client->(\w+)\(", code)):
        assert name in methods, name


def test_php_never_runs_a_shell_with_user_values() -> None:
    code = _all_code()
    for banned in ("exec(", "shell_exec(", "system(", "passthru(", "popen(", "eval(", "`",
                   "assert(", "create_function("):  # fmt: skip
        hits = [m.start() for m in re.finditer(re.escape(banned), code)]
        # proc_open is the only way the plugin starts processes, always with an argv array.
        hits = [h for h in hits if not code[max(0, h - 5) : h].endswith("proc_")]
        assert not hits, banned
    power = (PLUGIN / "lib" / "power.php").read_text("utf-8")
    assert power.count("proc_open(") == 2
    detach = re.search(r"const FK_DETACH_SH = '([^']*)';", power)
    assert detach and detach.group(1).startswith('"$0" "$@" </dev/null >/dev/null 2>&1 ')
    assert detach.group(1).endswith(" &") and "$1" not in detach.group(1)
    for call in re.findall(r"proc_open\((\$\w+)", power):
        assert re.search(rf"\{call} = array(_merge)?\(", power), call


def test_php_files_have_no_closing_tag_problems() -> None:
    for path in PHP_FILES:
        text = path.read_text("utf-8")
        if path.name == "setup.php":
            continue
        assert text.startswith("<?php"), path
        assert "?>" not in php_code(text), f"{path}: no closing tag in pure PHP files"


def test_passwords_never_reach_logs_or_output() -> None:
    for path in PHP_FILES:
        text = path.read_text("utf-8")
        for line in text.splitlines():
            if "fk_log(" in line or "echo " in line:
                assert "['password']" not in line or "=== null" in line, f"{path}: {line}"
    ajax = (PLUGIN / "ajax.php").read_text("utf-8")
    assert "fk_public_projector" in ajax  # the page only ever gets has_password


def test_no_owner_addresses_or_secrets_in_plugin() -> None:
    for path in PLUGIN.rglob("*"):
        if path.is_file():
            text = path.read_text("utf-8", errors="replace")
            assert "192.168.1.132" not in text
            assert not re.search(r"fkp_[0-9a-f]{48}", text)


@pytest.mark.skipif(shutil.which("php") is not None, reason="php is installed: real lint runs")
def test_php_lint_not_available_is_reported() -> None:
    # Recorded so the gate output says plainly that PHP was only checked statically here.
    assert shutil.which("php") is None
    assert os.environ.get("FK_REQUIRE_PHP") != "1", "FK_REQUIRE_PHP=1 but php is not installed"


def test_detach_shell_keeps_arguments_literal(tmp_path: Path) -> None:
    """Run the exact FK_DETACH_SH text with /bin/sh: a hostile argument is not evaluated."""
    power = (PLUGIN / "lib" / "power.php").read_text("utf-8")
    match = re.search(r"const FK_DETACH_SH = '([^']*)';", power)
    assert match
    target = tmp_path / "a b;$(touch pwned)`id`"
    subprocess.run(["/bin/sh", "-c", match.group(1), "/usr/bin/touch", str(target)], check=True,
                   cwd=tmp_path, timeout=10)  # fmt: skip
    deadline = time.monotonic() + 5
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert target.exists()
    assert not (tmp_path / "pwned").exists()


def test_stop_event_handling_exists_and_fixtures_parse() -> None:
    code = _all_code()
    for name in ("fk_active_show_get", "fk_active_show_set", "fk_active_show_clear",
                 "fk_system_timezone"):  # fmt: skip
        assert re.search(rf"\bfunction\s+{name}\s*\(", code), name
    events = json.loads(
        (Path(__file__).parent / "fixtures" / "fpp_playlist_events.json").read_text("utf-8")
    )
    assert events["start"]["Action"] == "start" and events["start"]["name"]
    assert events["stop"]["Action"] == "stop" and events["stop"]["name"] == ""
