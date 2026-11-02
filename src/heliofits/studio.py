"""Client for a running HelioFITS Studio.

Studio writes an endpoint file (port and per-session token) when its API setting is on:
`~/HFStudio/api.json`, or the file named by `HFSTUDIO_API`. This client reads it and speaks
JSON-RPC 2.0 over HTTP to 127.0.0.1. Every Studio command in `commands.json` becomes a method:
`studio.seekTime(...)` and `studio.seek_time(...)` are the same call. Arguments are checked
against the command's JSON Schema before anything is sent.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from .errors import CommandError, StudioNotRunning

ENDPOINT_ENV = "HFSTUDIO_API"


def endpoint_path() -> Path:
    return Path(os.environ.get(ENDPOINT_ENV) or Path.home() / "HFStudio" / "api.json")


def bundled_commands() -> list[dict]:
    """The command list shipped with this package: a copy of Studio's docs/commands.json plus the
    proposed data verbs (verbs.py) that Studio does not have yet. A running Studio's own list wins;
    this is for offline use, docs and tests."""
    from .verbs import VERBS
    text = resources.files("heliofits").joinpath("data/studio-commands.json").read_text("utf-8")
    have = {t["name"] for t in json.loads(text)["tools"]}
    return json.loads(text)["tools"] + [v for v in VERBS if v["name"] not in have]


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class Studio:
    def __init__(self, endpoint: dict | None = None, timeout: float = 600.0):
        self._endpoint = endpoint
        self.timeout = timeout
        self._tools: dict[str, dict] | None = None

    # connection ----------------------------------------------------------------------------

    def _ep(self) -> dict:
        if self._endpoint is None:
            path = endpoint_path()
            try:
                self._endpoint = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                raise StudioNotRunning(
                    f"No Studio endpoint at {path}. Start HelioFITS Studio and turn on "
                    "Settings > Scripting API (or set HFSTUDIO_API to the endpoint file).") from None
        return self._endpoint

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        ep = self._ep()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{ep['port']}/rpc", data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {ep['token']}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                reply = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self._endpoint = None  # Studio restarted: the token changed; read the file again
                raise StudioNotRunning("Studio refused the token; try again (it restarted?).") from None
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            self._endpoint = None
            raise StudioNotRunning(
                "Studio did not answer at the endpoint file's port. Is it running with the API on?") from None
        if "error" in reply:
            err = reply["error"]
            data = err.get("data") or {}
            raise CommandError(data.get("kind", "failed"), err.get("message", ""))
        return reply["result"]

    # commands ------------------------------------------------------------------------------

    def commands(self, live: bool = True) -> list[dict]:
        """Tool descriptions (name, description, inputSchema, outputSchema, annotations)."""
        if live:
            try:
                return self._rpc("commands.list")["tools"]
            except StudioNotRunning:
                pass
        return bundled_commands()

    def _schemas(self) -> dict[str, dict]:
        if self._tools is None:
            self._tools = {t["name"]: t for t in self.commands()}
        return self._tools

    def invoke(self, name: str, **args: Any) -> dict:
        """Run command `name` (camelCase or snake_case). Returns its result object."""
        tools = self._schemas()
        if name not in tools:
            by_snake = {snake(n): n for n in tools}
            if name not in by_snake:
                raise CommandError("unknown-command", f"no command '{name}'")
            name = by_snake[name]
        try:
            jsonschema.validate(args, tools[name]["inputSchema"])
        except jsonschema.ValidationError as e:
            raise CommandError("invalid-arguments", e.message) from None
        return self._rpc("commands.invoke", {"id": name, "args": args})

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **args: self.invoke(name, **args)

    def __dir__(self):
        return sorted(set(super().__dir__()) | {snake(n) for n in self._schemas()})


def __getattr__(name: str):
    """Module-level shortcut: `studio.seek_time(...)` uses the default Studio."""
    if name.startswith("_"):
        raise AttributeError(name)
    return getattr(default(), name)


_default: Studio | None = None


def default() -> Studio:
    global _default
    if _default is None:
        _default = Studio()
    return _default
