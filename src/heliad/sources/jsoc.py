"""JSOC exports by record set, through drms.

Fido reaches JSOC too; this route exists for what Fido does not expose well:
an explicit record set (any series, prime keys, cadence) exported as FITS
with headers attached. JSOC requires an email address registered at
http://jsoc.stanford.edu/ajax/register_email.html, taken from the jsoc_email
setting or HELIAD_JSOC_EMAIL.

Written against drms 0.9 (Client(email=), Client.export, ExportRequest.wait
and .download); not run against JSOC from the build container, which cannot
reach it. The first real export is its test.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..library import is_fits
from ..query import Query


class NoEmail(RuntimeError):
    pass


def _iso(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).replace(tzinfo=None)


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
        raise NoEmail("JSOC needs a registered email: heliad settings jsoc_email you@example.org")
    return drms.Client(email=email)


def search(query: Query, *, settings: dict | None = None) -> dict:
    import drms

    rs = recordset(query)
    keys = drms.Client().query(rs, key="T_REC,WAVELNTH,QUALITY")
    return {"query": query.to_json(), "recordset": rs, "total": len(keys),
            "rows": keys.astype(str).to_dict("records")[:2000],
            "times": [str(t) for t in keys["T_REC"]]}  # every record; the core bins them


def fetch(query: Query, staging: Path, *, settings: dict, phase) -> dict:
    rs = recordset(query)
    client = _client(settings)
    phase("exporting")
    request = client.export(rs, method="url", protocol="fits")
    request.wait()
    if not request.has_succeeded():
        raise RuntimeError(f"JSOC export {request.id} did not succeed (status {request.status})")
    urls = request.urls
    phase("fetching", 0, len(urls))
    # Records already staged by an earlier, interrupted run are not fetched again.
    have = {p.name for p in staging.iterdir() if p.is_file()}
    todo = [i for i, name in enumerate(urls["filename"]) if name not in have]
    errors = []
    if todo:
        table = request.download(str(staging), index=todo)
        for _, row in table.iterrows():
            if row["download"] is None:
                errors.append(f"{row['record']}: download failed")
    by_name = {row["filename"]: row["url"] for _, row in urls.iterrows()}
    paths = sorted(p for p in staging.iterdir() if p.is_file() and is_fits(p) and p.name in by_name)
    return {"found": len(urls), "paths": paths, "errors": errors,
            "urls": {p.name: by_name[p.name] for p in paths}}
