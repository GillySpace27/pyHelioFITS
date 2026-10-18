"""Search and fetch through sunpy's Fido (VSO, JSOC, and the clients sunpy registers).

Needs the optional "archives" extra (sunpy[net], drms). Not exercised by the
offline tests: the cloud build container cannot reach the archives.
"""

from __future__ import annotations

from pathlib import Path

from ..library import is_fits
from ..query import Query


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

    result = Fido.search(*query.to_fido((settings or {}).get("jsoc_email")))
    providers = []
    for block in result:
        rows = []
        for row in block[:limit]:
            rows.append({name: _jsonable(row[name]) for name in block.colnames})
        providers.append({
            "client": type(block.client).__name__ if getattr(block, "client", None) else None,
            "count": len(block),
            "columns": list(block.colnames),
            "rows": rows,
        })
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


def fetch(query: Query, staging: Path, *, settings: dict, phase, max_conn: int = 4) -> dict:
    """Search, then fetch every result into staging.

    Records the first fetch could not get are asked for again from the other
    VSO mirrors, one at a time, until none are missing. Fido skips files
    already complete in staging (overwrite=False), so a job that is run again
    after an interruption fetches only what is missing; VSO names a file only
    once the server answers, so a staged VSO file is found by looking in
    staging rather than through Fido. Each fetch is one call and cannot be
    interrupted part way.
    """
    from sunpy.net import Fido
    from sunpy.net.vso import VSOClient

    phase("searching")
    result = Fido.search(*query.to_fido(settings.get("jsoc_email")))
    found = getattr(result, "file_num", None)
    target = str(staging / "{file}")
    phase("fetching", 0, found)
    fetched = Fido.fetch(result, path=target, overwrite=False, max_conn=max_conn, progress=False)
    errors = [str(e) for e in fetched.errors]
    attempts = [{"site": "default", "failed": len(fetched.errors)}]
    vso = [block for block in result if isinstance(getattr(block, "client", None), VSOClient)]
    for site in mirrors(settings) if vso else []:
        if not errors:
            break
        phase(f"fetching from mirror {site}", _staged(staging), found)
        again = Fido.fetch(*vso, path=target, overwrite=False, max_conn=max_conn,
                           progress=False, site=site)
        errors = [str(e) for e in again.errors]
        attempts.append({"site": site, "failed": len(again.errors)})
    return {"found": found, "paths": _fits_in(staging), "errors": errors,
            "attempts": attempts, "urls": {}}


def _fits_in(staging: Path) -> list[Path]:
    return sorted(p for p in staging.iterdir() if p.is_file() and is_fits(p))


def _staged(staging: Path) -> int:
    return len(_fits_in(staging))
