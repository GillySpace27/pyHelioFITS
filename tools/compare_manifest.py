"""Check an old hand-made manifest.csv (name,path,query,sha256) against a Heliad index.

    python tools/compare_manifest.py EVENT_DIR/manifest.csv LIBRARY_ROOT

Prints how many manifest rows are in the index by checksum, and lists any that
are not. Read-only on both sides.
"""

import csv
import sqlite3
import sys
from pathlib import Path

csv.field_size_limit(10_000_000)


def main(manifest: str, library: str) -> int:
    db = sqlite3.connect(f"file:{Path(library) / 'heliad.sqlite'}?mode=ro", uri=True)
    known = {row[0] for row in db.execute("SELECT sha256 FROM files")}
    rows = list(csv.DictReader(open(manifest, newline="")))
    missing = [r for r in rows if r.get("sha256") and r["sha256"] not in known]
    no_sha = [r for r in rows if not r.get("sha256")]
    print(f"manifest rows: {len(rows)}  found by sha256: {len(rows) - len(missing) - len(no_sha)}  "
          f"missing: {len(missing)}  rows without sha256: {len(no_sha)}")
    for r in missing[:50]:
        print("  missing:", r.get("path"))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
