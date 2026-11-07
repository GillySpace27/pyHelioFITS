"""JSOC exports by record set, through drms.

Fido reaches JSOC too; this route exists for what Fido does not expose well:
an explicit record set (any series, prime keys, cadence) exported as FITS
with headers attached. JSOC requires an email address registered at
http://jsoc.stanford.edu/ajax/register_email.html, taken from the jsoc_email
setting or HFCORE_JSOC_EMAIL.

Written against drms 0.9 (Client(email=), Client.export, ExportRequest.wait
and .download); not run against JSOC from the build container, which cannot
reach it. The first real export is its test.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import times
from ..library import is_fits
from ..query import Query
from . import ArchiveError


class NoEmail(ArchiveError):
    pass


def _iso(t: str) -> datetime:
    when = times.parse(t)
    if when is None:
        raise ValueError(f"not a time: {t!r}")
    return when


def recordset(query: Query) -> str:
    """The JSOC record set for a query: series[start/duration@cadence][wavelength].

    AIA series take the wavelength as their second prime key; other series
    ignore it.
    """
    if not query.jsoc_series:
        raise ValueError("a JSOC query needs jsoc_series, e.g. aia.lev1_euv_12s")
    start, end = _iso(query.start), _iso(query.end)
    seconds = int((end - start).total_seconds())
    if seconds <= 0:
        raise ValueError("end must be after start")
    when = f"{start:%Y-%m-%dT%H:%M:%S}Z/{seconds}s"
    if query.sample_s:
        when += f"@{int(query.sample_s)}s"
    rs = f"{query.jsoc_series}[{when}]"
    if query.wavelength is not None and query.jsoc_series.startswith("aia."):
        rs += f"[{int(round(query.wavelength))}]"
    return rs


def _client(settings: dict):
    import drms

    email = settings.get("jsoc_email")
    if not email:
        raise NoEmail("JSOC needs a registered email: hfcore settings jsoc_email you@example.org")
    return drms.Client(email=email)


def search(query: Query, *, settings: dict | None = None) -> dict:
    import drms

    rs = recordset(query)
    try:
        keys = drms.Client().query(rs, key="T_REC,WAVELNTH,QUALITY")
    except Exception as exc:
        raise ArchiveError(f"JSOC query failed: {type(exc).__name__}: {exc}") from exc
    return {"query": query.to_json(), "recordset": rs, "total": len(keys),
            "rows": keys.astype(str).to_dict("records")[:2000],
            "times": [str(t) for t in keys["T_REC"]]}  # every record; the core bins them


def record_time(record: str) -> str | None:
    """T_REC from an export's record name, series[T_REC][...]: the first prime key.

    That T_REC comes first holds for the series HelioFITS Core builds record sets for
    (aia.lev1_*, hmi.*); for another series this may read a different key, which
    then fails to parse as a time and the record is fetched.
    """
    m = re.match(r"^[^\[]+\[([^\]]+)\]", str(record))
    return m.group(1) if m else None


def fetch(query: Query, staging: Path, *, settings: dict, phase, skip=None) -> dict:
    """Export the record set and download what staging lacks.

    With skip, a record whose T_REC (read from the urls table's record column) it
    claims is not downloaded; the export itself still covers the whole record set.
    When the urls table has no record column, skip is not applied, everything is
    downloaded, and ingest finds the copies the library already holds.
    """
    rs = recordset(query)
    client = _client(settings)
    phase("exporting")
    request = client.export(rs, method="url", protocol="fits")
    request.wait()
    if not request.has_succeeded():
        raise RuntimeError(f"JSOC export {request.id} did not succeed (status {request.status})")
    urls = request.urls
    wanted = list(range(len(urls)))
    skipped = None
    if skip is not None and "record" in urls.columns:
        wanted = [i for i, rec in enumerate(urls["record"])
                  if not ((t := record_time(rec)) and times.parse(t) and skip(t))]
        skipped = len(urls) - len(wanted)
    phase("fetching", 0, len(wanted))
    # Records already staged by an earlier, interrupted run are not fetched again.
    have = {p.name for p in staging.iterdir() if p.is_file()}
    names = list(urls["filename"])
    todo = [i for i in wanted if names[i] not in have]
    errors = []
    if todo:
        table = request.download(str(staging), index=todo)
        for _, row in table.iterrows():
            if row["download"] is None:
                errors.append(f"{row['record']}: download failed")
    by_name = {row["filename"]: row["url"] for _, row in urls.iterrows()}
    paths = sorted(p for p in staging.iterdir() if p.is_file() and is_fits(p) and p.name in by_name)
    out = {"found": len(wanted), "paths": paths, "errors": errors,
           "urls": {p.name: by_name[p.name] for p in paths}}
    if skipped is not None:
        out["skipped"] = skipped
    return out
