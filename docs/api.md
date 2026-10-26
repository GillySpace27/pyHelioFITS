# HelioFITS Core API

Everything the HelioFITS Cache Manager app and HelioFITS Studio do with a library goes through one interface:
`hfcore serve`, which speaks JSON-RPC 2.0 over its stdin and stdout. Scripts can use it the same
way. `hfcore api` prints the list below from the installed core, and `hfcore api METHOD` prints
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
- [Old names](#old-names)
- [Processing record methods](#processing-record-methods)
- [Notifications](#notifications)
- [Errors](#errors)
- [The command line and the API](#the-command-line-and-the-api)

The examples come from a scratch library (paths shortened, long results cut with `...`).

## Talking to a core

Start a core on a library:

    hfcore --library ~/HelioFITSLibrary serve

Without `--library` it serves the default library (the one `hfcore init` chose). Then write one
request per line to its stdin:

    {"jsonrpc": "2.0", "id": 1, "method": "library.stats"}

and read one line per reply from its stdout:

    {"jsonrpc":"2.0","id":1,"result":{"library_root":"/Users/you/HelioFITSLibrary","schema_version":4,"rules_version":3,"reindex_due":false,"datasets":2,"observations":2,"files":3,"bytes":17280,"warnings":0,"collisions":0}}

- **Params are named**: `"params": {"file_id": 1}`. Leave `params` out for a method that takes none.
- **Match replies by `id`.** Most methods answer in order. `archive.search`, `library.thumbnail` and
  `dataset.cadences` run in the background and answer when done, so their replies can overtake
  others.
- **Notifications** are lines with a `method` and no `id` (download and ingest progress). Ignore
  the ones you do not need.
- **Ending**: close the core's stdin. It answers what it was already asked, then exits.

From a shell:

    echo '{"jsonrpc":"2.0","id":1,"method":"library.datasets"}' | hfcore --library ~/HelioFITSLibrary serve

From Python:

```python
import json, subprocess

core = subprocess.Popen(["hfcore", "--library", "/Users/you/HelioFITSLibrary", "serve"],
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

The first `hfcore serve` on a library holds its lock and is the writer. A second `hfcore serve` on
the same library (Studio while the HelioFITS Cache Manager app is open, say) does not fail: it relays its client's
lines to the writer. Every client gets its own replies, and every client hears every
notification, so a download started from one shows its progress in all of them. When the
writer's own client leaves, the writer keeps running until the last relay has gone.

The writer listens on 127.0.0.1 only, at a port it picks. It writes the port and a random token
to `<library>/.heliad.endpoint`, readable only by you on macOS and Linux (not restricted on
Windows). A connection that does not send the token first is closed. You never need to use this
socket yourself: start `hfcore serve` and it relays.

If the lock is held by something that is not serving (`hfcore import`, say), `hfcore serve` waits
5 seconds for an endpoint, then exits with code 3 and says why.

## Queries

`download.enqueue`, `archive.search` and `ingest.path` take a query: an object with these fields, all
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

The same fields, URL-encoded, make a `hfcore://search?...` or `hfcore://get?...` link; see
[`link.parse`](#linkparse).

## Library methods

### `core.hello`

Params: none. Versions, the library, and every method this core answers. Call it first to check
that the core is new enough.

    {"name": "heliofits-core", "version": "0.1.0", "schema_version": 5, "reader_compat": 1, "rules_version": 4,
     "reindex_due": false, "readonly": false, "library_root": ".../library",
     "db_path": ".../library/hfcore.sqlite", "methods": ["archive.search", "dataset.cadences", ...],
     "aliases": {"search": "archive.search", "query.from_url": "link.parse", "dataset.from_url": "link.parse",
                 "heliad.hello": "core.hello"}}

`aliases` maps each old method name this core still answers to the method it stands for.

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

### `library.fix_extensions`

Params: `dry_run=True`. **A dry run unless `dry_run` is false.** Gives every stored file whose
name has no FITS suffix one: `.fits`, or `.fits.gz` when the file starts with gzip's magic bytes
(`1f 8b`); a file that starts with neither, or a name already ending in `.gz`, is left alone
(`not_fits`). Ingest does the same for new files (Studio's oldest cache files are named by a bare
SHA-256). The CLI is `hfcore fix-extensions [--dry-run]`; both run in the writer, under its lock.

For each file, in this order: rename it in its folder; in one transaction, point `files.relpath`
at the new name and record the old one in `file_aliases`; then leave a relative symlink at the old
name, so a saved Studio session holding the old path still opens. A run that stops between steps is
finished by the next one (a renamed file whose row still has the old name is found by its new name
and checksum; a missing link is made from `file_aliases`), and a second run changes nothing. Where
symlinks cannot be made the alias is still recorded (`links_skipped`). A file another process has
open is skipped (`open`) when that is cheap to see: `open_check` says how (`proc` on Linux, `lsof`
on macOS, `unavailable` elsewhere, where nothing is skipped). Views are rebuilt afterwards;
thumbnails are keyed by checksum and survive. Deleting a file removes its aliases and old-name links.

    {"candidates": 2, "renamed": 2, "fits": 1, "fits_gz": 1, "not_fits": 0, "missing": 0, "open": 0,
     "failed": [], "links_made": 2, "links_skipped": 0, "open_check": "proc", "dry_run": false,
     "examples": [{"from": "store/sdo/aia/1/171/2025/09/21/0123abcd",
                   "to": "store/sdo/aia/1/171/2025/09/21/0123abcd.fits.gz"}, ...],
     "views_rebuilt": ["event"]}

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

### `archive.search`

Params: `query, source='fido', bins=240`. Asks the archive what it holds; downloads nothing.
Answers in the background. With `fido`, one entry per provider (at most 2000 rows each); every
record's time comes back binned over the query's range as `coverage`, the way `library.coverage`
bins the index, so the two can be drawn one above the other. The shape (not a real result):

    {"query": "{...}", "total": ...,
     "providers": [{"client": "VSOClient", "count": ..., "columns": ["Start Time", ...],
                    "rows": [{...}], "coverage": {"start": ..., "end": ..., "bins": 240, "bin_seconds": ...,
                                                  "counts": [...], ...}}]}

With `jsoc`: `{"query", "recordset", "total", "rows", "coverage"}`. An archive that cannot be
asked (the network, a server error, a missing `jsoc_email`) is error -32004; a query the source
cannot use is -32602.

### `link.parse`

Params: `url`. What a `hfcore://` link asks for. Unknown parameters are refused (-32602). A
`hfcore://search?...` or `hfcore://get?...` link carries a query:

    link.parse(url="hfcore://search?start=2025-09-21T16:00:00&end=2025-09-21T17:00:00&instrument=AIA&wavelength=193")
    {"action": "search", "query": {"end": "2025-09-21T17:00:00", "instrument": "AIA",
                                   "start": "2025-09-21T16:00:00", "wavelength": 193.0}}

A `hfcore://dataset/<dataset_uid>?start=<iso>&end=<iso>` link names one dataset over a range
(see [Stable dataset ids](#stable-dataset-ids)); `dataset` is null when this library has no such
uid, and `start` or `end` is null when the link leaves it out:

    link.parse(url="hfcore://dataset/0b6f8d2e-8a1f-4c1e-9d55-2f3c7e1a4b90?start=2025-09-21T16:00:00&end=2025-09-21T17:00:00")
    {"action": "dataset", "dataset_uid": "0b6f8d2e-8a1f-4c1e-9d55-2f3c7e1a4b90", "start": "2025-09-21T16:00:00",
     "end": "2025-09-21T17:00:00", "dataset": {"dataset_key": "punch/wfi+nfi/-/cam/3/5300", ...}}

## Cache editor methods

The HelioFITS Core app's cache editor works on a library HelioFITS Studio filled: it sets bad frames
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

### `layer.upgrade`

Params: `headers, sample_s=None, dry_run=True`. For a layer that shows pictures rather than FITS
files (Studio's Helioviewer layers, streamed as JPEG 2000), says per frame whether the library
keeps a FITS file of it, and plans the fetch for the rest. `headers` is a list of objects of FITS
keywords, one per frame: the header each picture carries, from the FITS file it was made from (at
most 10000). Each is read with ingest's own rules, so `TELESCOP = 'SDO/AIA'`, `INSTRUME = 'AIA_3'`
is SDO AIA as it would be in the library.

A picture and a library file are one frame when they agree on observatory, instrument, detector
and wavelength (within the passband tolerance) and lie within `tolerance_s` of each other. Level
and product are not compared: a picture says what it was made from, which need not be the level
the archive serves. HMI pictures add `physobs` from `CONTENT` (`MAGNETOGRAM` is
`LOS_magnetic_field`, `CONTINUUM INTENSITY` is `intensity`), since both products are 6173 Å; the
library files HMI's products as one dataset, so an HMI frame found in the library may be the other
product.

Per frame, `status` is `in_library` (with `file_id`, `relpath`, `path`, `dataset_key`,
`observation_id` and the file's own `fits_date_obs`), `rejected` (set aside in the library; never
fetched again), `missing`, or `unreadable` (its header gives no instrument or no time; `reason`
says which). Frames of one kind form a `group`; a group with missing frames has `fetch`: the query
at the frames' own spacing (or `sample_s`), over the missing frames' span widened by the tolerance.
When the library has the dataset, that is the dataset's query with `missing_only` (as
`dataset.upgrade`); otherwise a Fido query inferred from the headers (`inferred: true`). **A dry run
unless `dry_run` is false**; otherwise each group's fetch is queued and the group carries its
`job`, as `download.get` returns it. Call again once the jobs end to get the files.

    {"dry_run": true, "complete": false,
     "counts": {"in_library": 1, "rejected": 0, "missing": 2, "unreadable": 0},
     "frames": [{"frame": 0, "date_obs": "2025-09-21T16:00:04.840", "group": 0, "status": "in_library",
                 "file_id": 7, "relpath": "...", "path": "...", "dataset_key": "sdo/aia/-/-/1/171", ...},
                {"frame": 1, "date_obs": "2025-09-21T16:30:04.840", "group": 0, "status": "missing", ...}],
     "groups": [{"group": 0, "label": "SDO AIA 171 Å", "count": 3, "spacing_s": 1800.0, "tolerance_s": 60.0,
                 "counts": {"in_library": 1, "rejected": 0, "missing": 2}, "datasets": ["sdo/aia/-/-/1/171"],
                 "fetch": {"query": {..., "sample_s": 1800.0}, "source": "fido",
                           "options": {"dataset_key": "sdo/aia/-/-/1/171", "missing_only": true, "tolerance_s": 60.0},
                           "inferred": false, "basis": "..."}}]}

## Stable dataset ids

A `dataset_key` is built from header rules, so a rules change can rename a dataset (PUNCH's
`.../530` became `.../5300` once `WAVEUNIT = 'nanometer'` was read). Since schema 5 every dataset
also has a `dataset_uid`, a random UUID that never changes, and `library.datasets` lists it.
`library.reindex` gives a renamed dataset's uid (and its row and `dataset_id`) to the new key that
holds most of its files by sha256, records the old key in `dataset_aliases`, and lists each rename
in its result as `datasets_renamed: [{old_key, new_key, dataset_uid}]`. Save the uid, not the key,
in anything that has to find a dataset again.

In the examples below the uid is an illustration, not a real one.

The link form is `hfcore://dataset/<dataset_uid>?start=<iso>&end=<iso>` (start and end optional).
The Mac app opens it by selecting the dataset; [`link.parse`](#linkparse) reads one.

### `dataset.get`

Params: `dataset_uid=None, dataset_key=None` (exactly one). The dataset as `library.datasets`
lists it, plus `resolved_by`: `"uid"`, `"key"`, or `"alias"` when `dataset_key` is an old key a
reindex renamed. Null when nothing matches.

    {"id": 3, "dataset_key": "punch/wfi+nfi/-/cam/3/5300", "dataset_uid": "0b6f8d2e-8a1f-4c1e-9d55-2f3c7e1a4b90",
     "label": "PUNCH WFI+NFI 530 nm CAM L3", ..., "observations": 12, "resolved_by": "alias"}

### `dataset.resolve`

Params: `key`. A uid, a current key, or an old key, to `{dataset_uid, dataset_key, resolved_by}`;
null when none matches. A current key wins over an alias of the same text.

    {"dataset_uid": "0b6f8d2e-8a1f-4c1e-9d55-2f3c7e1a4b90", "dataset_key": "punch/wfi+nfi/-/cam/3/5300",
     "resolved_by": "alias"}

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

Settings live in `settings.json` in HelioFITS Core's app-support folder, beside `config.json`. They are per
user, not per library.

### `settings.get`

Params: none. Every setting, as an object. `HFCORE_JSOC_EMAIL` in the environment overrides
`jsoc_email`.

### `settings.set`

Params: `key, value=None`. Sets one setting; a null `value` removes it. Returns all settings.
Known keys: `jsoc_email` (the address registered with JSOC exports) and `vso_mirrors` (VSO
mirrors to retry, comma-separated, or `none`).

## Old names

A method keeps answering under a name it once had, so a client written against it keeps
working; `core.hello` lists them as `aliases`. A new client uses the new name.

### `search`

Params: `query, source='fido', bins=240`. The old name of [`archive.search`](#archivesearch); answers the same.

### `query.from_url`

Params: `url`. The old name of [`link.parse`](#linkparse); answers the same.

### `dataset.from_url`

Params: `url`. The old name of [`link.parse`](#linkparse); answers the same (a dataset link's
result has gained `"action": "dataset"`).

### `heliad.hello`

Params: none. The old name of [`core.hello`](#corehello), from before HelioFITS Core was renamed
(it was Heliad); answers the same.

## Processing record methods

A processing record (format `hfrecord` 1.x, schema in `src/heliofits_core/schemas/hfrecord-1.schema.json`)
says what made an exported image or movie: each layer's dataset, its source files by SHA-256, and
the steps applied to them in order. HelioFITS Studio writes it beside the export
(`<export file name>.hfrec.json`, the complete copy) and inside it where the container takes one. These
methods read records; neither writes anything.

### `record.read`

Params: `path`, an exported file or a sidecar. Answers in the background, like `archive.search`. The core
looks, in this order, for:

1. a sidecar named for the whole export file, `<file>.hfrec.json` (`frame.png.hfrec.json`, the
   canonical name), then `<basename>.hfrec.json` (`frame.hfrec.json`) as a fallback, or `path`
   itself when it ends in `.hfrec.json`;
2. a PNG `tEXt`, `zTXt` or `iTXt` chunk with keyword `hfrecord`;
3. an OpenEXR string header attribute `hfrecord` (the header is parsed directly; OpenEXR is not needed);
4. an MP4 or MOV metadata tag `hfrecord`, through `ffprobe -show_format` when `ffprobe` is on
   `PATH` (without it movies are skipped, and `skipped` says so);
5. a FITS extension named `HFRECORD` (bytes as an 8-bit image, or text in a table's first column),
   then an `hfrecord.json` in the FITS file's folder (Send to Python);
6. only when none of those holds a record: Studio's older `hfstudio` block (PNG chunk, EXR
   attribute or movie comment), converted into a minimal record with `legacy: true`. Its layers and
   steps are read from the saved scene; its sources are archive addresses with no SHA-256.

A record whose `hfrecord` major version is not 1, or whose `reader_compat` is above 1, is refused
with error -32010. A higher minor version (1.7, say) is read; keys this core does not know are kept
and ignored.

    {"jsonrpc":"2.0","id":1,"method":"record.read","params":{"path":"/Users/you/Exports/connect.png"}}

Result: `found` (false, with `record` and `view` null, when the file carries no record), `path`,
`record` (as written), `found_in` (`kind`: sidecar, png, exr, mp4, fits or folder; `container`, such
as `png:tEXt:hfrecord`; `path`), `legacy`, `valid` and `errors` (checked against the schema; an
invalid record is still returned), `skipped` (places that held something unreadable),
`output_check` (sidecars only: the export's `sha256` against `output.sha256`, `matches` true, false
or null when the export is not beside it) and `view`, the rows to show as they are:

    {"found":true,"legacy":false,"valid":true,"errors":[],"found_in":{"kind":"sidecar","container":"sidecar","path":".../connect.hfrec.json"},
     "view":{"summary":"HelioFITS Studio <version>, still at 2025-09-21T20:32:00Z.\n1. PUNCH ...",
       "producer":"HelioFITS Studio <VERSION> (build ..., commit ...)","created":"2026-10-22T00:00:00Z",
       "output":[{"name":"kind","value":"still"},...],"scene":[{"name":"time","value":"2025-09-21T20:32:00Z"},...],
       "layers":[{"drawn":2,"name":"SDO AIA 193 Å L1","dataset_key":"sdo/aia/-/-/1/193","dataset_uid":null,
         "dataset_label":"SDO AIA 193 Å L1","frames":1,"filter":"no filter","lut":"SDO-AIA 193 Å","varies":false,
         "steps":[{"index":1,"op":"select_plane","stage":"decode","automatic":false,"params":[{"name":"plane","value":"0"}],
           "resolved":[],"varies":false,"automation":[],"reference":null,"caveat":null,"citation":null,"stamp":null,...},...],
         "acknowledgement":null,"studio":{"dataset":"sdo/aia/-/-/1/193","start":"2025-09-21T20:32:04.840",
           "end":"2025-09-21T20:32:04.840","args":["-dataset","sdo/aia/-/-/1/193","-start","...","-end","..."]}},...],
       "caveats":[...],"citations":[...],"acknowledgements":[],"provenance":[],"not_guaranteed_to_rebuild":[],
       "studio":{...}}}

`view.layers` lists the top layer first; `drawn` is each layer's index in the record, whose order
is drawing order (0 at the bottom). Parameter values are text (JSON for anything but a string).
`known_op` is false for an op this core does not know; show it verbatim. `studio` is the command
line that opens a layer's dataset in Studio over its sources' time span: `-dataset` takes the
record's `dataset_uid` when it has one, else its `dataset_key`. The view's own `studio` is the
layer Studio marked master, else the top layer.

### `record.resolve`

Params: `record`, as `record.read` returned it. Looks every source up in the index by `sha256`
(`files.sha256` is unique). A record's dataset is matched by `dataset_uid` when both the record and
the index have one, else by `dataset_key`.

    {"jsonrpc":"2.0","id":2,"method":"record.resolve","params":{"record":{...}}}

Per source, `status` is `in_library` (the observation's current file), `superseded` (kept, but a
newer version is current; `current` names it), `missing`, or `no_hash` (a Helioviewer frame or a
legacy record, with nothing to look up). Kept sources carry `relpath`, `file_id`,
`observation_id` and `rejected` (the reason, when set aside). `missing_by_dataset` groups the
missing sources with their time span, widened by the dataset's tolerance, and `fetch`, the call
that would get them:

    {"counts":{"in_library":1,"superseded":1,"missing":2,"no_hash":1},"complete":false,
     "layers":[{"layer":0,"name":"...","dataset_key":"...","dataset_in_library":"...","sources":[{"source":0,"sha256":"...","status":"superseded",...}]},...],
     "missing_by_dataset":[{"dataset":"sdo/aia/-/-/1/193","dataset_key":"sdo/aia/-/-/1/193","in_library":true,"count":1,
       "sha256":["..."],"start":"2025-09-21T20:32:58.840","end":"2025-09-21T20:33:10.840",
       "fetch":{"method":"dataset.upgrade","params":{"dataset_key":"sdo/aia/-/-/1/193","start":"...","end":"...","dry_run":false},
         "inferred":false,"note":"..."},"reason":null}]}

For a dataset the library keeps, `fetch` is `dataset.upgrade` over the span (missing frames only,
through the dataset's own query). For one it does not, `fetch` is `download.enqueue` with a query
inferred from the record's dataset fields (`inferred: true`), or null with a `reason` when the
record names no instrument or no times. Send `fetch.method` with `fetch.params` to fetch.

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
| -32600 | not a JSON-RPC 2.0 request |
| -32601 | no such method |
| -32602 | wrong or missing params (`params` must be an object), or a param the core cannot use: a bad `hfcore://` link, a time it cannot read, a range that ends before it starts, a rejection without a reason, `bins` out of range |
| -32603 | the method failed; `message` names the exception, `data` holds a short traceback |
| -32002 | not found: no dataset, observation, file, job, view or source of that id or name; `message` names it |
| -32003 | a write (a download, a rejection, a delete) asked of a core that opened the library read-only |
| -32004 | the archive could not be asked, or refused: the network, a server error, a setting it needs (`jsoc_email`); `message` says which |
| -32005 | another process holds the library and this core cannot act on it |
| -32010 | `record.read`, `record.resolve`: a processing record of a format this core does not read (another major version, or a higher `reader_compat`) |
| -32011 | `record.read`: the file named is a sidecar (`.hfrec.json`) that is not a readable record |
| -32001 | `ingest.path`: the file can never enter the library (cut off, or not readable as FITS), so offering it again will not help. A -32603 from `ingest.path` (a full disk, say) may pass |

## The command line and the API

Every `hfcore` command is a thin layer over the same library code. Commands that only read open
the library read-only, so they work while a core holds it. A command prints a table or a few lines
for a person; with `--json` it prints exactly the method's result. Exit codes: 0 done; 1 the
operation ran and reported a problem (`verify` found missing or altered files, `get` ended partial
or failed, `import` had errors, the archive could not be asked); 2 bad arguments, an id or key the
library does not have, or no library; 3 the library is locked by another core that is not serving it.

| Command | Method |
|---|---|
| `hfcore stats` | `library.stats` |
| `hfcore datasets` | `library.datasets` |
| `hfcore files [--dataset KEY] [--start T] [--end T]` | `library.files` |
| `hfcore versions OBSERVATION_ID` | `library.versions` |
| `hfcore checks FILE_ID` | `library.checks` |
| `hfcore coverage [--start T] [--end T] [--bins N] [--dataset KEY ...]` | `library.coverage` |
| `hfcore collisions` | `library.collisions` |
| `hfcore verify` | `library.verify` |
| `hfcore thumb FILE_ID [--size N]` | `library.thumbnail` |
| `hfcore import PATH...` | `ingest.path`, `ingest.tree` |
| `hfcore search --start T --end T ...` | `archive.search` |
| `hfcore get --start T --end T ...` | `download.enqueue`, run to the end |
| `hfcore jobs [--status S]` | `download.list` |
| `hfcore delete ... [--yes]` | `library.delete` |
| `hfcore reindex [--dry-run]` | `library.reindex` |
| `hfcore fix-extensions [--dry-run]` | `library.fix_extensions` |
| `hfcore reject OBSERVATION_ID... --reason TEXT` | `library.reject` |
| `hfcore unreject OBSERVATION_ID...` | `library.unreject` |
| `hfcore rejected [--dataset KEY]` | `library.rejected` |
| `hfcore refetch OBSERVATION_ID` | `library.refetch`, run to the end |
| `hfcore cadences DATASET_KEY [--start T] [--end T]` | `dataset.cadences` |
| `hfcore upgrade DATASET_KEY [--sample S] [--start T] [--end T] [--yes]` | `dataset.upgrade` (a dry run without `--yes`), run to the end |
| `hfcore layer-upgrade HEADERS.json [--sample S] [--yes]` | `layer.upgrade` (a dry run without `--yes`), its jobs run to the end |
| `hfcore view create NAME ...`, `view build NAME`, `view rebuild` | `view.create` and `view.materialize`, `view.materialize`, `view.rebuild_all` |
| `hfcore settings [KEY [VALUE] \| --unset KEY]` | `settings.get`, `settings.set` |
| `hfcore init` | makes a library and its config; no method |
| `hfcore serve` | all of them, over stdin and stdout |
| `hfcore api [METHOD]` | prints this list |
