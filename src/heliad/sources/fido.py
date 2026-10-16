"""Search and fetch through sunpy's Fido (VSO, JSOC, and the clients sunpy registers).

Needs the optional "archives" extra (sunpy[net], drms). Not exercised by the
offline tests: the cloud build container cannot reach the archives.
"""

from __future__ import annotations

from pathlib import Path

from ..library import Library, is_fits
from ..query import Query


def _jsonable(value):
    if hasattr(value, "isot"):
        return value.isot
    if hasattr(value, "unit") and hasattr(value, "value"):
        return f"{value.value} {value.unit}"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def search(query: Query, limit: int = 2000) -> dict:
    from sunpy.net import Fido

    result = Fido.search(*query.to_fido())
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


def download(lib: Library, query: Query, *, progress=None, max_conn: int = 4) -> dict:
    """Search, fetch into the staging area, ingest everything that arrived.

    Resumes at file level: Fido skips files already complete in staging, and a
    file already in the library by checksum is dropped as a duplicate.
    """
    from sunpy.net import Fido

    staging = lib.cfg.staging_dir / "fido"
    staging.mkdir(parents=True, exist_ok=True)
    result = Fido.search(*query.to_fido())
    fetched = Fido.fetch(result, path=str(staging / "{file}"), overwrite=False,
                         max_conn=max_conn, progress=False)
    counts: dict[str, int] = {}
    paths = [Path(p) for p in fetched if is_fits(Path(p))]
    for i, p in enumerate(paths, 1):
        r = lib.ingest(p, consume=True, query=query, source="fido")
        counts[r.status] = counts.get(r.status, 0) + 1
        if progress:
            progress(i, len(paths), p)
    return {"found": getattr(result, "file_num", None), "fetched": len(fetched),
            "errors": [str(e) for e in fetched.errors], "ingest": counts}
