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

Methods are listed in Server.methods; each takes named params. API below names
each one's params and says what it does; `hfcore api` prints it, and
docs/api.md is the full reference with examples. A test keeps all three in step.

`hfcore serve` also lets other cores share the library through this one (see
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
from urllib.parse import urlsplit

from . import __version__, config, coverage, db, fitsmeta, jobs, layers, upgrade, views
from . import record as hfrecord
from .library import Library, LibraryLocked, ReadOnlyLibrary
from .query import Query, URL_SCHEMES, parse_dataset_link
from .sources import ArchiveError

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603,
)
# HelioFITS Core's own codes. The vocabulary only grows: a code, once given, keeps its meaning.
UNSTORABLE = -32001  # ingest.path: this file can never enter the library; do not offer it again
NOT_FOUND = -32002   # no dataset, observation, file, job, view or source of that name or id
READ_ONLY = -32003   # a write asked of a core that opened the library read-only
ARCHIVE = -32004     # the archive could not be asked, or refused; the message says why
LOCKED = -32005      # another process holds the library and this core cannot act on it
RECORD_UNSUPPORTED = -32010  # record.*: a processing record of a format this core does not read
RECORD_UNREADABLE = -32011   # record.read: the file named is a sidecar that is not a record

# Methods slow enough that answering them in order would stall the app; none of
# them writes to the index.
BACKGROUND = frozenset({"archive.search", "library.thumbnail", "dataset.cadences", "record.read"})

# Old names, kept so a client written against them keeps working; each answers
# exactly as the method it names. A new client uses the new name.
ALIASES: dict[str, str] = {
    "search": "archive.search",
    "query.from_url": "link.parse",
    "dataset.from_url": "link.parse",
    "heliad.hello": "core.hello",  # the name from before the rename
}


# method: (params, what it does). Params are as the method takes them, by name.
API: dict[str, tuple[str, str]] = {
    "core.hello": ("", "Versions, the library root, whether this core is read-only, and every method."),
    "library.stats": ("", "Counts of datasets, observations, files, bytes, warnings and collisions."),
    "library.datasets": ("", "Every dataset with its label, keywords, observation count, time span and bytes."),
    "library.files": ("dataset_key=None, start=None, end=None, limit=500, offset=0, include_rejected=False",
                      "Current files (the newest version of each observation), in time order."),
    "library.checks": ("file_id", "The header checks recorded for one file."),
    "library.verify": ("", "Recompute every checksum; lists missing and altered files, changes nothing."),
    "library.versions": ("observation_id", "Every file of one observation, newest version first, marking the current one."),
    "library.collisions": ("", "Observations holding two different files of the current version."),
    "library.delete": ("file_ids=(), observation_ids=(), dataset_keys=(), dry_run=True",
                       "Remove files from the library; a dry run unless dry_run is false."),
    "library.reindex": ("dry_run=False", "Apply the current header rules to every file already kept."),
    "library.fix_extensions": ("dry_run=True",
                               "Add .fits or .fits.gz to stored files without a FITS suffix; a dry run unless dry_run is false."),
    "library.reject": ("observation_ids, reason", "Set observations aside (a bad frame): readers stop seeing them."),
    "library.unreject": ("observation_ids", "Serve rejected observations to readers again."),
    "library.rejected": ("dataset_key=None", "Rejected observations with their reason and current file."),
    "library.refetch": ("observation_id", "Queue a job asking the archive again for one observation's frame."),
    "dataset.get": ("dataset_uid=None, dataset_key=None",
                    "One dataset by its stable uid or by key (an old key a reindex renamed resolves too), or null."),
    "dataset.resolve": ("key", "{dataset_uid, dataset_key, resolved_by} for a uid, a key or an old key; or null."),
    "dataset.query": ("dataset_key", "The query that asks the archive again for a dataset, stored or inferred."),
    "dataset.cadences": ("dataset_key, start=None, end=None",
                         "Compare a dataset's cadence with the archive's; lists the frames it lacks."),
    "dataset.upgrade": ("dataset_key, sample_s=None, start=None, end=None, dry_run=True",
                        "Queue a job fetching only the frames a dataset lacks; a dry run unless dry_run is false."),
    "layer.upgrade": ("headers, sample_s=None, dry_run=True",
                      "For a layer of pictures (Helioviewer frames), per frame the FITS file the library keeps "
                      "or the fetch that would get it; queues the fetches unless dry_run."),
    "library.thumbnail": ("file_id, size=256", "Path of a greyscale PNG preview, made on first request."),
    "library.coverage": ("start=None, end=None, bins=240, dataset_keys=None, instrument=None, "
                         "wavelength=None, wavelength_max=None",
                         "Per dataset, how many observations fall in each equal bin of a time range."),
    "ingest.path": ("path, source_url=None, query=None", "Add one FITS file to the library (copied, deduplicated by checksum)."),
    "ingest.tree": ("path", "Add every FITS file under a folder; reports ingest.progress."),
    "download.enqueue": ("query, source='fido'", "Queue a download; returns the job at once, then reports download.progress."),
    "download.list": ("status=None, limit=50", "Download jobs, newest first."),
    "download.get": ("job_id", "One download job, or null."),
    "download.cancel": ("job_id", "Cancel a queued or running job."),
    "download.retry": ("job_id", "Queue a partial or failed job again; it fetches only what is missing."),
    "archive.search": ("query, source='fido', bins=240", "Ask an archive what it holds for a query; downloads nothing."),
    "link.parse": ("url", "What an hfcore:// (or older heliad://) link asks for: an action and a query, or a dataset and a range."),
    "view.create": ("name, description=None, dataset_keys=None, start=None, end=None, observation_ids=None",
                    "Create a named view (an event, say) or add observations to one."),
    "view.materialize": ("name", "Build a view's folder of links and its manifest.csv."),
    "view.rebuild_all": ("", "Rebuild every view's folder."),
    "view.members": ("name", "The current files of a view."),
    "view.list": ("", "Every view with its size, span and folder."),
    "record.read": ("path", "The processing record of an exported image or movie, with rows ready to show."),
    "record.resolve": ("record", "Look a record's sources up in the library by SHA-256; groups the missing by dataset."),
    "settings.get": ("", "HelioFITS Core's settings (jsoc_email, vso_mirrors, ...)."),
    "settings.set": ("key, value=None", "Set a setting; a null value removes it."),
    "search": ("query, source='fido', bins=240", "Old name of archive.search; answers the same."),
    "query.from_url": ("url", "Old name of link.parse; answers the same."),
    "dataset.from_url": ("url", "Old name of link.parse; answers the same."),
    "heliad.hello": ("", "Old name of core.hello, from before HelioFITS Core was renamed; answers the same."),
}

# Sent without an id, to every client of the core.
NOTIFICATIONS: dict[str, tuple[str, str]] = {
    "download.progress": ("job_id, phase, done, total", "A job's phase (searching, fetching, ingesting) and its count so far."),
    "download.finished": ("job_id, status, error, result", "A job ended: done, partial, failed or cancelled."),
    "ingest.progress": ("done, total, path", "Files handled so far by ingest.tree, every 25 files and at the end."),
}


def describe(name: str | None = None) -> str:
    """What `hfcore api` prints: every method and notification, or one method."""
    if name is not None:
        table = API if name in API else NOTIFICATIONS if name in NOTIFICATIONS else None
        if table is None:
            raise KeyError(name)
        params, what = table[name]
        kind = "notification" if table is NOTIFICATIONS else "method"
        return f"{name}({params})  [{kind}]\n    {what}\n\nFull reference: docs/api.md, section {name}\n"
    width = max(len(f"{n}({p})") for n, (p, _) in API.items() if len(p) < 40) + 2
    lines = [
        f"HelioFITS Core JSON-RPC API (hfcore {__version__})",
        "",
        "Start a core with `hfcore [--library DIR] serve`. Write one JSON-RPC 2.0 request per line",
        "to its stdin; each reply, and each notification (no id), is one line on its stdout.",
        "Params are named. A second `hfcore serve` on the same library relays to the first.",
        "Example:",
        "  echo '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"library.stats\"}' | hfcore serve",
        "",
        "Methods:",
    ]
    for table in (API, None, NOTIFICATIONS):
        if table is None:
            lines += ["", "Notifications:"]
            continue
        for n, (p, what) in table.items():
            call = f"{n}({p})"
            if len(call) >= width:
                lines += [f"  {call}", f"  {'':<{width}}{what}"]
            else:
                lines.append(f"  {call:<{width}}{what}")
    lines += ["", "One method in detail: hfcore api METHOD. Full reference with examples: docs/api.md", ""]
    return "\n".join(lines)


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
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="hfcore-rpc")
        self.methods: dict[str, Callable[..., Any]] = {
            "core.hello": self.hello,
            "library.stats": lambda: lib.stats(),
            "library.datasets": lambda: lib.datasets(),
            "library.files": lambda dataset_key=None, start=None, end=None, limit=500, offset=0, include_rejected=False:
                lib.current_files(dataset_key=dataset_key, start=start, end=end, limit=limit, offset=offset,
                                  include_rejected=include_rejected),
            "library.reject": lambda observation_ids, reason: lib.reject(observation_ids, reason),
            "library.unreject": lambda observation_ids: lib.unreject(observation_ids),
            "library.rejected": lambda dataset_key=None: lib.rejected(dataset_key),
            "library.refetch": lambda observation_id: upgrade.refetch(lib, self._jobs(), observation_id),
            "dataset.get": self.dataset_get,
            "dataset.resolve": lambda key: lib.resolve_dataset(key),
            "dataset.query": lambda dataset_key: upgrade.dataset_query(lib, dataset_key),
            "dataset.cadences": self.cadences,
            "dataset.upgrade": lambda dataset_key, sample_s=None, start=None, end=None, dry_run=True:
                upgrade.upgrade(lib, self.jobs if dry_run else self._jobs(), dataset_key,
                                sample_s=sample_s, start=start, end=end, dry_run=dry_run),
            "layer.upgrade": lambda headers, sample_s=None, dry_run=True:
                layers.upgrade_layer(lib, self.jobs if dry_run else self._jobs(), headers,
                                     sample_s=sample_s, dry_run=dry_run),
            "library.checks": lambda file_id: lib.checks_for(file_id),
            "library.verify": lambda: lib.verify(),
            "library.versions": lambda observation_id: lib.versions(observation_id),
            "library.collisions": lambda: lib.collisions(),
            "library.delete": self.delete,
            "library.reindex": lambda dry_run=False: lib.reindex(dry_run=dry_run),
            "library.fix_extensions": lambda dry_run=True: lib.fix_extensions(dry_run=dry_run),
            "download.enqueue": self.enqueue,
            "download.list": lambda status=None, limit=50: jobs.list_jobs(lib, status=status, limit=limit),
            "download.get": lambda job_id: next(iter(jobs.list_jobs(lib, job_id=job_id)), None),
            "download.cancel": lambda job_id: self._jobs().cancel(job_id),
            "download.retry": lambda job_id: self._jobs().retry(job_id),
            "library.thumbnail": lambda file_id, size=256: {"path": str(lib.thumbnail(file_id, size))},
            "link.parse": self.link_parse,
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
            "record.read": self.record_read,
            "record.resolve": self.record_resolve,
            "archive.search": self.search,
        }
        for old, new in ALIASES.items():
            self.methods[old] = self.methods[new]

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
        if ALIASES.get(request["method"], request["method"]) in BACKGROUND:
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
        except KeyError as exc:  # the core's "no such dataset / observation / file / job"
            return _error(rid, NOT_FOUND, str(exc.args[0]) if exc.args else "not found")
        except ValueError as exc:  # a param the core could not use: a bad time, a range, a missing reason
            return _error(rid, INVALID_PARAMS, str(exc))
        except ReadOnlyLibrary as exc:
            return _error(rid, READ_ONLY, str(exc))
        except LibraryLocked as exc:
            return _error(rid, LOCKED, str(exc))
        except ArchiveError as exc:
            return _error(rid, ARCHIVE, str(exc))
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
            "name": "heliofits-core",
            "version": __version__,
            "schema_version": db.SCHEMA_VERSION,
            "reader_compat": db.READER_COMPAT,
            "rules_version": fitsmeta.RULES_VERSION,
            "reindex_due": self.lib.stats()["reindex_due"],
            "readonly": self.lib.readonly,
            "library_root": str(self.lib.root),
            "db_path": str(self.lib.cfg.db_path),
            "methods": sorted(self.methods),
            "aliases": dict(ALIASES),
        }

    def ingest_path(self, path: str, source_url: str | None = None, query: dict | None = None) -> dict:
        q = Query.from_json(query) if query else None
        try:
            return self.lib.ingest(path, source_url=source_url, query=q).to_dict()
        except fitsmeta.Unstorable as exc:
            raise RpcError(UNSTORABLE, str(exc))

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

    def cadences(self, dataset_key: str, start: str | None = None, end: str | None = None) -> dict:
        """dataset.cadences: an archive search at full cadence over the dataset's query."""
        from . import sources  # sunpy and drms are optional; imported only when searching
        resolve = self.jobs._source if self.jobs is not None else sources.get
        return upgrade.cadences(self.lib, resolve, dataset_key, start, end, settings=config.load_settings())

    def link_parse(self, url: str) -> dict:
        """link.parse: an hfcore://search or hfcore://get link (or heliad://, the old scheme) gives {"action", "query"};
        an hfcore://dataset/<uid> link gives {"action": "dataset", "dataset_uid", "start",
        "end", "dataset"} (dataset null when this library has no such uid)."""
        try:
            parts = urlsplit(url)
            if parts.scheme in URL_SCHEMES and parts.netloc == "dataset":
                link = parse_dataset_link(url)
                return {"action": "dataset", **link,
                        "dataset": self.lib.dataset(dataset_uid=link["dataset_uid"])}
            action, q = Query.from_url(url)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc))
        return {"action": action, "query": json.loads(q.to_json())}

    def record_read(self, path: str) -> dict:
        """record.read: reads the file and its sidecar only; the index is not touched."""
        try:
            return hfrecord.read(path)
        except hfrecord.UnsupportedRecord as exc:
            raise RpcError(RECORD_UNSUPPORTED, str(exc))
        except FileNotFoundError as exc:
            raise RpcError(INVALID_PARAMS, str(exc))
        except hfrecord.RecordError as exc:
            raise RpcError(RECORD_UNREADABLE, str(exc))

    def record_resolve(self, record: dict) -> dict:
        try:
            return hfrecord.resolve(self.lib.reader, record)
        except hfrecord.UnsupportedRecord as exc:
            raise RpcError(RECORD_UNSUPPORTED, str(exc))
        except hfrecord.RecordError as exc:
            raise RpcError(INVALID_PARAMS, str(exc))

    def dataset_get(self, dataset_uid: str | None = None, dataset_key: str | None = None) -> dict | None:
        try:
            return self.lib.dataset(dataset_uid=dataset_uid, dataset_key=dataset_key)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc))

    def _jobs(self) -> jobs.JobRunner:
        if self.jobs is None:
            raise RpcError(READ_ONLY, "this core opened the library read-only")
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
