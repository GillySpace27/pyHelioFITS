"""Print one current file per dataset, to open each kind in Studio by hand.

    python tools/sample_files.py [--library DIR]

Reads the index read-only, so it runs while Heliad works.
"""

import argparse

from heliad.library import Library


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library")
    ns = ap.parse_args()
    with Library.open(ns.library, readonly=True) as lib:
        for d in lib.datasets():
            rows = lib.reader.execute(
                "SELECT relpath, date_obs FROM current_files WHERE dataset_id = ? "
                "ORDER BY date_obs LIMIT 1", (d["id"],)).fetchall()
            for r in rows:
                print(f"{d['label']}\t{r['date_obs']}\t{lib.abspath(r['relpath'])}")


if __name__ == "__main__":
    main()
