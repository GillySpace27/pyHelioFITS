"""The hfcore command (heliad, its old name, runs it too).

    hfcore [--library DIR] [--json] COMMAND ...

    hfcore init                      create a library and make it the default
    hfcore import PATH...            ingest FITS files or whole folders
    hfcore stats | datasets | collisions
    hfcore files [--dataset KEY] [--start T] [--end T]
    hfcore versions OBSERVATION_ID
    hfcore checks FILE_ID            the header checks recorded for one file
    hfcore coverage [--start T] [--end T] [--bins N] [--dataset KEY ...]
    hfcore thumb FILE_ID [--size N]  path of a PNG preview, made on first request
    hfcore verify
    hfcore view create NAME [--dataset KEY ...] [--start T] [--end T]
    hfcore view build NAME | rebuild
    hfcore search [--via fido|jsoc] --start T --end T [--instrument I] [--wavelength A] ...
    hfcore get    [--via fido|jsoc] --start T --end T [...]   download and ingest
    hfcore jobs [--status S]         download jobs, newest first
    hfcore reject OBSERVATION_ID... --reason TEXT   set bad frames aside (readers stop seeing them)
    hfcore unreject OBSERVATION_ID...               serve them again
    hfcore rejected [--dataset KEY]                 what is set aside, and why
    hfcore refetch OBSERVATION_ID                   ask the archive again for one frame
    hfcore cadences DATASET_KEY [--start T] [--end T]   the archive's cadence against the library's
    hfcore upgrade DATASET_KEY [--sample S] [--start T] [--end T] [--yes]
                                     fetch only the frames a dataset lacks (a dry run without --yes)
    hfcore layer-upgrade HEADERS.json [--sample S] [--yes]
                                     FITS for a layer of pictures, from their headers (a dry run without --yes)
    hfcore reindex [--dry-run]       apply the current header rules to every file
    hfcore fix-extensions [--dry-run]  add .fits / .fits.gz to stored files without one
    hfcore delete [--file ID] [--observation ID] [--dataset KEY] [--yes]
    hfcore settings [KEY [VALUE] | --unset KEY]
    hfcore serve                     JSON-RPC on stdin/stdout; relays to the
                                     library's writer if another core is one
    hfcore api [METHOD]              the JSON-RPC methods serve answers (docs/api.md)

Output: a table or a few lines for a person; with --json, exactly the JSON-RPC
result the same operation returns (docs/api.md), one document per command.

Exit codes: 0 done; 1 the operation ran and reported a problem (verify found
missing or altered files, a download ended partial or failed, an import had
errors, the archive could not be asked); 2 bad arguments, an id or key the
library does not have, or no library where one was expected; 3 the library is
locked by another core that is not serving it.

Commands that only read open the library read-only, so they work while the
Mac app (or another core) holds it. --library never changes the default
library; init does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, config, coverage, jobs, layers, upgrade, views
from .library import Library, LibraryLocked
from .query import Query
from .sources import ArchiveError

READ_ONLY = {"stats", "datasets", "files", "verify", "collisions", "versions", "jobs", "thumb",
             "checks", "coverage", "rejected", "cadences"}
NO_LIBRARY = {"search", "settings", "api"}

OK, PROBLEM, USAGE, LOCKED = 0, 1, 2, 3


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
    ap = argparse.ArgumentParser(
        prog="hfcore", description="Find solar data, download it and keep it in one library.",
        epilog="Programs talk to HelioFITS Core through `hfcore serve` (JSON-RPC over stdin/stdout): "
               "`hfcore api` lists its methods, and docs/api.md in the HelioFITS Core repository is the full reference.")
    ap.add_argument("--library", help="library root (default: config file, then ~/HelioFITSLibrary)")
    ap.add_argument("--json", action="store_true", help="print the JSON-RPC result instead of a table")
    ap.add_argument("--version", action="version", version=f"hfcore {__version__}")
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
    p = sub.add_parser("checks", help="the header checks recorded for one file")
    p.add_argument("file_id", type=int)
    p = sub.add_parser("coverage", help="per dataset, how many observations fall in each bin of a time range")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--bins", type=int, default=24)
    p.add_argument("--dataset", action="append", dest="dataset_keys")
    p.add_argument("--instrument")
    p.add_argument("--wavelength", type=float, help="Angstrom")
    p.add_argument("--wavelength-max", type=float)
    p = sub.add_parser("reject", help="set observations aside: readers stop seeing them")
    p.add_argument("observation_ids", type=int, nargs="+", metavar="OBSERVATION_ID")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("unreject", help="serve rejected observations again")
    p.add_argument("observation_ids", type=int, nargs="+", metavar="OBSERVATION_ID")
    p = sub.add_parser("rejected", help="rejected observations with their reason")
    p.add_argument("--dataset")
    p = sub.add_parser("refetch", help="ask the archive again for one observation's frame")
    p.add_argument("observation_id", type=int)
    p = sub.add_parser("cadences", help="compare a dataset's cadence with the archive's")
    p.add_argument("dataset_key")
    p.add_argument("--start")
    p.add_argument("--end")
    p = sub.add_parser("upgrade", help="fetch only the frames a dataset lacks; a dry run without --yes")
    p.add_argument("dataset_key")
    p.add_argument("--sample", type=float, dest="sample_s", help="target cadence in seconds")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--yes", action="store_true", help="queue and run the download; without it, only show the plan")
    p = sub.add_parser("layer-upgrade",
                       help="FITS for a layer of pictures (Helioviewer frames), from a JSON list of their headers")
    p.add_argument("headers", type=Path, help="a JSON file: a list of FITS-keyword objects, one per frame")
    p.add_argument("--sample", type=float, dest="sample_s", help="cadence in seconds; default the frames' own")
    p.add_argument("--yes", action="store_true", help="queue and run the downloads; without it, only show the plan")
    p = sub.add_parser("thumb")
    p.add_argument("file_id", type=int)
    p.add_argument("--size", type=int, default=256)
    p = sub.add_parser("jobs")
    p.add_argument("--status", choices=["queued", "active", "done", "partial", "failed", "cancelled"])
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("reindex")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("fix-extensions", help="add .fits or .fits.gz to stored files that have no FITS suffix")
    p.add_argument("--dry-run", action="store_true", help="count what would be renamed; change nothing")
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
    p = sub.add_parser("api", help="the JSON-RPC methods that serve answers")
    p.add_argument("method", nargs="?")
    return ap


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    out = Output(ns.json)
    if ns.cmd in NO_LIBRARY:
        return _no_library(ns, out)
    if ns.cmd == "serve":
        return _serve(ns)
    try:
        lib = Library.open(ns.library, readonly=ns.cmd in READ_ONLY)
    except FileNotFoundError as exc:
        return _fail(USAGE, str(exc))
    except LibraryLocked as exc:
        return _fail(LOCKED, str(exc))
    with lib:
        try:
            return _run(lib, ns, out)
        except KeyError as exc:  # the core's "no such dataset / observation / file / job"
            return _fail(USAGE, str(exc.args[0]) if exc.args else "not found")
        except ValueError as exc:
            return _fail(USAGE, str(exc))
        except ArchiveError as exc:
            return _fail(PROBLEM, str(exc))


def _fail(code: int, message: str) -> int:
    print(f"hfcore: {message}", file=sys.stderr)
    return code


def _serve(ns) -> int:
    from . import share
    from .rpc import Server
    if os.name == "nt":
        # JSON-RPC lines are UTF-8 on every platform; Windows pipes default to the
        # locale's code page, which would misread a client's non-ASCII text.
        for stream in (sys.stdin, sys.stdout):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    try:
        lib = Library.open(ns.library)
    except LibraryLocked:
        try:
            return share.relay(config.load(ns.library).library_root)
        except share.WriterUnavailable as exc:
            return _fail(LOCKED, str(exc))
    with lib:
        Server(lib).serve(shared=True)
    return OK


def _no_library(ns, out: "Output") -> int:
    if ns.cmd == "api":
        from .rpc import describe
        try:
            sys.stdout.write(describe(ns.method))
        except KeyError:
            return _fail(USAGE, f"no method {ns.method!r} (hfcore api lists them)")
    elif ns.cmd == "search":
        from . import sources
        out.show(sources.get(ns.client).search(_query(ns), settings=config.load_settings()), _search_text)
    elif ns.cmd == "settings":
        if ns.unset:
            out.show(config.save_setting(ns.key, None), _settings_text)
        elif ns.key and ns.value is not None:
            out.show(config.save_setting(ns.key, ns.value), _settings_text)
        elif ns.key:
            value = config.load_settings().get(ns.key, "")
            out.show(value, lambda v: str(v))
        else:
            out.show(config.load_settings(), _settings_text)
    return OK


def _run(lib: Library, ns, out: "Output") -> int:
    if ns.cmd == "init":
        path = lib.make_default()
        out.show({"config": str(path), **lib.stats(),
                  "location_warnings": config.location_warnings(lib.root)}, _stats_text)
    elif ns.cmd == "import":
        results = []
        for raw in ns.paths:
            path = Path(raw)
            if path.is_dir():
                def progress(i, n, p):
                    if i == n or i % 100 == 0:
                        print(f"  {i}/{n}", file=sys.stderr)
                results.append({"path": str(path), **lib.import_tree(path, progress=progress)})
            else:
                results.append({"path": str(path), **lib.ingest(path).to_dict()})
        out.show(results, _import_text)
        if any(r.get("errors") for r in results):
            return PROBLEM
    elif ns.cmd == "stats":
        out.show({**lib.stats(), "location_warnings": config.location_warnings(lib.root)}, _stats_text)
    elif ns.cmd == "datasets":
        out.show(lib.datasets(), _datasets_text)
    elif ns.cmd == "files":
        out.show(lib.current_files(dataset_key=ns.dataset, start=ns.start, end=ns.end, limit=ns.limit),
                 _files_text)
    elif ns.cmd == "verify":
        result = lib.verify()
        out.show(result, _verify_text)
        return PROBLEM if result["missing"] or result["altered"] else OK
    elif ns.cmd == "collisions":
        out.show(lib.collisions(), _collisions_text)
    elif ns.cmd == "versions":
        out.show(lib.versions(ns.observation_id), _versions_text)
    elif ns.cmd == "checks":
        out.show(lib.checks_for(ns.file_id), _checks_text)
    elif ns.cmd == "coverage":
        out.show(coverage.library(lib, start=ns.start, end=ns.end, bins=ns.bins, dataset_keys=ns.dataset_keys,
                                  instrument=ns.instrument, wavelength=ns.wavelength,
                                  wavelength_max=ns.wavelength_max), _coverage_text)
    elif ns.cmd == "reject":
        out.show(lib.reject(ns.observation_ids, ns.reason), _reject_text)
    elif ns.cmd == "unreject":
        out.show(lib.unreject(ns.observation_ids), _reject_text)
    elif ns.cmd == "rejected":
        out.show(lib.rejected(ns.dataset), _rejected_text)
    elif ns.cmd == "refetch":
        runner = _runner(lib)
        job = upgrade.refetch(lib, runner, ns.observation_id)
        return _run_job(runner, job, out)
    elif ns.cmd == "cadences":
        from . import sources
        out.show(upgrade.cadences(lib, sources.get, ns.dataset_key, ns.start, ns.end,
                                  settings=config.load_settings()), _cadences_text)
    elif ns.cmd == "upgrade":
        runner = _runner(lib) if ns.yes else None
        result = upgrade.upgrade(lib, runner, ns.dataset_key, sample_s=ns.sample_s, start=ns.start,
                                 end=ns.end, dry_run=not ns.yes)
        if not ns.yes:
            out.show(result, _upgrade_plan_text)
            print("Nothing queued. Run again with --yes to fetch these.", file=sys.stderr)
            return OK
        return _run_job(runner, result, out)
    elif ns.cmd == "layer-upgrade":
        try:
            headers = json.loads(ns.headers.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read {ns.headers}: {exc}") from exc
        runner = _runner(lib) if ns.yes else None
        result = layers.upgrade_layer(lib, runner, headers, sample_s=ns.sample_s, dry_run=not ns.yes)
        if not ns.yes or not any(g.get("job") for g in result["groups"]):
            out.show(result, _layer_plan_text)
            if not ns.yes and any(g["fetch"] for g in result["groups"]):
                print("Nothing queued. Run again with --yes to fetch these.", file=sys.stderr)
            return OK
        finished = runner.run_pending()
        out.show(finished, _jobs_text)
        return OK if all(j["status"] == "done" for j in finished) else PROBLEM
    elif ns.cmd == "thumb":
        print(lib.thumbnail(ns.file_id, ns.size))
    elif ns.cmd == "jobs":
        out.show(jobs.list_jobs(lib, status=ns.status, limit=ns.limit), _jobs_text)
    elif ns.cmd == "reindex":
        def progress(i, n):
            print(f"  read {i}/{n}", file=sys.stderr)
        out.show(lib.reindex(dry_run=ns.dry_run, progress=progress), _reindex_text)
    elif ns.cmd == "fix-extensions":
        def progress(i, n):
            print(f"  checked {i}/{n}", file=sys.stderr)
        out.show(lib.fix_extensions(dry_run=ns.dry_run, progress=progress), _fix_extensions_text)
    elif ns.cmd == "delete":
        result = lib.delete(file_ids=ns.file_ids, observation_ids=ns.observation_ids,
                            dataset_keys=ns.dataset_keys, dry_run=not ns.yes)
        out.show(result, _delete_text)
        if not ns.yes and result["files"]:
            print("Nothing deleted. Run again with --yes to delete these.", file=sys.stderr)
    elif ns.cmd == "view":
        if ns.view_cmd == "create":
            created = views.create(lib, ns.name, description=ns.description,
                                   dataset_keys=ns.datasets, start=ns.start, end=ns.end)
            built = views.materialize(lib, ns.name)
            out.show({"created": created, "built": built},
                     lambda r: _view_text(r["created"]) + "\n" + _view_text(r["built"]))
        elif ns.view_cmd == "build":
            out.show(views.materialize(lib, ns.name), _view_text)
        else:
            out.show(views.rebuild_all(lib), lambda rows: "\n".join(_view_text(r) for r in rows) or "No views.")
    elif ns.cmd == "get":
        runner = _runner(lib)
        return _run_job(runner, runner.enqueue(_query(ns), ns.client), out)
    return OK


def _runner(lib: Library) -> jobs.JobRunner:
    """A job runner that prints each notification, for commands that download in the foreground."""
    def notify(method, params):
        print(f"  {method} {json.dumps(params, default=str)}", file=sys.stderr)
    return jobs.JobRunner(lib, notify=notify)


def _run_job(runner: jobs.JobRunner, job: dict, out: "Output") -> int:
    """Run every queued job to the end and print them; exit 1 unless all ended done."""
    print(f"job {job['job_id']} queued", file=sys.stderr)
    finished = runner.run_pending()
    out.show(finished, _jobs_text)
    return OK if all(j["status"] == "done" for j in finished) else PROBLEM


# ---- output ------------------------------------------------------------------

class Output:
    """Print a result as JSON (--json) or as the text a person reads."""

    def __init__(self, as_json: bool):
        self.as_json = as_json

    def show(self, result, text) -> None:
        if self.as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(text(result))


def _table(rows: list[dict], columns: list[tuple[str, str]], right: set[str] = frozenset()) -> str:
    """Rows as aligned columns. columns = [(key, heading)]; keys in `right` are right-aligned."""
    if not rows:
        return ""
    cells = [[_cell(r.get(k)) for k, _ in columns] for r in rows]
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, (_, h) in enumerate(columns)]
    def line(values):
        parts = []
        for i, (k, _) in enumerate(columns):
            parts.append(values[i].rjust(widths[i]) if k in right else values[i].ljust(widths[i]))
        return "  ".join(parts).rstrip()
    return "\n".join([line([h for _, h in columns]), *(line(c) for c in cells)])


def _cell(value) -> str:
    if value is None or value is False:
        return ""
    if value is True:
        return "yes"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1000
    return f"{n:.2f} TB"


def _stats_text(s: dict) -> str:
    lines = []
    if "config" in s:
        lines.append(f"Config written: {s['config']}")
    lines += [
        f"Library: {s['library_root']}",
        f"Datasets: {s['datasets']}   Observations: {s['observations']}   Files: {s['files']}   "
        f"Size: {_bytes(s['bytes'])}",
        f"Warnings: {s['warnings']}   Collisions: {s['collisions']}   Schema: {s['schema_version']}   "
        f"Rules: {s['rules_version']}" + ("   Reindex due: run hfcore reindex" if s["reindex_due"] else ""),
    ]
    for w in s.get("location_warnings") or []:
        lines.append(f"Warning: {w}")
    return "\n".join(lines)


def _datasets_text(rows: list[dict]) -> str:
    if not rows:
        return "No datasets."
    shaped = [{**r, "size": _bytes(r["bytes"]), "first": r["first"] or "", "last": r["last"] or ""} for r in rows]
    return _table(shaped, [("observations", "Obs"), ("first", "First (UTC)"), ("last", "Last (UTC)"),
                           ("size", "Size"), ("label", "Dataset"), ("dataset_key", "Key")],
                  right={"observations", "size"})


def _files_text(rows: list[dict]) -> str:
    if not rows:
        return "No files."
    shaped = [{**r, "size": _bytes(r["size"])} for r in rows]
    return _table(shaped, [("file_id", "File"), ("date_obs", "Observed (UTC)"), ("version", "Version"),
                           ("size", "Size"), ("label", "Dataset"), ("relpath", "Path")],
                  right={"file_id", "size"})


def _versions_text(rows: list[dict]) -> str:
    if not rows:
        return "No files for this observation."
    shaped = [{**r, "size": _bytes(r["size"]), "current": "current" if r["current"] else ""} for r in rows]
    return _table(shaped, [("file_id", "File"), ("version", "Version"), ("current", ""), ("size", "Size"),
                           ("ingested_at", "Ingested"), ("relpath", "Path")], right={"file_id", "size"})


def _checks_text(rows: list[dict]) -> str:
    if not rows:
        return "No checks recorded for this file."
    return _table(rows, [("severity", "Severity"), ("code", "Code"), ("message", "Message")])


_BARS = " ▁▂▃▄▅▆▇█"


def _sparkline(counts: list[int]) -> str:
    top = max(counts) if counts else 0
    if not top:
        return " " * len(counts)
    return "".join(_BARS[-1] if c == top else _BARS[1 + c * (len(_BARS) - 2) // top] if c else _BARS[0]
                   for c in counts)


def _coverage_text(r: dict) -> str:
    head = f"{r['start']} to {r['end']} UTC in {r['bins']} bins of {r['bin_seconds']:g} s"
    if not r["datasets"]:
        return head + "\nNo observations in this range."
    shaped = [{**d, "bins": _sparkline(d["counts"]), "first": d["first"] or "", "last": d["last"] or ""}
              for d in r["datasets"]]
    return head + "\n" + _table(shaped, [("frames", "Frames"), ("bins", "Per bin"), ("first", "First (UTC)"),
                                         ("last", "Last (UTC)"), ("label", "Dataset"), ("dataset_key", "Key")],
                                 right={"frames"})


def _reject_text(r: dict) -> str:
    verb, n = ("Rejected", r["rejected"]) if "rejected" in r else ("Unrejected", r["unrejected"])
    line = f"{verb} {n} observations: {', '.join(str(i) for i in r['observation_ids'])}"
    if r.get("views_rebuilt"):
        line += "\nViews rebuilt: " + ", ".join(r["views_rebuilt"])
    return line


def _rejected_text(rows: list[dict]) -> str:
    if not rows:
        return "Nothing rejected."
    return _table(rows, [("observation_id", "Observation"), ("date_obs", "Observed (UTC)"), ("label", "Dataset"),
                         ("reason", "Reason"), ("rejected_at", "Rejected"), ("file_id", "File")],
                  right={"observation_id", "file_id"})


def _query_line(q: dict) -> str:
    fields = [(k, v) for k, v in q.items() if v not in (None, {}, "") and k not in ("start", "end")]
    what = " ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in fields)
    return f"{q.get('start', '?')} to {q.get('end', '?')} UTC" + (f"  {what}" if what else "")


def _cadences_text(r: dict) -> str:
    lib_, arc = r["library"], r["archive"]
    def spacing(s):
        return f"every {s:g} s" if s else "one frame"
    lines = [f"Dataset {r['dataset_key']} via {r['source']}: {_query_line(r['query'])}",
             f"Library: {lib_['count']} observations, {spacing(lib_['median_spacing_s'])}"
             + (f" ({lib_['rejected']} rejected)" if lib_["rejected"] else ""),
             f"Archive: {arc['count']} records, {spacing(arc['median_spacing_s'])}",
             f"Missing from the library (within {r['tolerance_s']:g} s): {len(r['missing'])}"]
    shown = r["missing"][:20]
    lines += [f"  {t}" for t in shown]
    if len(r["missing"]) > len(shown):
        lines.append(f"  ... {len(r['missing']) - len(shown)} more (--json for every one)")
    return "\n".join(lines)


def _upgrade_plan_text(r: dict) -> str:
    o = r["options"]
    lines = [f"Would ask {r['source']} for {o['dataset_key']}: {_query_line(r['query'])}",
             f"Only frames the library lacks (none within {o['tolerance_s']:g} s of one it has).",
             f"Query: {r['basis']}"]
    return "\n".join(lines)


def _layer_plan_text(r: dict) -> str:
    c = r["counts"]
    lines = [f"{len(r['frames'])} frames: {c['in_library']} in the library, {c['missing']} missing, "
             f"{c['rejected']} rejected, {c['unreadable']} unreadable."]
    for g in r["groups"]:
        gc = g["counts"]
        lines.append(f"  {g['label']}: {g['count']} frames, {gc['in_library']} in the library, "
                     f"{gc['missing']} missing (within {g['tolerance_s']:g} s)")
        f = g["fetch"]
        if f:
            lines.append(f"    would ask {f['source']}: {_query_line(f['query'])}")
            lines.append(f"    query: {f['basis']}")
    for fr in r["frames"]:
        if fr["status"] == "unreadable":
            lines.append(f"  frame {fr['frame']}: {fr['reason']}")
    return "\n".join(lines)


def _collisions_text(rows: list[dict]) -> str:
    if not rows:
        return "No collisions."
    return _table(rows, [("observation_id", "Observation"), ("label", "Dataset"), ("date_obs", "Observed (UTC)"),
                         ("files", "Files")], right={"observation_id", "files"})


def _verify_text(r: dict) -> str:
    lines = [f"Verified {r['ok']} files; {len(r['missing'])} missing, {len(r['altered'])} altered."]
    lines += [f"  missing: {p}" for p in r["missing"]]
    lines += [f"  altered: {p}" for p in r["altered"]]
    return "\n".join(lines)


def _job_line(j: dict) -> dict:
    q = j["query"]
    what = " ".join(str(v) for v in (q.get("instrument"), q.get("jsoc_series"),
                                     f"{q['wavelength']:g} Å" if q.get("wavelength") else None) if v)
    progress = j["phase"] or ""
    if j["status"] == "active" and j.get("files_total"):
        progress = f"{j['phase']} {j['files_done']}/{j['files_total']}"
    elif j.get("result"):
        res = j["result"]
        progress = f"stored {res.get('stored', 0)} of {res.get('found') if res.get('found') is not None else '?'}"
    return {"job_id": j["job_id"], "status": j["status"], "source": j["source"], "progress": progress,
            "what": what, "range": f"{q.get('start', '?')} to {q.get('end', '?')}", "created_at": j["created_at"],
            "error": j.get("error") or ""}


def _jobs_text(rows: list[dict]) -> str:
    if not rows:
        return "No jobs."
    return _table([_job_line(j) for j in rows],
                  [("job_id", "Job"), ("status", "Status"), ("source", "Via"), ("progress", "Progress"),
                   ("what", "What"), ("range", "Range (UTC)"), ("created_at", "Queued"), ("error", "Error")],
                  right={"job_id"})


def _import_text(results: list[dict]) -> str:
    lines = []
    for r in results:
        if "counts" in r:
            counts = ", ".join(f"{n} {k}" for k, n in sorted(r["counts"].items()))
            lines.append(f"{r['path']}: {r['files']} files; {counts or 'nothing'}")
            lines += [f"  {e['path']}: {e['error']}" for e in r.get("errors", [])]
        else:
            lines.append(f"{r['path']}: {r['status']} as file {r['file_id']} ({r['relpath']})")
            lines += [f"  {f['severity']}: {f['message']}" for f in r.get("findings", [])]
    return "\n".join(lines)


def _fix_extensions_text(r: dict) -> str:
    done = r["fits"] + r["fits_gz"] if r.get("dry_run") else r["renamed"]
    verb = "would rename" if r.get("dry_run") else "renamed"
    return (f"{r['candidates']} stored files without a FITS suffix: {verb} {done} "
            f"({r['fits']} .fits, {r['fits_gz']} .fits.gz); not FITS {r['not_fits']}, "
            f"missing {r['missing']}, open elsewhere {r['open']}, failed {len(r['failed'])}")


def _reindex_text(r: dict) -> str:
    head = "Dry run: " if r["dry_run"] else ""
    lines = [f"{head}{r['files']} files; datasets {r['datasets_before']} -> {r['datasets_after']}, "
             f"observations {r['observations_before']} -> {r['observations_after']}, "
             f"{r['files_moved']} files moved, {r['current_changed']} current versions changed."]
    if r.get("views_rebuilt"):
        lines.append("Views rebuilt: " + ", ".join(r["views_rebuilt"]))
    return "\n".join(lines)


def _delete_text(r: dict) -> str:
    verb = "Would remove" if r["dry_run"] else "Removed"
    line = (f"{verb} {r['files']} files ({_bytes(r['bytes_released'])}), {r['observations']} observations, "
            f"{r['datasets']} datasets; {r['repointed']} observations fall back to an older version.")
    lines = [line]
    lines += [f"  could not remove {f['relpath']}: {f['error']}" for f in r.get("unlink_failed", [])]
    if r.get("views_rebuilt"):
        lines.append("Views rebuilt: " + ", ".join(r["views_rebuilt"]))
    return "\n".join(lines)


def _view_text(r: dict) -> str:
    if "path" in r:
        modes = ", ".join(f"{n} {m}" for m, n in sorted(r.get("link_modes", {}).items()))
        return f"View {r['name']}: {r['files']} files linked at {r['path']}" + (f" ({modes})" if modes else "")
    return f"View {r['name']} (id {r['view_id']}): {r['observations']} observations"


def _settings_text(data: dict) -> str:
    if not data:
        return "No settings."
    return "\n".join(f"{k} = {v}" for k, v in sorted(data.items()))


_SEARCH_COLUMNS = ["Start Time", "Instrument", "Wavelength", "Provider", "Source", "Level", "Size",
                   "T_REC", "WAVELNTH", "QUALITY"]


def _search_text(r: dict, shown: int = 20) -> str:
    lines = [f"{r['total']} files found"]
    if r.get("recordset"):
        lines.append(f"Record set: {r['recordset']}")
    providers = r.get("providers") or [{"client": "JSOC export", "count": r["total"],
                                        "columns": sorted(r["rows"][0]) if r.get("rows") else [],
                                        "rows": r.get("rows", [])}]
    for p in providers:
        lines.append(f"\n{p.get('client') or 'archive'}: {p['count']}")
        columns = [c for c in _SEARCH_COLUMNS if c in p["columns"]] or p["columns"][:6]
        table = _table(p["rows"][:shown], [(c, c) for c in columns])
        if table:
            lines.append(table)
        if len(p["rows"]) > shown:
            lines.append(f"  ... {len(p['rows']) - shown} more (--json for every row)")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
