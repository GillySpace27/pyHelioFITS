"""A fake Studio for tests and for trying notebooks without Studio: the same endpoint file
and JSON-RPC as the real listener, over the bundled command list. `commands.invoke` records the
call and answers `{}` unless a handler is given. Destructive commands answer 'declined' unless
`confirm=True`, mirroring Studio's confirmation dialog."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import jsonschema

from .studio import bundled_commands


class FakeStudio:
    def __init__(self, handlers: dict[str, Callable[[dict], dict]] | None = None, confirm: bool = True):
        self.tools = {t["name"]: t for t in bundled_commands()}
        self.handlers = handlers or {}
        self.confirm = confirm
        self.calls: list[tuple[str, dict]] = []
        self.token = secrets.token_hex(16)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps(outer._answer(req)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _answer(self, req: dict) -> dict:
        rid, params = req.get("id"), req.get("params") or {}

        def err(kind, msg):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": msg, "data": {"kind": kind}}}

        if req["method"] == "commands.list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": list(self.tools.values())}}
        if req["method"] != "commands.invoke":
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such method"}}
        name, args = params["id"], params.get("args") or {}
        tool = self.tools.get(name)
        if tool is None:
            return err("unknown-command", f"no command '{name}'")
        try:
            jsonschema.validate(args, tool["inputSchema"])
        except jsonschema.ValidationError as e:
            return err("invalid-arguments", e.message)
        if tool.get("x-effect") == "destructive" and not self.confirm:
            return err("declined", f"'{name}' was declined in the confirmation dialog")
        self.calls.append((name, args))
        result: Any = self.handlers.get(name, lambda a: {})(args)
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def endpoint_file(self, path: Path) -> Path:
        path.write_text(json.dumps({"port": self.port, "token": self.token, "pid": 0}), "utf-8")
        return path

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
