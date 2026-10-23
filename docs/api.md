# Heliad API

Everything the Heliad app and HelioFITS Studio do with a library goes through one interface:
`heliad serve`, which speaks JSON-RPC 2.0 over its stdin and stdout. Scripts can use it the same
way. `heliad api` prints the list below from the installed core, and `heliad api METHOD` prints
one entry; this page is the full reference.

If you only need to read a library, you do not need the API: open the index read-only as
[the reader contract](reader-contract.md) describes. Anything that changes the library (ingest,
download, delete, reject, views, reindex) goes through a core, because the core is the library's only
writer.

- [Talking to a core](#talking-to-a-core)
- [Several clients on one library](#several-clients-on-one-library)
- [Queries](#queries)
- [Library methods](#library-methods)
- [Ingest methods](#ingest-methods)
- [Download and search methods](#download-and-search-methods)
- [Cache editor methods](#cache-editor-methods)
- [View methods](#view-methods)
- [Settings methods](#settings-methods)
- [Notifications](#notifications)
- [Errors](#errors)
- [The command line and the API](#the-command-line-and-the-api)

The examples come from a scratch library (paths shortened, long results cut with `...`).

## Talking to a core

Start a core on a library:

    heliad --library ~/HeliadLibrary serve

Without `--library` it serves the default library (the one `heliad init` chose). Then write one
request per line to its stdin:

    {"jsonrpc": "2.0", "id": 1, "method": "library.stats"}

and read one line per reply from its stdout:

    {"jsonrpc":"2.0","id":1,"result":{"library_root":"/Users/you/HeliadLibrary","schema_version":4,"rules_version":3,"reindex_due":false,"datasets":2,"observations":2,"files":3,"bytes":17280,"warnings":0,"collisions":0}}

- **Params are named**: `"params": {"file_id": 1}`. Leave `params` out for a method that takes none.
- **Match replies by `id`.** Most methods answer in order. `search`, `library.thumbnail` and
  `dataset.cadences` run in the background and answer when done, so their replies can overtake
  others.
- **Notifications** are lines with a `method` and no `id` (download and ingest progress). Ignore
  the ones you do not need.
- **Ending**: close the core's stdin. It answers what it was already asked, then exits.

From a shell:

    echo '{"jsonrpc":"2.0","id":1,"method":"library.datasets"}' | heliad --library ~/HeliadLibrary serve

From Python:

```python
import json, subprocess

core = subprocess.Popen(["heliad", "--library", "/Users/you/HeliadLibrary", "serve"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

def call(method, id=1, **params):
    core.stdin.write(json.dumps({"jsonrpc": "2.0", "id": id, "method": method, "params": params}) + "\n")
    core.stdin.flush()
    while True:
        msg = json.loads(core.stdout.readline())
        if msg.get("id") == id:          # skip notifications
            if "error" in msg:
                raise RuntimeError(msg["error"]["message"])
            return msg["result"]

print(call("ingest.path", path="/data/aia_193.fits"))
core.stdin.close()
core.wait()
```

## Several clients on one library

The first `heliad serve` on a library holds its lock and is the writer. A second `heliad serve` on
the same library (Studio while the Heliad app is open, say) does not fail: it relays its client's
lines to the writer. Every client gets its own replies, and every client hears every
notification, so a download started from one shows its progress in all of them. When the
writer's own client leaves, the writer keeps running until the last relay has gone.

The writer listens on 127.0.0.1 only, at a port it picks. It writes the port and a random token
to `<library>/.heliad.endpoint`, readable only by you on macOS and Linux (not restricted on
Windows). A connection that does not send the token first is closed. You never need to use this
socket yourself: start `heliad serve` and it relays.

If the lock is held by something that is not serving (`heliad import`, say), `heliad serve` waits
5 seconds for an endpoint, then exits with code 3 and says why.

## Queries

`download.enqueue`, `search` and `ingest.path` take a query: an object with these fields, all
optional except `start` and `end`. Times are UTC, ISO 8601.

| Field | Meaning |
|---|---|
| `start`, `end` | the time range |
| `instrument` | as the archive names it, e.g. `AIA` |
| `wavelength`, `wavelength_max` | Angstrom; one value, or a range |
| `source`, `provider`, `detector`, `level`, `physobs` | narrow the search as sunpy's Fido attributes do |
| `sample_s` | cadence in seconds |
| `jsoc_series` | a JSOC series for the `jsoc` source, e.g. `aia.lev1_euv_12s` |

    {"start": "2025-09-21T16:00:00", "end": "2025-09-21T17:00:00", "instrument": "AIA", "wavelength": 193.0}

The same fields, URL-encoded, make a `heliad://search?...` or `heliad://get?...` link; see
[`query.from_url`](#queryfrom_url).

## Library methods

### `heliad.hello`

Params: none. Versions, the library, and every method this core answers. Call it first to check
that the core is new enough.

    {"name": "heliad", "version": "0.1.0", "schema_version": 4, "reader_compat": 1, "rules_version": 3,
     "reindex_due": false, "readonly": false, "library_root": ".../library",
     "db_path": ".../library/heliad.sqlite", "methods": ["download.cancel", "download.enqueue", ...]}

### `library.stats`

Params: none. Counts for the whole library. `reindex_due` is true when the rules that turn
headers into datasets changed since the files were indexed (see `library.reindex`).

    {"library_root": ".../library", "schema_version": 4, "rules_version": 3, "reindex_due": false,
     "datasets": 2, "observations": 2, "files": 3, "bytes": 17280, "warnings": 0, "collisions": 0}

### `library.datasets`

Params: none. Every dataset (one instrument, channel, product and level), ordered by label.

    [{"id": 1, "dataset_key": "sdo/aia/-/-/1/193", "observatory": "SDO", "instrument": "AIA",
      "detector": null, "product": null, "level": "1", "wavelength": 193.0, "label": "SDO AIA 193 Å L1",
      "observations": 1, "first": "2025-09-21T16:00:04.840", "last": "2025-09-21T16:00:04.840", "bytes": 5760}, ...]

### `library.files`

Params: `dataset_key=None, start=None, end=None, limit=500, offset=0, include_rejected=False`. The
current file of each observation (its newest version), in time order, optionally one dataset and
a time range. Page with `limit` and `offset`. `relpath` is relative to the library root. Rejected
observations (see [`library.reject`](#libraryreject)) are left out, as they are from
`current_files`, `library.coverage` and views; with `include_rejected` true they are listed too, and
every row carries `rejected`: the reason, or null.

    [{"file_id": 1, "relpath": "store/sdo/aia/1/193/2025/09/21/a.fits", "sha256": "0610e5...", "size": 5760,
      "version": null, "observation_id": 1, "date_obs": "2025-09-21T16:00:04.840", "dataset_id": 1,
      "dataset_key": "sdo/aia/-/-/1/193", "label": "SDO AIA 193 Å L1", "observatory": "SDO",
      "instrument": "AIA", "detector": null, "product": null, "level": "1", "wavelength": 193.0}, ...]

### `library.checks`

Params: `file_id`. The header checks recorded when the file was ingested: a list of
`{"code", "severity", "message"}`, empty when nothing was found.

### `library.verify`

Params: none. Recomputes every stored file's checksum. Changes nothing.

    {"ok": 3, "missing": [], "altered": []}

### `library.versions`

Params: `observation_id`. Every file of one observation (reprocessed versions such as PUNCH
FILEVRSN 0k and 0l), newest first; `current` is 1 for the one readers are served.

    [{"file_id": 2, "relpath": "store/punch/.../p1.fits", "version": "0l", "size": 5760,
      "original_name": "p1.fits", "ingested_at": "2026-10-19T19:19:49+00:00", "current": 1},
     {"file_id": 3, "relpath": "store/punch/.../p2.fits", "version": "0k", ..., "current": 0}]

### `library.collisions`

Params: none. Observations holding two different files with the same version as the current
one: `[{"observation_id", "label", "date_obs", "files"}]`. Zero is normal; anything else means the
dataset key misses a keyword that tells the files apart.

### `library.delete`

Params: `file_ids=(), observation_ids=(), dataset_keys=(), dry_run=True`. Removes files from the
library. **A dry run unless `dry_run` is false**, so a client can show what would go before it
goes. Deleting a current version makes the next newest current; an observation or dataset with
no files left goes too; views that lost members are rebuilt.

    {"files": 1, "observations": 1, "datasets": 1, "repointed": 0, "bytes_released": 5760,
     "unlink_failed": [], "views_rebuilt": ["connect"], "dry_run": true}

### `library.reindex`

Params: `dry_run=False`. Applies the current header rules to every file already kept, in one
transaction. File ids, paths and checksums never change.

    {"files": 3, "datasets_before": 2, "datasets_after": 2, "observations_before": 2,
     "observations_after": 2, "files_moved": 0, "current_changed": 0, "dry_run": true}

### `library.thumbnail`

Params: `file_id, size=256`. A greyscale PNG preview, north up, made on first request and kept
under `.thumbs/`. Answers in the background.

    {"path": ".../library/.thumbs/06/0610e5...-64.png"}

### `library.coverage`

Params: `start=None, end=None, bins=240, dataset_keys=None, instrument=None, wavelength=None,
wavelength_max=None`. Per dataset, how many current observations fall in each of `bins` equal
bins of the range (the app's Timeline). Without `start` or `end`, the library's own span.

    {"start": "2025-09-21T00:00:00.000", "end": "2025-09-22T00:00:00.000", "bins": 4, "bin_seconds": 21600.0,
     "datasets": [{"dataset_key": "sdo/aia/-/-/1/193", "label": "SDO AIA 193 Å L1", "instrument": "AIA",
                   "wavelength": 193.0, "counts": [0, 0, 1, 0], "frames": 1,
                   "first": "2025-09-21T16:00:04.840", "last": "2025-09-21T16:00:04.840", "undated": 0}, ...]}

## Ingest methods

### `ingest.path`

Params: `path, source_url=None, query=None`. Adds one FITS file. The file is copied into the store
(an APFS clone on a Mac, so no extra disk) and left where it was. `source_url` and `query` are
recorded with it, for provenance. A file cut off in transit, or not readable as FITS, is refused
with code -32001 (see [Errors](#errors)).

    {"status": "added", "file_id": 1, "relpath": "store/sdo/aia/1/193/2025/09/21/a.fits", "findings": []}

`status` is one of:

| Status | Meaning |
|---|---|
| `added` | a new observation |
| `duplicate` | the same bytes are already in the library; nothing stored |
| `new_version` | a newer version of an observation already kept; it becomes current |
| `older_version` | an older version; kept, the newer one stays current |
| `same_version` | another file claims the same version with different bytes; kept, flagged in `findings` |

### `ingest.tree`

Params: `path`. Adds every FITS file under a folder (an event folder, an old cache). One bad file
does not stop it. Sends `ingest.progress` as it goes.

    {"files": 2, "counts": {"added": 1, "older_version": 1}, "errors": []}

## Download and search methods

A download is a job. `download.enqueue` returns at once; a worker thread searches, fetches into a
staging folder, and ingests, reporting `download.progress` and finally `download.finished`. A job
ends `done`, `partial` (some files stored), `failed` or `cancelled`.

Sources: `fido` (VSO, JSOC and every client sunpy registers; the default) and `jsoc` (a JSOC
export by record set, through drms; needs the `jsoc_email` setting).

### `download.enqueue`

Params: `query, source='fido'`. Queues a download of everything the query finds.

    {"job_id": 1, "source": "fido", "status": "queued", "phase": null, "files_total": null, "files_done": 0,
     "error": null, "created_at": "...", "updated_at": "...", "query": {...}, "result": null}

### `download.list`

Params: `status=None, limit=50`. Jobs, newest first, optionally only one status (`queued`,
`active`, `done`, `partial`, `failed`, `cancelled`). `options` is null for a plain download, and
holds what `dataset.upgrade` or `library.refetch` queued the job with. A finished job's `result`
says what happened (`skipped` counts the records an upgrade left out because the library has them):

    [{"job_id": 1, "status": "done", "files_total": 1, "files_done": 1, ...,
      "result": {"found": 1, "fetched": 1, "stored": 1, "errors": [], "ingest": {"added": 1}}}]

### `download.get`

Params: `job_id`. One job, as in `download.list`, or null.

### `download.cancel`

Params: `job_id`. A queued job is cancelled at once (`{"job_id": 1, "status": "cancelled"}`). A
running one answers `"status": "cancelling"`: a fetch already running finishes first, and nothing
from it is ingested. A job that already ended answers with its status.

### `download.retry`

Params: `job_id`. Queues a `partial` or `failed` job again; it fetches only what is still missing.
Any other job is an error.

### `search`

Params: `query, source='fido', bins=240`. Asks the archive what it holds; downloads nothing.
Answers in the background. With `fido`, one entry per provider (at most 2000 rows each); every
record's time comes back binned over the query's range as `coverage`, the way `library.coverage`
bins the index, so the two can be drawn one above the other. The shape (not a real result):

    {"query": "{...}", "total": ...,
     "providers": [{"client": "VSOClient", "count": ..., "columns": ["Start Time", ...],
                    "rows": [{...}], "coverage": {"start": ..., "end": ..., "bins": 240, "bin_seconds": ...,
                                                  "counts": [...], ...}}]}

With `jsoc`: `{"query", "recordset", "total", "rows", "coverage"}`.

### `query.from_url`

Params: `url`. The action and query in a `heliad://` link. Unknown parameters are refused.

    query.from_url(url="heliad://search?start=2025-09-21T16:00:00&end=2025-09-21T17:00:00&instrument=AIA&wavelength=193")
    {"action": "search", "query": {"end": "2025-09-21T17:00:00", "instrument": "AIA",
                                   "start": "2025-09-21T16:00:00", "wavelength": 193.0}}

## Cache editor methods

The Heliad app's cache editor works on a library HelioFITS Studio filled: it sets bad frames
aside, asks the archive again for one frame, and densifies a dataset by fetching only the frames
it lacks.

Two times are **one frame** when they lie within the tolerance: half the spacing, clamped to
1 s to 60 s. The spacing is the dataset's median spacing between observations, or the target
cadence when that is denser (`sample_s` for `dataset.upgrade`, the archive's median spacing for
`dataset.cadences`), so densifying 24 s to 12 s does not mistake the new frames for the old. A
dataset with fewer than two observations gets 60 s. A rejected observation counts as a frame the
library has, so an upgrade never fetches it again.

### `library.reject`

Params: `observation_ids, reason`. Sets observations aside: `current_files` (what Studio reads),
`library.files`, `library.coverage` and views stop showing them, and views holding them are
rebuilt. Nothing is deleted; `library.unreject` brings them back. `reason` is required.
Rejecting one already rejected replaces its reason.

    {"rejected": 1, "observation_ids": [2], "views_rebuilt": []}

### `library.unreject`

Params: `observation_ids`. Serves rejected observations to readers again.

    {"unrejected": 1, "observation_ids": [2], "views_rebuilt": []}

### `library.rejected`

Params: `dataset_key=None`. Rejected observations, in time order, with the reason, when, and the
current file.

    [{"observation_id": 2, "dataset_key": "sdo/aia/-/-/1/193", "label": "SDO AIA 193 Å L1",
      "date_obs": "2025-09-21T16:00:24.840", "reason": "smeared by a flare",
      "rejected_at": "2026-10-21T00:14:26+00:00", "file_id": 2,
      "relpath": "store/sdo/aia/1/193/2025/09/21/aia1.fits"}]

### `library.refetch`

Params: `observation_id`. Queues a job for the dataset's query (see `dataset.query`) narrowed to
the observation's time plus or minus the tolerance, at full cadence. A newer version (a higher
FILEVRSN) that arrives becomes current; an identical file is a `duplicate`. Returns the job, as
`download.get` does.

    {"job_id": 2, "source": "fido", "status": "queued", ...,
     "query": {"end": "2025-09-21T16:00:54.840", "instrument": "AIA", "level": "1", "source": "SDO",
               "start": "2025-09-21T16:00:42.840", "wavelength": 193.0},
     "result": null, "options": {"missing_only": false, "observation_id": 3}}

### `dataset.query`

Params: `dataset_key`. The query that asks the archive again for a dataset, with every field (one
that could not be read is null). The newest archive query stored with the dataset's files is
preferred, widened to the dataset's first and last observation. A dataset that was imported
rather than downloaded has one inferred from its row (instrument, wavelength, detector, level,
the observatory as `source` when sunpy has a Source of that name) and `sample_s` from the median
spacing of its observations; `inferred` is then true. `basis` says where it came from.

    {"query": {"start": "2025-09-21T16:00:00.840", "end": "2025-09-21T16:01:12.840", "instrument": "AIA",
               "wavelength": 193.0, "wavelength_max": null, "source": "SDO", "provider": null,
               "detector": null, "level": "1", "physobs": null, "sample_s": 24.0, "jsoc_series": null,
               "extra": {}},
     "source": "fido", "inferred": true,
     "basis": "inferred from the dataset (instrument, wavelength, detector and level as its headers give them), its span, and the median spacing of its observations"}

### `dataset.cadences`

Params: `dataset_key, start=None, end=None`. Searches the archive over the dataset's query at full
cadence (without `sample_s`), by default over the dataset's span, and compares: `library` counts
the dataset's observations in the range (`rejected` of them set aside), `archive` the distinct
record times the search found, each with its median spacing in seconds. `missing` lists the
archive's times with no observation within `tolerance_s`. Downloads nothing; answers in the
background.

    {"dataset_key": "sdo/aia/-/-/1/193", "query": {..., "sample_s": null, ...}, "source": "fido",
     "tolerance_s": 6.0,
     "library": {"count": 4, "median_spacing_s": 24.0, "rejected": 1},
     "archive": {"count": 6, "median_spacing_s": 12.0},
     "missing": ["2025-09-21T16:00:12.000", "2025-09-21T16:00:36.000", "2025-09-21T16:01:00.000"]}

### `dataset.upgrade`

Params: `dataset_key, sample_s=None, start=None, end=None, dry_run=True`. Queues a job for the
dataset's query that fetches only the frames the dataset lacks; `sample_s`, `start` and `end`
replace the query's own (`sample_s` 12 on a 24 s dataset doubles its cadence). **A dry run unless
`dry_run` is false**: it returns the query and the job's options and queues nothing.

    {"dry_run": true, "query": {..., "sample_s": 12, ...}, "source": "fido",
     "options": {"dataset_key": "sdo/aia/-/-/1/193", "missing_only": true, "tolerance_s": 6.0},
     "inferred": true, "basis": "..."}

Otherwise the queued job, as `download.get` returns it. When it ends, its `result` counts what it
`skipped`:

    {"job_id": 1, "status": "done", ..., "result": {"found": 3, "fetched": 3, "stored": 3, "errors": [],
     "ingest": {"added": 3}, "skipped": 3},
     "options": {"dataset_key": "sdo/aia/-/-/1/193", "missing_only": true, "tolerance_s": 6.0}}

## View methods

A view is a named selection of observations, such as an event. It always resolves to the newest
version of each file. Its folder, `views/<name>/`, holds relative links and a `manifest.csv`, and
can be rebuilt at any time; programs should resolve paths through the index, not the links.

### `view.create`

Params: `name, description=None, dataset_keys=None, start=None, end=None, observation_ids=None`.
Creates the view, or adds to it: the observations matching `dataset_keys`, `start` and `end`
(each optional, all applied together), plus any `observation_ids`.

    {"view_id": 1, "name": "connect", "observations": 2}

### `view.materialize`

Params: `name`. Builds the view's folder.

    {"name": "connect", "path": ".../library/views/connect", "files": 2, "link_modes": {"symlink": 2}}

### `view.rebuild_all`

Params: none. `view.materialize` for every view; a list of its results.

### `view.members`

Params: `name`. The current files of a view, as `library.files` gives them, plus `source_url`,
`original_name` and `query_json`.

### `view.list`

Params: none.

    [{"view_id": 1, "name": "connect", "kind": "event", "description": null, "created_at": "...",
      "observations": 2, "datasets": 2, "bytes": 11520, "first": "2025-09-21T16:00:04.840",
      "last": "2025-09-21T17:00:00.000", "path": ".../library/views/connect", "materialized": true}]

## Settings methods

Settings live in `settings.json` in Heliad's app-support folder, beside `config.json`. They are per
user, not per library.

### `settings.get`

Params: none. Every setting, as an object. `HELIAD_JSOC_EMAIL` in the environment overrides
`jsoc_email`.

### `settings.set`

Params: `key, value=None`. Sets one setting; a null `value` removes it. Returns all settings.
Known keys: `jsoc_email` (the address registered with JSOC exports) and `vso_mirrors` (VSO
mirrors to retry, comma-separated, or `none`).

## Notifications

Sent without an `id`, to every client of the core.

### `download.progress`

Params: `job_id, phase, done, total`. `phase` is `searching`, `fetching` or `ingesting`; `done` and
`total` count files where known, else null.

    {"jsonrpc":"2.0","method":"download.progress","params":{"job_id":1,"phase":"fetching","done":0,"total":1}}

### `download.finished`

Params: `job_id, status, error, result`. A job ended; `result` is as in `download.list`.

    {"jsonrpc":"2.0","method":"download.finished","params":{"job_id":1,"status":"done","error":null,"result":{"found":1,"fetched":1,"stored":1,"errors":[],"ingest":{"added":1}}}}

### `ingest.progress`

Params: `done, total, path`. Sent by `ingest.tree` every 25 files and at the end.

## Errors

An error reply has `error` instead of `result`:

    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no method 'nope'"}}

| Code | When |
|---|---|
| -32700 | the line is not JSON |
| -32600 | not a JSON-RPC 2.0 request; or a download asked of a read-only core |
| -32601 | no such method |
| -32602 | wrong or missing params (`params` must be an object), or a bad `heliad://` link |
| -32603 | the method failed; `message` names the exception, `data` holds a short traceback |
| -32001 | `ingest.path`: the file can never enter the library (cut off, or not readable as FITS), so offering it again will not help. A -32603 from `ingest.path` (a full disk, say) may pass |

## The command line and the API

Every `heliad` command is a thin layer over the same library code. Commands that only read open
the library read-only, so they work while a core holds it. A command prints a table or a few lines
for a person; with `--json` it prints exactly the method's result. Exit codes: 0 done; 1 the
operation ran and reported a problem (`verify` found missing or altered files, `get` ended partial
or failed, `import` had errors); 2 bad arguments or no library; 3 the library is locked by another
core that is not serving it.

| Command | Method |
|---|---|
| `heliad stats` | `library.stats` |
| `heliad datasets` | `library.datasets` |
| `heliad files [--dataset KEY] [--start T] [--end T]` | `library.files` |
| `heliad versions OBSERVATION_ID` | `library.versions` |
| `heliad collisions` | `library.collisions` |
| `heliad verify` | `library.verify` |
| `heliad thumb FILE_ID [--size N]` | `library.thumbnail` |
| `heliad import PATH...` | `ingest.path`, `ingest.tree` |
| `heliad search --start T --end T ...` | `search` |
| `heliad get --start T --end T ...` | `download.enqueue`, run to the end |
| `heliad jobs [--status S]` | `download.list` |
| `heliad delete ... [--yes]` | `library.delete` |
| `heliad reindex [--dry-run]` | `library.reindex` |
| `heliad view create NAME ...`, `view build NAME`, `view rebuild` | `view.create` and `view.materialize`, `view.materialize`, `view.rebuild_all` |
| `heliad settings [KEY [VALUE] \| --unset KEY]` | `settings.get`, `settings.set` |
| `heliad serve` | all of them, over stdin and stdout |
| `heliad api [METHOD]` | prints this list |
