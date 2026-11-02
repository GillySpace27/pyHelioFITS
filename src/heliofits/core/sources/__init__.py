"""Archive sources. Each one turns a Query into records and records into files.

A source is a module with

    search(query, *, settings) -> dict
        what the archive holds, for display; downloads nothing
    fetch(query, staging, *, settings, phase, skip=None) -> dict
        fetch into the staging folder; returns {"found", "paths", "errors", "urls"},
        and "skipped" when skip left records out

phase(name, done=None, total=None) reports progress and is where a cancel
takes effect, so a source calls it between steps. Files already complete in
staging are kept, so fetching again after an interruption resumes.

skip(time) -> bool, when given, is asked about each record's time (ISO 8601
text, or the archive's own form such as JSOC's T_REC) before anything is
fetched; a record it answers true for is left out, and "found" counts only what
is left. An upgrade passes one so that only the frames the library lacks are
fetched. A source that cannot tell a record's time before fetching it says so
and fetches everything; ingest then finds the copies it already holds.
"""

from __future__ import annotations

import importlib

class ArchiveError(RuntimeError):
    """The archive could not be asked, or refused: the network, a server error, or a
    setting it needs (a JSOC email). The message says which. A bad query is a
    ValueError, not this."""


SOURCES = {
    "fido": "heliofits.core.sources.fido",  # VSO, JSOC and every client sunpy registers
    "jsoc": "heliofits.core.sources.jsoc",  # JSOC export by record set, through drms
}


def get(name: str):
    if name not in SOURCES:
        raise KeyError(f"unknown source {name!r} (known: {', '.join(sorted(SOURCES))})")
    return importlib.import_module(SOURCES[name])
