"""Bring a dataset up to the archive's newest frame: template.latest.

A HelioFITS Studio template (a saved session) shows "the latest" of some datasets.
Refreshing one asks the archive, for each dataset, what its newest frame is, and
when that is newer than the library's newest current frame, queues ONE download
job for the span before it (span_s ending at the archive's newest frame). The job
is an ordinary download job with the options dataset.upgrade gives one (see
upgrade.py), so it reports download.progress and download.finished, runs in the
same queue, and fetches only the frames the library lacks.

The archive is asked through the dataset's query (upgrade.dataset_query: stored,
or inferred from the dataset), narrowed to the lookback window [now - lookback_s,
now] and searched the way the search method searches. The query keeps its
sample_s, so the newest frame found can be up to one sample older than the
archive's very newest. Two times are one frame within the dataset's tolerance
(upgrade.dataset_tolerance), so the library is behind only when the archive's
newest frame is later than its own by more than that.

One entry comes back per dataset asked for, in order. A dataset that is not in
the library, has no query to ask with, or whose archive search fails says so in
its entry; the others go on.

A Studio session file is JSON with the state under "org.helioviewer.jhv.state".
Studio writes no dataset uid or key into it: a layer it loaded from the FITS
library (FitsLibraryDatasetLoad, the Local FITS Library tab's Add) is a plain list of file URIs,
"imageLayers"[i]["data"]["uris"], each the library root resolved against the
file's relpath (FitsLibrary.files). So a layer is a library dataset when its
file URIs name files this library stores; the index says which dataset each
file belongs to. Layers from other archives (an "APIRequest" to Helioviewer, a
"fitsRequest", files outside the library) are listed as skipped.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlparse

from . import db, times, upgrade
from .library import Library
from .query import Query

DEFAULT_SPAN_S = 7200            # 2 hours
DEFAULT_LOOKBACK_S = 3 * 86400   # 3 days

STATE_KEY = "org.helioviewer.jhv.state"
NOT_HELIAD = "not a library dataset"

# What an entry's status is; "would_queue" is a dry run's "queued".
STATUSES = ("queued", "would_queue", "up_to_date", "no_query", "archive_error", "not_found")


def now_seconds() -> float:
    """Now, as seconds since 1970 UTC. Tests replace it."""
    return time.time()


def latest(lib: Library, resolve, runner, datasets, *, span_s=DEFAULT_SPAN_S,
           lookback_s=DEFAULT_LOOKBACK_S, dry_run: bool = False, settings: dict | None = None) -> list[dict]:
    """One entry per dataset in datasets (uids or keys; an old key resolves too).

    resolve(name) gives the source module for a query's source; runner queues the
    job (it may be None for a dry run). Reads the index through its own connection,
    since the core runs this on its background pool.
    """
    if isinstance(datasets, str) or not all(isinstance(d, str) for d in datasets):
        raise TypeError("datasets must be a list of dataset uids or keys")
    span_s, lookback_s = _positive("span_s", span_s), _positive("lookback_s", lookback_s)
    if not dry_run and runner is None:
        raise ValueError("queueing a job needs a writer")
    now = now_seconds()
    conn = db.connect_reader(lib.cfg.db_path)
    try:
        return [_one(lib, conn, resolve, runner, name, now, span_s, lookback_s, dry_run, settings or {})
                for name in datasets]
    finally:
        conn.close()


def _positive(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds")
    return float(value)


def _entry(requested: str, found: dict | None) -> dict:
    return {"dataset_uid": found["dataset_uid"] if found else None,
            "dataset_key": found["dataset_key"] if found else None,
            "requested": requested, "newest_local": None, "newest_remote": None,
            "job_id": None, "start": None, "end": None, "status": "not_found"}


def newest_local(conn, dataset_key: str) -> str | None:
    """The newest current frame of a dataset (rejected ones are not current), or None."""
    (text,) = conn.execute("SELECT MAX(date_obs) FROM current_files WHERE dataset_key = ?",
                           (dataset_key,)).fetchone()
    return times.index_text(text)


def _askable(base: dict) -> bool:
    """Whether a dataset's query names something an archive can be asked for. A stored
    query was asked once; an inferred one needs at least an instrument or a series."""
    q = base["query"]
    return not base["inferred"] or bool(q.get("instrument") or q.get("jsoc_series"))


def _record_times(found: dict) -> list[float]:
    raw = list(found.get("times") or [])
    for provider in found.get("providers") or []:
        raw += provider.get("times") or []
    return [t for t in (times.seconds(v) for v in raw) if t is not None]


def _one(lib, conn, resolve, runner, requested, now, span_s, lookback_s, dry_run, settings) -> dict:
    found = lib.resolve_dataset(requested, conn)
    entry = _entry(requested, found)
    if found is None:
        return entry
    key = found["dataset_key"]
    entry["newest_local"] = local = newest_local(conn, key)
    try:
        base = upgrade.dataset_query(lib, key, conn)
    except ValueError:  # no dated observations: nothing to infer a query from
        base = None
    if base is None or not _askable(base):
        entry["status"] = "no_query"
        return entry
    query = Query.from_json(base["query"])
    window = replace(query, start=times.from_seconds(now - lookback_s), end=times.from_seconds(now))
    try:
        result = resolve(base["source"]).search(window, settings=settings)
    except Exception as exc:  # the archive is down, refuses the query, or the source is missing
        entry.update(status="archive_error", error=f"{type(exc).__name__}: {exc}")
        return entry
    remote = max(_record_times(result), default=None)
    if remote is None:  # the archive holds nothing in the lookback window
        entry["status"] = "up_to_date"
        return entry
    entry["newest_remote"] = times.from_seconds(remote)
    tol = upgrade.dataset_tolerance(conn, key, query.sample_s)
    local_s = times.seconds(local)
    if local_s is not None and remote <= local_s + tol:
        entry["status"] = "up_to_date"
        return entry
    entry["start"], entry["end"] = times.from_seconds(remote - span_s), entry["newest_remote"]
    if dry_run:
        entry["status"] = "would_queue"
        return entry
    job_query = replace(query, start=entry["start"], end=entry["end"])
    job = runner.enqueue(job_query, base["source"],
                         options={"dataset_key": key, "missing_only": True, "tolerance_s": tol})
    entry.update(job_id=job["job_id"], status="queued")
    return entry


# ---- Studio session files -------------------------------------------------------------

def session_state(document) -> dict:
    """The state of a Studio session file's JSON, or ValueError when it is not one."""
    state = document.get(STATE_KEY) if isinstance(document, dict) else None
    if not isinstance(state, dict):
        raise ValueError(f"not a HelioFITS Studio session: no {STATE_KEY!r} object at the top level")
    return state


def _uri_path(text) -> Path | None:
    """The local path a file: URI names (Java's File.toURI form, file:/abs/path), or None."""
    if not isinstance(text, str):
        return None
    u = urlparse(text)
    if u.scheme != "file":
        return None
    path = unquote(u.path)
    if u.netloc and u.netloc != "localhost":
        path = f"//{u.netloc}{path}"
    if re.match(r"^/[A-Za-z]:[/\\]", path):  # file:/C:/... on Windows
        return Path(PureWindowsPath(path[1:]))
    return Path(path)


def _relpath(lib: Library, path: Path) -> str | None:
    """path relative to the library root, as the index writes relpaths, or None."""
    roots = {Path(lib.root), Path(lib.root).resolve()}
    candidates = {Path(os.path.normpath(path))}
    try:
        candidates.add(path.resolve())
    except OSError:
        pass
    for root in roots:
        for cand in candidates:
            try:
                return cand.relative_to(root).as_posix()
            except ValueError:
                continue
    return None


def layer_datasets(lib: Library, layer: dict, conn=None) -> list[str]:
    """The uids of the library datasets whose files a session layer shows, in first-seen order."""
    conn = conn or lib.reader
    data = layer.get("data") if isinstance(layer, dict) else None
    uris = data.get("uris") if isinstance(data, dict) else None
    out: list[str] = []
    for uri in uris if isinstance(uris, list) else []:
        path = _uri_path(uri)
        rel = _relpath(lib, path) if path is not None else None
        if rel is None:
            continue
        row = conn.execute(
            "SELECT d.dataset_uid FROM files f JOIN observations o ON o.id = f.observation_id "
            "JOIN datasets d ON d.id = o.dataset_id WHERE f.relpath = ?", (rel,)).fetchone()
        if row is not None and row["dataset_uid"] and row["dataset_uid"] not in out:
            out.append(row["dataset_uid"])
    return out


def refresh(lib: Library, resolve, runner, document, *, span_s=DEFAULT_SPAN_S,
            lookback_s=DEFAULT_LOOKBACK_S, dry_run: bool = False, settings: dict | None = None) -> list[dict]:
    """template.latest for the library datasets a Studio session shows.

    One entry per image layer and dataset, in the session's order: the dataset's
    template.latest entry with the layer's name, or {layer, status: "skipped",
    reason} for a layer that is not a library dataset. A dataset two layers show is
    asked for (and queued) once.
    """
    state = session_state(document)
    layers = state.get("imageLayers") or []
    found = [(layer, layer_datasets(lib, layer)) for layer in layers if isinstance(layer, dict)]
    wanted = list(dict.fromkeys(uid for _, uids in found for uid in uids))
    entries = dict(zip(wanted, latest(lib, resolve, runner, wanted, span_s=span_s,
                                      lookback_s=lookback_s, dry_run=dry_run, settings=settings)))
    out = []
    for layer, uids in found:
        name = layer.get("name")
        if not uids:
            out.append({"layer": name, "status": "skipped", "reason": NOT_HELIAD})
        out += [{"layer": name, **entries[uid]} for uid in uids]
    return out
