"""JSON-RPC 2.0 over newline-delimited JSON on stdin/stdout.

This is how the Mac app (and anything else) asks the core to act. One request
per line in, one response per line out; progress arrives as notifications
(a "method" and no "id"). The core runs requests one at a time, so the index
always has exactly one writer.

Methods are listed in METHODS; each takes named params.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from typing import Any, Callable, TextIO

from . import __version__, db, views
from .library import Library
from .query import Query

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603,
)


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class Server:
    def __init__(self, lib: Library, out: TextIO = sys.stdout):
        self.lib = lib
        self.out = out
        self._out_lock = threading.Lock()
        self.methods: dict[str, Callable[..., Any]] = {
            "heliad.hello": self.hello,
            "library.stats": lambda: lib.stats(),
            "library.datasets": lambda: lib.datasets(),
            "library.files": lambda dataset_key=None, start=None, end=None, limit=500:
                lib.current_files(dataset_key=dataset_key, start=start, end=end, limit=limit),
            "library.checks": lambda file_id: lib.checks_for(file_id),
            "library.verify": lambda: lib.verify(),
            "ingest.path": self.ingest_path,
            "ingest.tree": self.ingest_tree,
            "view.create": lambda name, **kw: views.create(lib, name, **kw),
            "view.materialize": lambda name: views.materialize(lib, name),
            "view.rebuild_all": lambda: views.rebuild_all(lib),
            "view.members": lambda name: views.members(lib, name),
            "search": self.search,
        }

    # ---- io ----------------------------------------------------------------

    def send(self, message: dict) -> None:
        line = json.dumps(message, separators=(",", ":"), default=str)
        with self._out_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def notify(self, method: str, params: dict) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def handle_line(self, line: str) -> dict | None:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return _error(None, PARSE_ERROR, f"parse error: {exc}")
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or "method" not in request:
            return _error(request.get("id") if isinstance(request, dict) else None,
                          INVALID_REQUEST, "not a JSON-RPC 2.0 request")
        rid = request.get("id")
        method = self.methods.get(request["method"])
        if method is None:
            return _error(rid, METHOD_NOT_FOUND, f"no method {request['method']!r}")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params must be an object of named parameters")
        try:
            result = method(**params)
        except TypeError as exc:
            return _error(rid, INVALID_PARAMS, str(exc))
        except RpcError as exc:
            return _error(rid, exc.code, exc.message, exc.data)
        except Exception as exc:
            return _error(rid, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}",
                          traceback.format_exc(limit=5))
        if rid is None:  # a notification from the client: no reply
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def serve(self, inp: TextIO = sys.stdin) -> None:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            reply = self.handle_line(line)
            if reply is not None:
                self.send(reply)

    # ---- methods -----------------------------------------------------------

    def hello(self) -> dict:
        return {
            "name": "heliad",
            "version": __version__,
            "schema_version": db.SCHEMA_VERSION,
            "library_root": str(self.lib.root),
            "db_path": str(self.lib.cfg.db_path),
            "methods": sorted(self.methods),
        }

    def ingest_path(self, path: str, source_url: str | None = None, query: dict | None = None) -> dict:
        q = Query.from_json(query) if query else None
        return self.lib.ingest(path, source_url=source_url, query=q).to_dict()

    def ingest_tree(self, path: str) -> dict:
        def progress(i, n, p):
            if i == n or i % 25 == 0:
                self.notify("ingest.progress", {"done": i, "total": n, "path": str(p)})
        return self.lib.import_tree(path, progress=progress)

    def search(self, query: dict) -> dict:
        from .sources import fido  # sunpy is optional; imported only when searching
        return fido.search(Query.from_json(query))


def _error(rid, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}
