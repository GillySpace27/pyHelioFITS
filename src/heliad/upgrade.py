"""The cache editor's Upgrade panel: ask the archive again for a dataset.

A dataset's query is the newest archive query stored with its files, widened to
the dataset's span; a dataset that was imported rather than downloaded has one
inferred from its row instead, and says so. From that query the panel compares
cadences (what the archive holds against what the library holds), densifies a
dataset (a job that fetches only the frames it lacks) and refetches one frame (a
newer version becomes current through the ingest rules; an identical file is a
duplicate).

Two times are one frame when they lie within the tolerance: half the spacing,
clamped to [MIN_TOLERANCE_S, MAX_TOLERANCE_S]. The spacing is the dataset's
median spacing, or the target cadence when that is denser (sample_s for an
upgrade, the archive's median spacing for cadences): densifying 24 s to 12 s
puts the new frames 12 s from the old ones, and half of 24 s would call them
had. A dataset with fewer than two observations has no spacing and gets the
maximum. Rejected observations count as had, so an upgrade never fetches a
rejected frame again.
"""

from __future__ import annotations

import bisect
import re
import statistics
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from . import coverage, db, sources
from .library import Library
from .query import Query

MIN_TOLERANCE_S = 1.0
MAX_TOLERANCE_S = 60.0


def seconds(text) -> float | None:
    """A time as seconds since 1970 UTC (any form coverage.utc_text reads), or None."""
    iso = coverage.utc_text(text)
    if iso is None:
        return None
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


def _iso(value: float) -> str:
    when = datetime(1970, 1, 1) + timedelta(milliseconds=round(value * 1000))
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def median_spacing(times) -> float | None:
    """Median gap between distinct sorted times (seconds), rounded to the millisecond."""
    ts = sorted(set(times))
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    return round(statistics.median(gaps), 3) if gaps else None


def tolerance(spacing_s: float | None, target_s: float | None = None) -> float:
    """Half the spacing (the denser of the dataset's and the target's), clamped."""
    known = [s for s in (spacing_s, target_s) if s]
    if not known:
        return MAX_TOLERANCE_S
    return round(min(MAX_TOLERANCE_S, max(MIN_TOLERANCE_S, min(known) / 2)), 3)


def near(sorted_times: list[float], t: float, tol: float) -> bool:
    """Whether a time in sorted_times lies within tol of t."""
    i = bisect.bisect_left(sorted_times, t)
    return any(abs(sorted_times[j] - t) <= tol for j in (i - 1, i) if 0 <= j < len(sorted_times))


def observation_times(conn, dataset_key: str, *, start: float | None = None,
                      end: float | None = None, rejected: bool = True) -> list[float]:
    """Sorted times of a dataset's observations (rejected ones too, unless rejected=False)."""
    sql = ("SELECT o.date_obs FROM observations o JOIN datasets d ON d.id = o.dataset_id "
           "WHERE d.dataset_key = ?")
    if not rejected and db.has_table(conn, "rejections"):
        sql += " AND o.id NOT IN (SELECT observation_id FROM rejections)"
    out = []
    for (text,) in conn.execute(sql, (dataset_key,)):
        t = seconds(text)
        if t is not None and (start is None or t >= start) and (end is None or t <= end):
            out.append(t)
    return sorted(out)


def skip_function(conn, dataset_key: str, tol: float):
    """skip(time) for a source's fetch: true when the dataset already has that frame."""
    have = observation_times(conn, dataset_key)

    def skip(when) -> bool:
        t = seconds(when if isinstance(when, str) else getattr(when, "isot", str(when)))
        return t is not None and near(have, t, tol)
    return skip


def _dataset(conn, dataset_key: str):
    row = conn.execute("SELECT * FROM datasets WHERE dataset_key = ?", (dataset_key,)).fetchone()
    if row is None:
        raise KeyError(f"no dataset {dataset_key!r}")
    return row


def _sunpy_source(observatory: str | None) -> str | None:
    """The observatory as a sunpy attrs.Source, when sunpy registers a Source of that name.

    The registry ships with sunpy (no network). Without sunpy, nothing is claimed.
    """
    if not observatory:
        return None
    try:
        from sunpy.net import attrs as a
    except ImportError:
        return None
    name = re.sub(r"[^a-z0-9]", "_", observatory.strip().lower())
    return observatory if hasattr(a.Source, name) else None


def dataset_query(lib: Library, dataset_key: str, conn=None) -> dict:
    """The query that asks the archive again for this dataset.

    {"query", "source", "inferred", "basis"}. The query has every field; one that
    could not be read is null, never guessed.
    """
    conn = conn or lib.reader
    d = _dataset(conn, dataset_key)
    times = observation_times(conn, dataset_key)
    if not times:
        raise ValueError(f"dataset {dataset_key!r} has no dated observations")
    first, last = times[0], times[-1]
    stored = conn.execute(
        "SELECT q.id, q.source, q.query_json FROM queries q WHERE q.id IN ("
        " SELECT f.query_id FROM files f JOIN observations o ON o.id = f.observation_id"
        " JOIN datasets d ON d.id = o.dataset_id WHERE d.dataset_key = ?)"
        f" AND q.source IN ({','.join('?' * len(sources.SOURCES))})"
        " ORDER BY q.created_at DESC, q.id DESC LIMIT 1",
        (dataset_key, *sources.SOURCES)).fetchone()
    if stored is not None:
        q = Query.from_json(stored["query_json"])
        lo = min(t for t in (seconds(q.start), first) if t is not None)
        hi = max(t for t in (seconds(q.end), last) if t is not None)
        q = replace(q, start=_iso(lo), end=_iso(hi))
        return {"query": asdict(q), "source": stored["source"], "inferred": False,
                "basis": f"stored query {stored['id']} ({stored['source']}), widened to the "
                         "dataset's first and last observation"}
    source = _sunpy_source(d["observatory"])
    q = Query(start=_iso(first), end=_iso(last), instrument=d["instrument"], wavelength=d["wavelength"],
              source=source, detector=d["detector"], level=d["level"], sample_s=median_spacing(times))
    basis = ("inferred from the dataset (instrument, wavelength, detector and level as its headers "
             "give them), its span, and the median spacing of its observations")
    if d["observatory"] and source is None:
        basis += f"; observatory {d['observatory']!r} is not a sunpy Source here, so source is left null"
    return {"query": asdict(q), "source": "fido", "inferred": True, "basis": basis}


def _query_with(base: dict, **changes) -> Query:
    return replace(Query.from_json(base["query"]), **changes)


def dataset_tolerance(conn, dataset_key: str, target_s: float | None = None) -> float:
    return tolerance(median_spacing(observation_times(conn, dataset_key)), target_s)


def upgrade(lib: Library, runner, dataset_key: str, sample_s=None, start=None, end=None,
            dry_run: bool = True) -> dict:
    """Densify or fill a dataset: a job fetching only the frames it lacks.

    sample_s, start and end replace the query's own. A dry run returns the query
    and the job's options without queueing; otherwise the queued job.
    """
    base = dataset_query(lib, dataset_key)
    changes = {k: v for k, v in (("sample_s", sample_s), ("start", start), ("end", end)) if v is not None}
    q = _query_with(base, **changes)
    tol = dataset_tolerance(lib.reader, dataset_key, q.sample_s)
    options = {"dataset_key": dataset_key, "missing_only": True, "tolerance_s": tol}
    if dry_run:
        return {"dry_run": True, "query": asdict(q), "source": base["source"], "options": options,
                "inferred": base["inferred"], "basis": base["basis"]}
    return runner.enqueue(q, base["source"], options=options)


def refetch(lib: Library, runner, observation_id: int) -> dict:
    """A job asking the archive again for one observation's frame."""
    row = lib.reader.execute(
        "SELECT o.date_obs, d.dataset_key FROM observations o JOIN datasets d ON d.id = o.dataset_id "
        "WHERE o.id = ?", (observation_id,)).fetchone()
    if row is None:
        raise KeyError(f"no observation {observation_id}")
    t = seconds(row["date_obs"])
    if t is None:
        raise ValueError(f"observation {observation_id} has no time to ask the archive for")
    base = dataset_query(lib, row["dataset_key"])
    tol = dataset_tolerance(lib.reader, row["dataset_key"])
    q = _query_with(base, start=_iso(t - tol), end=_iso(t + tol), sample_s=None)
    return runner.enqueue(q, base["source"], options={"missing_only": False, "observation_id": observation_id})


def cadences(lib: Library, resolve, dataset_key: str, start=None, end=None, *,
             settings: dict | None = None) -> dict:
    """What the archive holds for a dataset against what the library holds.

    Searches the archive (nothing is downloaded) over the range, at full cadence.
    missing lists the archive's record times with no observation of the dataset
    (rejected ones included) within the tolerance. Reads the index through its own
    connection, since it runs on the core's background pool. resolve(name) gives
    the source module for the query's source.
    """
    conn = db.connect_reader(lib.cfg.db_path)
    try:
        base = dataset_query(lib, dataset_key, conn)
        changes = {k: v for k, v in (("start", start), ("end", end)) if v is not None}
        q = _query_with(base, sample_s=None, **changes)
        lo, hi = seconds(q.start), seconds(q.end)
        have = observation_times(conn, dataset_key, start=lo, end=hi)
        shown = observation_times(conn, dataset_key, start=lo, end=hi, rejected=False)
        lib_spacing = median_spacing(have)
    finally:
        conn.close()
    found = resolve(base["source"]).search(q, settings=settings or {})
    raw = list(found.get("times") or [])
    for provider in found.get("providers", []):
        raw += provider.get("times") or []
    archive = sorted({t for t in (seconds(v) for v in raw) if t is not None})
    arch_spacing = median_spacing(archive)
    tol = tolerance(lib_spacing, arch_spacing)
    missing = [_iso(t) for t in archive if not near(have, t, tol)]
    return {"dataset_key": dataset_key, "query": asdict(q), "source": base["source"],
            "tolerance_s": tol,
            "library": {"count": len(have), "median_spacing_s": lib_spacing,
                        "rejected": len(have) - len(shown)},
            "archive": {"count": len(archive), "median_spacing_s": arch_spacing},
            "missing": missing}
