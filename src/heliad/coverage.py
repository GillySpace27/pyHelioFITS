"""Coverage: how many observations fall in each slice of a time range.

The app's Timeline draws every dataset on one time axis from this, and its search
pane draws what the archives hold beside what the library holds, so both sides
are binned here, by the same rule: [start, end] cut into equal bins, an
observation exactly at end counted in the last bin.

Times are compared as SQLite julian days, which parse the index's ISO 8601 UTC
text (with or without a trailing Z); a time SQLite cannot read is counted as
undated rather than dropped.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import times as _times
from .library import WAVELENGTH_BAND_TOLERANCE, Library

DEFAULT_BINS = 240
MAX_BINS = 4000
SINGLE_SPAN_DAYS = 60 / 86400  # the span drawn for a library whose observations are all one instant


def _jd(conn, text: str | None) -> float | None:
    if text is None or text == "":
        return None
    jd = conn.execute("SELECT julianday(?)", (text,)).fetchone()[0]
    if jd is None:
        raise ValueError(f"not a time: {text!r}")
    return jd


def _iso(conn, jd: float) -> str:
    return conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f', ?)", (jd,)).fetchone()[0]


def _bins(bins) -> int:
    n = int(bins)
    if not 1 <= n <= MAX_BINS:
        raise ValueError(f"bins must be 1 to {MAX_BINS}, not {bins}")
    return n


def _dataset_filter(dataset_keys, instrument, wavelength, wavelength_max) -> tuple[str, list]:
    sql, args = "1 = 1", []
    if dataset_keys:
        sql += f" AND dataset_key IN ({','.join('?' * len(dataset_keys))})"
        args += list(dataset_keys)
    if instrument:
        sql += " AND UPPER(instrument) = UPPER(?)"
        args.append(instrument)
    if wavelength is not None:
        if wavelength_max is not None:
            sql += " AND wavelength BETWEEN ? AND ?"
            args += [float(wavelength), float(wavelength_max)]
        else:
            # One passband by the library's own rule, so a search for 193 finds 193.0 and 193.3.
            sql += " AND ABS(wavelength - ?) <= ?"
            args += [float(wavelength), abs(float(wavelength)) * WAVELENGTH_BAND_TOLERANCE]
    return sql, args


def library(lib: Library, start: str | None = None, end: str | None = None, bins: int = DEFAULT_BINS,
            dataset_keys: list[str] | None = None, instrument: str | None = None,
            wavelength: float | None = None, wavelength_max: float | None = None) -> dict:
    """Per dataset, the observations (newest versions only) in each bin of [start, end].

    With no start or end, the range is the library's own first to last observation
    among the datasets asked about. Every dataset that passes the filters is listed,
    in the order library.datasets uses, even when none of its observations fall in
    the range, so a timeline's rows stay put as it is zoomed.
    """
    n = _bins(bins)
    conn = lib.reader
    where, args = _dataset_filter(dataset_keys, instrument, wavelength, wavelength_max)
    lo = _jd(conn, start)
    hi = _jd(conn, end)
    if lo is None or hi is None:
        first, last = conn.execute(
            f"SELECT MIN(julianday(date_obs)), MAX(julianday(date_obs)) FROM current_files WHERE {where}",
            args).fetchone()
        lo = first if lo is None else lo
        hi = last if hi is None else hi
    datasets = conn.execute(
        f"SELECT dataset_key, label, instrument, wavelength FROM datasets WHERE {where} "
        "ORDER BY label, dataset_key", args).fetchall()
    if lo is None or hi is None:  # nothing dated to draw
        return {"start": None, "end": None, "bins": n, "bin_seconds": None,
                "datasets": [_row(d, [0] * n, 0, None, None, _undated(conn, d["dataset_key"])) for d in datasets]}
    if hi < lo:
        raise ValueError("end must not be before start")
    if hi == lo:
        lo, hi = lo - SINGLE_SPAN_DAYS / 2, hi + SINGLE_SPAN_DAYS / 2
    width = (hi - lo) / n
    counts: dict[str, list[int]] = {d["dataset_key"]: [0] * n for d in datasets}
    for key, b, c in conn.execute(
            f"SELECT dataset_key, CAST((julianday(date_obs) - ?) / ? AS INTEGER) AS b, COUNT(*) "
            f"FROM current_files WHERE {where} AND julianday(date_obs) BETWEEN ? AND ? "
            "GROUP BY dataset_key, b", [lo, width, *args, lo, hi]):
        if key in counts:
            counts[key][min(b, n - 1)] += c
    spans = {key: (frames, first, last) for key, frames, first, last in conn.execute(
        f"SELECT dataset_key, COUNT(*), MIN(date_obs), MAX(date_obs) FROM current_files "
        f"WHERE {where} AND julianday(date_obs) BETWEEN ? AND ? GROUP BY dataset_key", [*args, lo, hi])}
    rows = []
    for d in datasets:
        frames, first, last = spans.get(d["dataset_key"], (0, None, None))
        rows.append(_row(d, counts[d["dataset_key"]], frames, first, last, _undated(conn, d["dataset_key"])))
    return {"start": _iso(conn, lo), "end": _iso(conn, hi), "bins": n,
            "bin_seconds": round(width * 86400, 3), "datasets": rows}


def _row(d, counts, frames, first, last, undated) -> dict:
    return {"dataset_key": d["dataset_key"], "label": d["label"], "instrument": d["instrument"],
            "wavelength": d["wavelength"], "counts": counts, "frames": frames,
            "first": first, "last": last, "undated": undated}


def _undated(conn, key: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM current_files WHERE dataset_key = ? AND julianday(date_obs) IS NULL",
                        (key,)).fetchone()[0]


# ---- the archives' side --------------------------------------------------------

def utc_text(value) -> str | None:
    """A record time from an archive as index text, or None when it is not one.

    Fido rows carry ISO text already (astropy's isot); JSOC's T_REC comes as ISO text
    ending in Z or as 2025.09.21_16:00:00_TAI, moved to UTC. The forms are times.parse's;
    this name stays for callers that import it from here.
    """
    return _times.index_text(value)


def times(values, start: str, end: str, bins: int = DEFAULT_BINS) -> dict:
    """Bin record times from an archive search over [start, end], as library() bins the index.

    Pure Python and no index, since a search runs on the core's background thread."""
    n = _bins(bins)
    lo_text, hi_text = utc_text(start), utc_text(end)
    if lo_text is None or hi_text is None:
        raise ValueError(f"not a time range: {start!r} to {end!r}")
    lo, hi = datetime.fromisoformat(lo_text), datetime.fromisoformat(hi_text)
    if hi < lo:
        raise ValueError("end must not be before start")
    if hi == lo:
        half = timedelta(days=SINGLE_SPAN_DAYS / 2)
        lo, hi = lo - half, hi + half
    span = (hi - lo).total_seconds()
    counts, outside, undated = [0] * n, 0, 0
    for value in values:
        text = utc_text(value)
        if text is None:
            undated += 1
            continue
        when = datetime.fromisoformat(text)
        if lo <= when <= hi:
            counts[min(int((when - lo).total_seconds() / span * n), n - 1)] += 1
        else:
            outside += 1
    return {"start": _text(lo), "end": _text(hi), "bins": n, "bin_seconds": round(span / n, 3),
            "counts": counts, "outside": outside, "undated": undated}


def _text(when: datetime) -> str:
    return _times.text(when)
