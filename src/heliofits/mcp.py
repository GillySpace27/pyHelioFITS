"""`heliofits-mcp`: one MCP server (stdio) for Studio and the cache.

Tools are `studio_<command>` (from Studio's command list: a running Studio's own, else the
bundled copy) and `cache_<method>` (HelioFITS Core's API, with dots as underscores, so
`library.stats` is `cache_library_stats`). Names use underscores because some MCP clients reject dots.
Studio asks for confirmation on destructive commands in its own window; this server adds nothing on top.

    heliofits-mcp [--read-only] [--include-ui]

--read-only hides every tool that changes anything. --include-ui adds Studio's menu and dialog
commands (hidden by default: they open windows and take no arguments).
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from heliofits_core import rpc

from . import cache
from .errors import CommandError, StudioNotRunning
from .studio import Studio, bundled_commands

CACHE_READ = frozenset({
    "core.hello", "library.stats", "library.datasets", "library.files", "library.checks", "library.verify",
    "library.versions", "library.collisions", "library.rejected", "dataset.get", "dataset.resolve",
    "dataset.query", "dataset.cadences", "library.coverage", "download.list", "download.get",
    "archive.search", "link.parse", "view.members", "view.list", "record.read", "record.resolve",
    "settings.get"})
CACHE_DESTRUCTIVE = frozenset({"library.reject", "library.unreject", "library.refetch", "download.cancel",
                               "settings.set"})


def _cache_schema(params: str) -> dict:
    props: dict[str, Any] = {}
    required = []
    for part in filter(None, (p.strip() for p in params.split(","))):
        name, _, default = part.partition("=")
        props[name.strip()] = {}
        if not default:
            required.append(name.strip())
    schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def cache_tools() -> dict[str, tuple[str, types.Tool]]:
    out = {}
    for method, (params, what) in rpc.API.items():
        if method in rpc.ALIASES:
            continue
        name = "cache_" + method.replace(".", "_")
        destructive = method in CACHE_DESTRUCTIVE or "dry_run" in params
        out[name] = (method, types.Tool(
            name=name, description=what + (" Parameters: " + params if params else ""),
            input_schema=_cache_schema(params),
            annotations=types.ToolAnnotations(
                read_only_hint=method in CACHE_READ, destructive_hint=destructive and method not in CACHE_READ)))
    return out


def studio_tools(studio: Studio, include_ui: bool) -> dict[str, tuple[str, types.Tool]]:
    try:
        tools = studio.commands()
    except Exception:
        tools = bundled_commands()
    out = {}
    for t in tools:
        if t.get("x-effect") == "ui" and not include_ui:
            continue
        a = t.get("annotations", {})
        name = "studio_" + t["name"]
        out[name] = (t["name"], types.Tool(
            name=name, description=t.get("description", ""), input_schema=t["inputSchema"],
            annotations=types.ToolAnnotations(read_only_hint=a.get("readOnlyHint"),
                                              destructive_hint=a.get("destructiveHint"))))
    return out


def build(studio: Studio | None = None, read_only: bool = False, include_ui: bool = False) -> Server:
    studio = studio or Studio()
    table = {**studio_tools(studio, include_ui), **cache_tools()}
    if read_only:
        table = {k: v for k, v in table.items() if v[1].annotations and v[1].annotations.read_only_hint}

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[t for _, t in table.values()])

    async def call_tool(ctx, params: types.CallToolRequestParams):
        entry = table.get(params.name)
        if entry is None:
            return _fail(f"unknown tool {params.name}")
        target, _ = entry
        args = params.arguments or {}
        try:
            if params.name.startswith("studio_"):
                result = await anyio.to_thread.run_sync(lambda: studio.invoke(target, **args))
            else:
                result = await anyio.to_thread.run_sync(lambda: cache.call(target, **args))
        except (CommandError, StudioNotRunning) as e:
            return _fail(str(e))
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, default=str))],
                                    structured_content=result if isinstance(result, dict) else {"result": result})

    return Server("heliofits", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool)


def _fail(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="heliofits-mcp", description=__doc__.split("\n\n")[0])
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--include-ui", action="store_true")
    args = ap.parse_args(argv)
    server = build(read_only=args.read_only, include_ui=args.include_ui)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
