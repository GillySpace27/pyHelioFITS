"""JSON-RPC 2.0 over newline-delimited JSON on stdin/stdout.

This is how the Mac app (and anything else) asks the core to act. One request
per line in, one response per line out; progress arrives as notifications
(a "method" and no "id"). The core answers requests in order, except the slow
ones in BACKGROUND (an archive search, rendering a thumbnail), which run on a
small pool and answer when done, so replies can arrive out of order: match
them by id. Downloads run on a worker thread (see jobs.py), so
download.enqueue returns at once and the job reports through
download.progress and download.finished. Every write, from any thread, goes
through the Library's one writer connection.

Methods are listed in Server.methods; each takes named params.

`heliad serve` also lets other cores share the library through this one (see
share.py): their requests arrive on their own connections and are answered
there, and every notification goes to every client.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TextIO

from . import __version__, config, coverage, db, fitsmeta, jobs, views
from .library import Library
from .query import Query

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603,
)

# Methods slow enough that answering them in order would stall the app; none of
# them writes to the index.
BACKGROUND = frozenset({"search", "library.thumbnail"})


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class Server:
    def __init__(self, lib: Library, out: TextIO = sys.stdout):
        self.lib = lib
        self.out = out
        self._out_lock = threading.Lock()
        self._out_ok = True
        self._hub = None
        self.jobs = None if lib.readonly else jobs.JobRunner(lib, notify=self.notify)
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="heliad-rpc")
        self.methods: dict[str, Callable[..., Any]] = {
            "heliad.hello": self.hello,
            "library.stats": lambda: lib.stats(),
            "library.datasets": lambda: lib.datasets(),
            "library.files": lambda dataset_key=None, start=None, end=None, limit=500, offset=0:
                lib.current_files(dataset_key=dataset_key, start=start, end=end, limit=limit, offset=offset),
            "library.checks": lambda file_id: lib.checks_for(file_id),
            "library.verify": lambda: lib.verify(),
            "library.versions": lambda observation_id: lib.versions(observation_id),
            "library.collisions": lambda: lib.collisions(),
            "library.delete": self.delete,
            "library.reindex": lambda dry_run=False: lib.reindex(dry_run=dry_run),
            "download.enqueue": self.enqueue,
            "download.list": lambda status=None, limit=50: jobs.list_jobs(lib, status=status, limit=limit),
            "download.get": lambda job_id: next(iter(jobs.list_jobs(lib, job_id=job_id)), None),
            "download.cancel": lambda job_id: self._jobs().cancel(job_id),
            "download.retry": lambda job_id: self._jobs().retry(job_id),
            "library.thumbnail": lambda file_id, size=256: {"path": str(lib.thumbnail(file_id, size))},
            "query.from_url": self.query_from_url,
            "settings.get": lambda: config.load_settings(),
            "settings.set": lambda key, value=None: config.save_setting(key, value),
            "ingest.path": self.ingest_path,
            "ingest.tree": self.ingest_tree,
            "view.create": lambda name, **kw: views.create(lib, name, **kw),
            "view.materialize": lambda name: views.materialize(lib, name),
            "view.rebuild_all": lambda: views.rebuild_all(lib),
            "view.members": lambda name: views.members(lib, name),
            "view.list": lambda: views.list_views(lib),
            "library.coverage": lambda **kw: coverage.library(lib, **kw),
            "search": self.search,
        }

    # ---- io ----------------------------------------------------------------

    def send(self, message: dict) -> None:
        line = encode(message)
        with self._out_lock:
            if not self._out_ok:
                return
            try:
                self.out.write(line + "\n")
                self.out.flush()
            except (OSError, ValueError):  # our client has gone; relays may still be listening
                self._out_ok = False

    def notify(self, method: str, params: dict) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        self.send(message)
        if self._hub is not None:
            self._hub.broadcast(message)

    def handle_line(self, line: str, reply: Callable[[dict], None] | None = None,
                    pending: list | None = None) -> dict | None:
        """The reply to one request line, or None. A BACKGROUND method answers later
        through `reply` (default: send), and its future is added to `pending`."""
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
        if request["method"] in BACKGROUND:
            future = self._pool.submit(self._answer_later, rid, method, params, reply or self.send)
            if pending is not None:
                pending.append(future)
            return None
        return self._call(rid, method, params)

    def _answer_later(self, rid, method, params, reply) -> None:
        answer = self._call(rid, method, params)
        if answer is not None:
            reply(answer)

    def _call(self, rid, method, params) -> dict | None:
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

    def serve(self, inp: TextIO = sys.stdin, shared: bool = False) -> None:
        """Answer inp until it ends. With shared, other cores may relay to this one
        meanwhile (share.py), and it keeps serving them until the last one leaves."""
        if shared and not self.lib.readonly:
            from .share import Hub
            self._hub = Hub(self, self.lib.root)
            self._hub.start()
        if self.jobs is not None:
            self.jobs.start()
        try:
            for line in inp:
                line = line.strip()
                if not line:
                    continue
                reply = self.handle_line(line)
                if reply is not None:
                    self.send(reply)
        finally:
            if self._hub is not None:
                self._hub.wait_until_idle()
                self._hub.close()
            self._pool.shutdown(wait=True)  # answer what was asked before input ended
            if self.jobs is not None:
                self.jobs.stop(timeout=5)

    # ---- methods -----------------------------------------------------------

    def hello(self) -> dict:
        return {
            "name": "heliad",
            "version": __version__,
            "schema_version": db.SCHEMA_VERSION,
            "reader_compat": db.READER_COMPAT,
            "rules_version": fitsmeta.RULES_VERSION,
            "reindex_due": self.lib.stats()["reindex_due"],
            "readonly": self.lib.readonly,
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

    def search(self, query: dict, source: str = "fido", bins: int = coverage.DEFAULT_BINS) -> dict:
        """An archive search. Where a source reports its records' times, they come back
        binned over the query's range as "coverage", beside the table they belong to."""
        from . import sources  # sunpy and drms are optional; imported only when searching
        q = Query.from_json(query)
        result = sources.get(source).search(q, settings=config.load_settings())
        for part in [result, *result.get("providers", [])]:
            times = part.pop("times", None)
            if times is not None:
                part["coverage"] = coverage.times(times, q.start, q.end, bins)
        return result

    def query_from_url(self, url: str) -> dict:
        try:
            action, q = Query.from_url(url)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc))
        return {"action": action, "query": json.loads(q.to_json())}

    def _jobs(self) -> jobs.JobRunner:
        if self.jobs is None:
            raise RpcError(INVALID_REQUEST, "this core opened the library read-only")
        return self.jobs

    def enqueue(self, query: dict, source: str = "fido") -> dict:
        return self._jobs().enqueue(Query.from_json(query), source)

    def delete(self, file_ids=(), observation_ids=(), dataset_keys=(), dry_run: bool = True) -> dict:
        """Dry run unless dry_run is false, so a client shows what would go before it goes."""
        return self.lib.delete(file_ids=file_ids, observation_ids=observation_ids,
                               dataset_keys=dataset_keys, dry_run=dry_run)


def encode(message: dict) -> str:
    return json.dumps(message, separators=(",", ":"), default=str)


def _error(rid, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}
