"""Client for the FacadeKit FPP plugin (integrations/fpp-plugin, docs/20-fpp-plugin.md).

The plugin adds routes under ``/api/plugin/fpp-plugin-FacadeKit/`` to FPP's own API. They
are optional: everything FacadeKit does works through the plain FPP API, and every function
here reports "absent" instead of failing when the plugin is not installed.

``version`` needs no token; every other route needs the pairing token created on the
plugin's page in FPP, sent as ``X-FacadeKit-Token`` (FPP's optional UI password already
uses the Authorization header). Tokens are kept per FPP host in
``<data dir>/secrets/fpp_plugin_tokens.json`` (mode 0600), or come from
``FACADEKIT_FPP_PLUGIN_TOKEN``. They are never logged or sent to the browser.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from facadekit.config import data_dir
from facadekit.errors import FPP_BAD_RESPONSE, FPP_UNREACHABLE, FkError
from facadekit.fpp.client import (
    DEFAULT_USER,
    PASSWORD_ENV,
    USER_ENV,
    _parse_host,
    describe_transport_error,
)

log = logging.getLogger(__name__)

PLUGIN_REPO = "fpp-plugin-FacadeKit"
PLUGIN_PREFIX = f"/api/plugin/{PLUGIN_REPO}"
TOKEN_ENV = "FACADEKIT_FPP_PLUGIN_TOKEN"
TOKEN_HEADER = "X-FacadeKit-Token"
TOKEN_FILE = "fpp_plugin_tokens.json"
SUPPORTED_API = 1


class PluginError(FkError):
    """A plugin failure. ``reason``: absent, not_paired, bad_token, unreachable, http_status,
    not_json, not_configured, refused."""

    def __init__(self, message: str, *, reason: str, code: str = FPP_BAD_RESPONSE) -> None:
        super().__init__(code, message)
        self.reason = reason


@dataclass
class PluginInfo:
    version: str
    api: int
    paired: bool
    configured: bool


# -- token store -----------------------------------------------------------------------


def _token_path() -> Path:
    return data_dir() / "secrets" / TOKEN_FILE


def _host_key(host: str) -> str:
    _scheme, cleaned = _parse_host(host)
    return cleaned.lower()


def _read_tokens() -> dict[str, str]:
    path = _token_path()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def stored_token(host: str) -> str | None:
    """The token for ``host``: the environment variable first, then the token file."""
    env = os.environ.get(TOKEN_ENV)
    if env:
        return env.strip()
    return _read_tokens().get(_host_key(host))


def store_token(host: str, token: str | None) -> None:
    """Remember (or with ``None`` forget) the token for ``host``. The file is mode 0600."""
    tokens = _read_tokens()
    key = _host_key(host)
    if token:
        tokens[key] = token
    else:
        tokens.pop(key, None)
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(tokens, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def valid_token_shape(token: str) -> bool:
    return 8 <= len(token) <= 256 and token.isascii() and token.isprintable() and " " not in token


# -- client ----------------------------------------------------------------------------


class FppPluginClient:
    def __init__(
        self,
        host: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        password: str | None = None,
    ) -> None:
        scheme, cleaned = _parse_host(host)
        self.host = cleaned
        self.token = token if token is not None else stored_token(host)
        if password is None:
            password = os.environ.get(PASSWORD_ENV) or None
        user = os.environ.get(USER_ENV) or DEFAULT_USER
        auth = httpx.BasicAuth(user, password) if password is not None else None
        self._client = httpx.Client(base_url=f"{scheme}://{cleaned}", timeout=timeout, auth=auth)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FppPluginClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request(
        self, method: str, route: str, *, auth: bool = True, timeout: float | None = None, **kw: Any
    ) -> Any:
        headers: dict[str, str] = {}
        if auth:
            if not self.token:
                raise PluginError(
                    "FacadeKit is not paired with the plugin on this FPP. Create a pairing token "
                    "on the plugin's page in FPP and paste it here.",
                    reason="not_paired",
                )
            headers[TOKEN_HEADER] = self.token
        path = f"{PLUGIN_PREFIX}/{route}"
        if timeout is not None:
            kw["timeout"] = timeout
        try:
            response = self._client.request(method, path, headers=headers, **kw)
        except httpx.TransportError as exc:
            reason, message = describe_transport_error(self.host, exc)
            log.warning("FPP plugin %s %s on %s failed: %s", method, route, self.host, reason)
            raise PluginError(message, reason="unreachable", code=FPP_UNREACHABLE) from exc
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code == 404 and not (isinstance(data, dict) and "error" in data):
            # FPP answers an unknown plugin route with its own 404 page.
            raise PluginError(
                "The FacadeKit plugin is not installed on this FPP (or is older than this route).",
                reason="absent",
            )
        error = data.get("error") if isinstance(data, dict) else None
        if response.status_code == 401 and isinstance(error, dict):
            raise PluginError(
                "The plugin did not accept FacadeKit's pairing token. Create a new token on "
                "the plugin's page in FPP and pair again.",
                reason="bad_token",
            )
        if response.status_code == 401:
            raise PluginError(
                f"FPP at {self.host} asks for its UI password. Set {PASSWORD_ENV}.",
                reason="fpp_password",
            )
        if response.status_code == 403 and isinstance(error, dict):
            code = str(error.get("code", ""))
            raise PluginError(
                str(error.get("message", "refused")),
                reason="not_paired" if code == "not_paired" else "refused",
            )
        if response.status_code >= 400:
            message = (
                str(error.get("message"))
                if isinstance(error, dict)
                else f"HTTP {response.status_code}"
            )
            code = str(error.get("code")) if isinstance(error, dict) else "http_status"
            raise PluginError(f"Plugin: {message}", reason=code)
        if data is None:
            raise PluginError(
                "The plugin answered, but not with JSON (is it really the FacadeKit plugin?).",
                reason="not_json",
            )
        return data

    # -- routes -------------------------------------------------------------------------

    def info(self) -> PluginInfo:
        data = self._request("GET", "version", auth=False, timeout=5.0)
        if not isinstance(data, dict) or data.get("plugin") != PLUGIN_REPO:
            raise PluginError("Something answered on the plugin's route, but not the plugin.",
                              reason="not_json")  # fmt: skip
        api = data.get("api")
        return PluginInfo(
            version=str(data.get("version", "")),
            api=api if isinstance(api, int) else 0,
            paired=bool(data.get("paired")),
            configured=bool(data.get("configured")),
        )

    def status(self, *, live: bool = True) -> dict[str, Any]:
        route = "status" if live else "status?cached=1"
        data = self._request("GET", route, timeout=40.0)
        return data if isinstance(data, dict) else {}

    def health(self) -> dict[str, Any]:
        data = self._request("GET", "health", timeout=10.0)
        return data if isinstance(data, dict) else {}

    def power(
        self,
        action: str,
        *,
        wait: bool = False,
        timeout_s: int | None = None,
        projectors: str = "all",
    ) -> dict[str, Any]:
        if action not in ("on", "off"):
            raise FkError("BAD_REQUEST", "action must be on or off")
        body: dict[str, Any] = {"wait": wait, "projectors": projectors}
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        http_timeout = 30.0 + (timeout_s or 0) + (120.0 if wait else 0.0)
        data = self._request("POST", f"projectors/{action}", json=body, timeout=http_timeout)
        return data if isinstance(data, dict) else {}

    def shows(self) -> list[dict[str, Any]]:
        data = self._request("GET", "shows")
        shows = data.get("shows") if isinstance(data, dict) else None
        return [s for s in shows if isinstance(s, dict)] if isinstance(shows, list) else []

    def register_show(self, manifest: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "shows", json=manifest)
        return data if isinstance(data, dict) else {}

    def delete_show(self, playlist: str) -> None:
        self._request("DELETE", f"shows/{quote(playlist, safe='')}")


def plugin_summary(host: str, *, token: str | None = None) -> dict[str, Any]:
    """Everything the FPP page's plugin card needs. Never raises.

    ``present`` is false when the plugin is not installed; ``paired`` says whether a token
    is stored for this host and accepted; ``health`` is the plugin's /health answer.
    """
    out: dict[str, Any] = {
        "present": False,
        "version": None,
        "api": None,
        "paired": False,
        "token_stored": False,
        "configured": False,
        "health": None,
        "problem": None,
        "reason": None,
    }
    try:
        client = FppPluginClient(host, token=token, timeout=5.0)
    except FkError as exc:
        out.update(problem=exc.message, reason="bad_host")
        return out
    with client:
        out["token_stored"] = bool(client.token)
        try:
            info = client.info()
        except PluginError as exc:
            out.update(problem=exc.message, reason=exc.reason)
            return out
        out.update(present=True, version=info.version, api=info.api, configured=info.configured)
        if info.api > SUPPORTED_API:
            out["problem"] = (
                f"The plugin speaks API {info.api}; this FacadeKit knows {SUPPORTED_API}. "
                "Update FacadeKit."
            )
        if not client.token:
            out["reason"] = "not_paired"
            return out
        try:
            out["health"] = client.health()
            out["paired"] = True
        except PluginError as exc:
            out.update(problem=exc.message, reason=exc.reason)
    return out
