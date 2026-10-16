"""The heliad command.

    heliad [--library DIR] init
    heliad import PATH...            ingest FITS files or whole folders
    heliad stats | datasets
    heliad files [--dataset KEY] [--start T] [--end T]
    heliad verify
    heliad view create NAME [--dataset KEY ...] [--start T] [--end T]
    heliad view build NAME | rebuild
    heliad search --start T --end T [--instrument I] [--wavelength A] ...
    heliad get    --start T --end T [...]    search, download and ingest
    heliad serve                     JSON-RPC on stdin/stdout
"""

from __future__ import annotations

import argparse
import json
import sys

from . import views
from .library import Library
from .query import Query


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _query_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--instrument")
    p.add_argument("--wavelength", type=float, help="Angstrom")
    p.add_argument("--wavelength-max", type=float)
    p.add_argument("--source")
    p.add_argument("--provider")
    p.add_argument("--detector")
    p.add_argument("--level")
    p.add_argument("--physobs")
    p.add_argument("--sample", type=float, dest="sample_s", help="cadence in seconds")


def _query(ns) -> Query:
    keys = ("start", "end", "instrument", "wavelength", "wavelength_max", "source",
            "provider", "detector", "level", "physobs", "sample_s")
    return Query(**{k: getattr(ns, k) for k in keys})


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="heliad", description="Find solar data, download it and keep it in one library.")
    ap.add_argument("--library", help="library root (default: config file, then ~/HeliadLibrary)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("import")
    p.add_argument("paths", nargs="+")
    sub.add_parser("stats")
    sub.add_parser("datasets")
    p = sub.add_parser("files")
    p.add_argument("--dataset")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--limit", type=int, default=200)
    sub.add_parser("verify")
    v = sub.add_parser("view").add_subparsers(dest="view_cmd", required=True)
    p = v.add_parser("create")
    p.add_argument("name")
    p.add_argument("--dataset", action="append", dest="datasets")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--description")
    p = v.add_parser("build")
    p.add_argument("name")
    v.add_parser("rebuild")
    _query_args(sub.add_parser("search"))
    _query_args(sub.add_parser("get"))
    sub.add_parser("serve")
    return ap


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    with Library.open(ns.library) as lib:
        if ns.cmd == "init":
            _print(lib.stats())
        elif ns.cmd == "import":
            from pathlib import Path
            for raw in ns.paths:
                path = Path(raw)
                if path.is_dir():
                    def progress(i, n, p):
                        if i == n or i % 100 == 0:
                            print(f"  {i}/{n}", file=sys.stderr)
                    _print(lib.import_tree(path, progress=progress))
                else:
                    _print(lib.ingest(path).to_dict())
        elif ns.cmd == "stats":
            _print(lib.stats())
        elif ns.cmd == "datasets":
            for d in lib.datasets():
                print(f"{d['observations']:>7}  {d['first'] or '':<23}  {d['last'] or '':<23}  "
                      f"{d['bytes'] / 1e9:8.2f} GB  {d['label']}   [{d['dataset_key']}]")
        elif ns.cmd == "files":
            _print(lib.current_files(dataset_key=ns.dataset, start=ns.start, end=ns.end, limit=ns.limit))
        elif ns.cmd == "verify":
            result = lib.verify()
            _print(result)
            return 1 if result["missing"] or result["altered"] else 0
        elif ns.cmd == "view":
            if ns.view_cmd == "create":
                _print(views.create(lib, ns.name, description=ns.description,
                                    dataset_keys=ns.datasets, start=ns.start, end=ns.end))
                _print(views.materialize(lib, ns.name))
            elif ns.view_cmd == "build":
                _print(views.materialize(lib, ns.name))
            else:
                _print(views.rebuild_all(lib))
        elif ns.cmd == "search":
            from .sources import fido
            _print(fido.search(_query(ns)))
        elif ns.cmd == "get":
            from .sources import fido
            _print(fido.download(lib, _query(ns)))
        elif ns.cmd == "serve":
            from .rpc import Server
            Server(lib).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
