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


def fetch(query: Query, staging: Path, *, settings: dict, phase, max_conn: int = 4) -> dict:
    """Search, then fetch every result into staging.

    Fido skips files already complete in staging (overwrite=False), so a job
    that is run again after an interruption fetches only what is missing. The
    fetch itself is one call and cannot be interrupted part way.
    """
    from sunpy.net import Fido

    phase("searching")
    result = Fido.search(*query.to_fido(settings.get("jsoc_email")))
    found = getattr(result, "file_num", None)
    phase("fetching", 0, found)
    fetched = Fido.fetch(result, path=str(staging / "{file}"), overwrite=False,
                         max_conn=max_conn, progress=False)
    return {"found": found, "paths": [Path(p) for p in fetched if is_fits(Path(p))],
            "errors": [str(e) for e in fetched.errors], "urls": {}}
