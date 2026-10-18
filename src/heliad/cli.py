"""The heliad command.

    heliad [--library DIR] init      create a library and make it the default
    heliad import PATH...            ingest FITS files or whole folders
    heliad stats | datasets | collisions
    heliad files [--dataset KEY] [--start T] [--end T]
    heliad versions OBSERVATION_ID
    heliad verify
    heliad view create NAME [--dataset KEY ...] [--start T] [--end T]
    heliad view build NAME | rebuild
    heliad search [--via fido|jsoc] --start T --end T [--instrument I] [--wavelength A] ...
    heliad get    [--via fido|jsoc] --start T --end T [...]   download and ingest
    heliad jobs [--status S]         download jobs, newest first
    heliad reindex [--dry-run]       apply the current header rules to every file
    heliad delete [--file ID] [--observation ID] [--dataset KEY] [--yes]
    heliad settings [KEY [VALUE] | --unset KEY]
    heliad serve                     JSON-RPC on stdin/stdout

Commands that only read open the library read-only, so they work while the
Mac app (or another core) holds it. --library never changes the default
library; init does.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config, jobs, views
from .library import Library
from .query import Query

READ_ONLY = {"stats", "datasets", "files", "verify", "collisions", "versions", "jobs"}
NO_LIBRARY = {"search", "settings"}


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
    p.add_argument("--jsoc-series", help="JSOC series, e.g. aia.lev1_euv_12s")
    p.add_argument("--via", dest="client", default="fido", choices=["fido", "jsoc"],
                   help="fido (VSO and every sunpy client; the default) or jsoc (drms export)")


def _query(ns) -> Query:
    keys = ("start", "end", "instrument", "wavelength", "wavelength_max", "source",
            "provider", "detector", "level", "physobs", "sample_s", "jsoc_series")
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
    sub.add_parser("collisions")
    p = sub.add_parser("versions")
    p.add_argument("observation_id", type=int)
    p = sub.add_parser("jobs")
    p.add_argument("--status", choices=["queued", "active", "done", "failed", "cancelled"])
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("reindex")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("delete")
    p.add_argument("--file", type=int, action="append", dest="file_ids", default=[])
    p.add_argument("--observation", type=int, action="append", dest="observation_ids", default=[])
    p.add_argument("--dataset", action="append", dest="dataset_keys", default=[])
    p.add_argument("--yes", action="store_true", help="delete; without it, only show what would go")
    p = sub.add_parser("settings")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.add_argument("--unset", action="store_true")
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
    if ns.cmd in NO_LIBRARY:
        return _no_library(ns)
    try:
        lib = Library.open(ns.library, readonly=ns.cmd in READ_ONLY)
    except FileNotFoundError as exc:
        print(f"heliad: {exc}", file=sys.stderr)
        return 2
    with lib:
        return _run(lib, ns)


def _no_library(ns) -> int:
    if ns.cmd == "search":
        from . import sources
        _print(sources.get(ns.client).search(_query(ns), settings=config.load_settings()))
    elif ns.cmd == "settings":
        if ns.unset:
            _print(config.save_setting(ns.key, None))
        elif ns.key and ns.value is not None:
            _print(config.save_setting(ns.key, ns.value))
        elif ns.key:
            print(config.load_settings().get(ns.key, ""))
        else:
            _print(config.load_settings())
    return 0


def _run(lib: Library, ns) -> int:
    if ns.cmd == "init":
        path = lib.make_default()
        _print({"config": str(path), **lib.stats(),
                "location_warnings": config.location_warnings(lib.root)})
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
        _print({**lib.stats(), "location_warnings": config.location_warnings(lib.root)})
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
    elif ns.cmd == "collisions":
        _print(lib.collisions())
    elif ns.cmd == "versions":
        _print(lib.versions(ns.observation_id))
    elif ns.cmd == "jobs":
        _print(jobs.list_jobs(lib, status=ns.status, limit=ns.limit))
    elif ns.cmd == "reindex":
        def progress(i, n):
            print(f"  read {i}/{n}", file=sys.stderr)
        _print(lib.reindex(dry_run=ns.dry_run, progress=progress))
    elif ns.cmd == "delete":
        result = lib.delete(file_ids=ns.file_ids, observation_ids=ns.observation_ids,
                            dataset_keys=ns.dataset_keys, dry_run=not ns.yes)
        _print(result)
        if not ns.yes and result["files"]:
            print("Nothing deleted. Run again with --yes to delete these.", file=sys.stderr)
    elif ns.cmd == "view":
        if ns.view_cmd == "create":
            _print(views.create(lib, ns.name, description=ns.description,
                                dataset_keys=ns.datasets, start=ns.start, end=ns.end))
            _print(views.materialize(lib, ns.name))
        elif ns.view_cmd == "build":
            _print(views.materialize(lib, ns.name))
        else:
            _print(views.rebuild_all(lib))
    elif ns.cmd == "get":
        def notify(method, params):
            print(f"  {method} {json.dumps(params, default=str)}", file=sys.stderr)
        runner = jobs.JobRunner(lib, notify=notify)
        job = runner.enqueue(_query(ns), ns.client)
        print(f"job {job['job_id']} queued", file=sys.stderr)
        finished = runner.run_pending()
        _print(finished)
        return 0 if all(j["status"] == "done" for j in finished) else 1
    elif ns.cmd == "serve":
        from .rpc import Server
        Server(lib).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
