"""Pick one current file per instrument from a library, for the decode-parity fixtures.

    python tools/parity_sample.py [--library DIR] [--out manifest.json]

Reads the index read-only. For every dataset it opens the newest-first file's header, classes it
with heliofits_core.parity.instrument_of, and keeps the first file of each class (LASCO: one per
detector). Prints the picks and, with --out, writes them as a manifest of SHA-256 hashes (the
parity test finds files by hash; nothing is copied). Instruments with no file in the library are
listed as missing, never invented.
"""

import argparse
import json

from heliofits_core import fitsmeta, parity
from heliofits_core.library import Library


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library")
    ap.add_argument("--out")
    ns = ap.parse_args()
    picks: dict[str, dict] = {}
    with Library.open(ns.library, readonly=True) as lib:
        for d in lib.datasets():
            rows = lib.reader.execute(
                "SELECT relpath, date_obs, sha256, size FROM current_files WHERE dataset_id = ? "
                "ORDER BY date_obs DESC LIMIT 1", (d["id"],)).fetchall()
            for r in rows:
                path = lib.abspath(r["relpath"])
                header, _, _ = fitsmeta.read_headers(path)
                inst = parity.instrument_of(header)
                key = f"LASCO-{str(header.get('DETECTOR', '')).strip()}" if inst == "LASCO" else inst
                if inst in parity.INSTRUMENTS and key not in picks:
                    picks[key] = {"instrument": inst, "dataset": d["label"], "date_obs": r["date_obs"],
                                  "name": path.name, "sha256": r["sha256"], "size": r["size"],
                                  "planes": int(header.get("NAXIS3", 1))}
    for inst in parity.INSTRUMENTS:
        if not any(v["instrument"] == inst for v in picks.values()):
            print(f"missing: {inst} has no file in this library")
    for key, v in picks.items():
        print(f"{key}\t{v['dataset']}\t{v['date_obs']}\t{v['name']}\tplanes={v['planes']}")
    if ns.out:
        with open(ns.out, "w") as f:
            json.dump({"picks": picks}, f, indent=1)


if __name__ == "__main__":
    main()
