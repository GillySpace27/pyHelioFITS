"""Search and fetch through sunpy's Fido (VSO, JSOC, and the clients sunpy registers).

Needs the optional "archives" extra (sunpy[net], drms). Not exercised by the
offline tests: the cloud build container cannot reach the archives.
"""

from __future__ import annotations

from pathlib import Path

from ..library import is_fits
from ..query import Query
from . import ArchiveError


def _jsonable(value):
    if hasattr(value, "isot"):
        return value.isot
    if hasattr(value, "unit") and hasattr(value, "value"):
        return f"{value.value} {value.unit}"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def search(query: Query, *, settings: dict | None = None, limit: int = 2000) -> dict:
    from sunpy.net import Fido

    attrs = query.to_fido((settings or {}).get("jsoc_email"))
    try:
        result = Fido.search(*attrs)
    except Exception as exc:  # sunpy's clients raise many kinds; none is this query's fault
        raise ArchiveError(f"archive search failed: {type(exc).__name__}: {exc}") from exc
    providers = []
    for block in result:
        rows = []
        for row in block[:limit]:
            rows.append({name: _jsonable(row[name]) for name in block.colnames})
        provider = {
            "client": type(block.client).__name__ if getattr(block, "client", None) else None,
            "count": len(block),
            "columns": list(block.colnames),
            "rows": rows,
        }
        if "Start Time" in block.colnames:  # every record, not just the rows shown; the core bins them
            provider["times"] = [_jsonable(t) for t in block["Start Time"]]
        providers.append(provider)
    return {"query": query.to_json(), "total": sum(p["count"] for p in providers),
            "providers": providers}


# VSO caching mirrors tried, in order, for records the first fetch could not get.
# The names are VSO site codes from sunpy's VSOClient.fetch documentation; which
# of them are serving on a given day is not known in advance. The vso_mirrors
# setting (comma-separated, or "none") replaces the list.
VSO_MIRRORS = ("NSO", "SAO", "ROB")


def mirrors(settings: dict) -> list[str]:
    value = (settings.get("vso_mirrors") or "").strip()
    if not value:
        return list(VSO_MIRRORS)
    if value.lower() == "none":
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def _without_skipped(result, skip) -> tuple[list, int]:
    """Each block's rows whose "Start Time" skip does not claim, and how many were left out.

    A block with no "Start Time" column is kept whole. Slicing a QueryResponseTable
    with a boolean mask keeps its client (checked offline against sunpy 8.0);
    that Fido.fetch then gets exactly those records from each archive is not
    verified here, since the build container cannot reach one.
    """
    import numpy as np

    blocks, skipped = [], 0
    for block in result:
        if "Start Time" in block.colnames:
            keep = np.array([not skip(_jsonable(t)) for t in block["Start Time"]], dtype=bool)
            skipped += int((~keep).sum())
            block = block[keep]
        if len(block):
            blocks.append(block)
    return blocks, skipped


def fetch(query: Query, staging: Path, *, settings: dict, phase, max_conn: int = 4, skip=None) -> dict:
    """Search, then fetch every result into staging.

    Records the first fetch could not get are asked for again from the other
    VSO mirrors, one at a time, until none are missing. Fido skips files
    already complete in staging (overwrite=False), so a job that is run again
    after an interruption fetches only what is missing; VSO names a file only
    once the server answers, so a staged VSO file is found by looking in
    staging rather than through Fido. Each fetch is one call and cannot be
    interrupted part way.

    With skip, records whose start time it claims are dropped from each block
    before anything is fetched, and the mirrors are asked only for what is left.
    """
    from sunpy.net import Fido
    from sunpy.net.vso import VSOClient

    phase("searching")
    result = Fido.search(*query.to_fido(settings.get("jsoc_email")))
    found = getattr(result, "file_num", None)
    blocks, skipped = [result], None
    if skip is not None:
        blocks, skipped = _without_skipped(result, skip)
        found = sum(len(b) for b in blocks)
    target = str(staging / "{file}")
    phase("fetching", 0, found)
    if blocks:
        fetched = Fido.fetch(*blocks, path=target, overwrite=False, max_conn=max_conn, progress=False)
        errors = [str(e) for e in fetched.errors]
    else:  # everything the search found is already in the library
        errors = []
    attempts = [{"site": "default", "failed": len(errors)}]
    vso = [block for block in (result if skip is None else blocks)
           if isinstance(getattr(block, "client", None), VSOClient)]
    for site in mirrors(settings) if vso else []:
        if not errors:
            break
        phase(f"fetching from mirror {site}", _staged(staging), found)
        again = Fido.fetch(*vso, path=target, overwrite=False, max_conn=max_conn,
                           progress=False, site=site)
        errors = [str(e) for e in again.errors]
        attempts.append({"site": site, "failed": len(again.errors)})
    out = {"found": found, "paths": _fits_in(staging), "errors": errors,
           "attempts": attempts, "urls": {}}
    if skipped is not None:
        out["skipped"] = skipped
    return out


def _fits_in(staging: Path) -> list[Path]:
    return sorted(p for p in staging.iterdir() if p.is_file() and is_fits(p))


def _staged(staging: Path) -> int:
    return len(_fits_in(staging))
