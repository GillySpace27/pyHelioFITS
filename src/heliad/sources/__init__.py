"""Archive sources. Each one turns a Query into records and records into files.

A source is a module with

    search(query, *, settings) -> dict
        what the archive holds, for display; downloads nothing
    fetch(query, staging, *, settings, phase) -> dict
        fetch into the staging folder; returns {"found", "paths", "errors", "urls"}

phase(name, done=None, total=None) reports progress and is where a cancel
takes effect, so a source calls it between steps. Files already complete in
staging are kept, so fetching again after an interruption resumes.
"""

from __future__ import annotations

import importlib

SOURCES = {
    "fido": "heliad.sources.fido",  # VSO, JSOC and every client sunpy registers
    "jsoc": "heliad.sources.jsoc",  # JSOC export by record set, through drms
}


def get(name: str):
    if name not in SOURCES:
        raise KeyError(f"unknown source {name!r} (known: {', '.join(sorted(SOURCES))})")
    return importlib.import_module(SOURCES[name])
