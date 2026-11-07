# Reading a HelioFITS Core library from another program

This is what HelioFITS Studio (and any script) may rely on. HelioFITS Core is the
only writer; everything else follows these rules and only reads. (HelioFITS Core was
called Heliad until 2026-10-25; every name it had then is still read, as below.)

## Find the library

1. Read `config.json` in HelioFITS Core's app-support folder:
   - macOS: `~/Library/Application Support/HelioFITS Core/config.json`
   - Linux: `$XDG_CONFIG_HOME/hfcore/config.json` (default `~/.config/hfcore/`)
   - Windows: `%APPDATA%\HelioFITS Core\config.json`
   - `HFCORE_HOME` (or `HELIAD_HOME`, its old name), when set, replaces the folder on every
     platform.
   - When that folder has no `config.json` but the folder from before the rename has a
     `config.json` or `settings.json` (`Heliad` on macOS and Windows, `heliad` on Linux),
     use the old folder: a computer set up before the rename keeps its config there.
2. It holds `library_root` and `db_path`. Only `hfcore init` (or the first run on a
   machine with no config) writes it, so a one-off `hfcore --library` never moves it.
   A library made before the rename has its index at `heliad.sqlite`; `db_path` names it.
3. No config file, or no file at `db_path`: there is no library. Show nothing from
   HelioFITS Core; do not create anything.

## Open the index

- Open `db_path` read-only: `jdbc:sqlite:file:<db_path>?mode=ro` (sqlite-jdbc), or
  `sqlite3.connect("file:<db_path>?mode=ro", uri=True)`. Set `PRAGMA busy_timeout = 5000`.
- Read `SELECT value FROM meta WHERE key = 'reader_compat'` (absent means 1). If it
  is higher than the reader supports (Studio supports 1), do not read the library;
  say HelioFITS Core is newer than Studio. Do **not** compare `schema_version`: the schema
  can grow (new tables, new columns) without changing what readers see, and only
  `reader_compat` changes when it would break them.
- The index is in WAL mode; the writer can work while you read. Keep each read
  short and close ResultSets promptly: an open read holds a snapshot and stops the
  WAL from being checkpointed.

## What to read (reader_compat 1)

`current_files`, one row per observation, its newest version:

| column | meaning |
| --- | --- |
| `file_id` | stable id of the file |
| `relpath` | path relative to `library_root`; always resolve through this, never through view folders |
| `sha256`, `size`, `version` | checksum, bytes, file version (FILEVRSN or the filename's `_vNN`) |
| `observation_id`, `date_obs` | the observation; `date_obs` is ISO 8601 UTC text and sorts as time |
| `dataset_id`, `dataset_key`, `label` | the dataset; `label` is for display, `dataset_key` for grouping |
| `observatory`, `instrument`, `detector`, `product`, `level`, `wavelength` | the parts of the key; any can be NULL |

`current_files` leaves out rejected observations: frames a person set aside in
HelioFITS Core's cache editor (`library.reject` in [the API](api.md)). Their files stay in
the store, and unrejecting one brings it back. Schema 4 added this; the columns are
unchanged, so it is still reader_compat 1, and a reader simply stops seeing a
rejected frame.

Also stable at reader_compat 1: `datasets`, `views(name, description)` and
`view_members(view_id, observation_id)` (join to `current_files` on `observation_id`).
Other tables are HelioFITS Core's own and can change.

Schema 5 (still reader_compat 1) adds stable ids. `datasets.dataset_uid` and
`views.view_uid` are UUIDs that never change, and `current_files` gains
`dataset_uid` as its last column; every earlier column is unchanged. A
`dataset_key` can change when HelioFITS Core's header rules do (a reindex then renames the
dataset), but its `dataset_uid` stays, and `dataset_aliases(old_key, dataset_uid)`
maps each key it had before. A reader that saved a dataset should save its
`dataset_uid`, and look a saved key up in `datasets` first, then in
`dataset_aliases`. `file_aliases(old_relpath, file_id, sha256)` does the same for a
stored file whose `relpath` changed (always resolve through `files.relpath`). Links
to a dataset take the form `hfcore://dataset/<dataset_uid>?start=<iso>&end=<iso>`.
Check for these columns and tables before using them: a library not yet opened by
a HelioFITS Core with schema 5 does not have them.

Schema 6 (still reader_compat 1) adds `file_sources(url, file_id, recorded_at)`: every URL a stored
file arrived from, a duplicate download's included. A URL that later delivered other bytes points at
the newer file. A program that downloads by URL finds what it already has with
`SELECT f.relpath FROM file_sources s JOIN files f ON f.id = s.file_id WHERE s.url = ?`, so it need
not keep a copy of its own (Studio, with `ingest.path`'s `consume`). Check for the table first.

Files under `store/` are read-only and never rewritten in place. A new version is
a new file, and `current_files` points at it once its row is committed.

## When to read again

The library changes while Studio runs (a download finishing, a delete, a reindex).
Re-run the queries when the Add Layer dialog opens or regains focus. A path read
earlier can disappear after a delete: treat a missing file as "gone from the
library", re-read, and do not cache paths across dialog openings.

## Studio before and after the HelioFITS Cache Manager app (decision 9, staged)

Studio's own download buttons stay until the HelioFITS Cache Manager app exists, so Studio is never
left unable to get data.

1. **Now:** if a library is found as above, the new-layer dialog adds a "HelioFITS Core
   library" tab read from `current_files` (grouped by `dataset_key`, shown by `label`,
   counted against the master time range). Studio's existing download buttons are
   unchanged. With no library, the dialog is exactly as it is today. Studio's reader is
   `org.helioviewer.jhv.io.FitsLibrary`, checked by `extra/test/FitsLibraryCheck.java`
   (first built as `HeliadLibrary` on branch `claude/heliad-library-tab` in HelioFITS-Studio).
2. **When the HelioFITS Cache Manager app ships:** the dialog shows "Open in HelioFITS Cache Manager" only when the
   app is installed. The app's bundle id is `io.github.gillyspace27.heliad` (kept through the
   rename: a bundle id is never changed), and it handles `hfcore://search?...` and
   `hfcore://get?...` links (and `heliad://`, the old scheme) whose parameters are the query's
   fields (`start`, `end`, `instrument`, `wavelength`, `sample_s`, ...; see
   `Query.to_url`). Studio opens such a link to hand a search to the app; a `get` link
   shows the search with a Download button rather than downloading unasked.
3. ~~**Last:** Studio's download buttons are removed once the app is installed and
   detected, so every search, download and delete goes through HelioFITS Core.~~
   **Withdrawn on 2026-10-20 by Gilly.** Studio keeps every download function, and
   HelioFITS Core manages and upgrades the cache (rejecting frames, refetching one,
   densifying a dataset).

Studio keeps JPIP streaming from Helioviewer (decision 10): it is viewing rather than
downloading, and none of these steps touches it.
