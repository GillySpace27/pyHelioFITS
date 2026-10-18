# Reading a Heliad library from another program

This is what HelioFITS Studio (and any script) may rely on. Heliad's core is the
only writer; everything else follows these rules and only reads.

## Find the library

1. Read `config.json` in Heliad's app-support folder:
   - macOS: `~/Library/Application Support/Heliad/config.json`
   - Linux: `$XDG_CONFIG_HOME/heliad/config.json` (default `~/.config/heliad/`)
   - Windows: `%APPDATA%\Heliad\config.json`
   - `HELIAD_HOME`, when set, replaces the folder on every platform.
2. It holds `library_root` and `db_path`. Only `heliad init` (or the first run on a
   machine with no config) writes it, so a one-off `heliad --library` never moves it.
3. No config file, or no file at `db_path`: there is no library. Show nothing from
   Heliad; do not create anything.

## Open the index

- Open `db_path` read-only: `jdbc:sqlite:file:<db_path>?mode=ro` (sqlite-jdbc), or
  `sqlite3.connect("file:<db_path>?mode=ro", uri=True)`. Set `PRAGMA busy_timeout = 5000`.
- Read `SELECT value FROM meta WHERE key = 'reader_compat'` (absent means 1). If it
  is higher than the reader supports (Studio supports 1), do not read the library;
  say Heliad is newer than Studio. Do **not** compare `schema_version`: the schema
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

Also stable at reader_compat 1: `datasets`, `views(name, description)` and
`view_members(view_id, observation_id)` (join to `current_files` on `observation_id`).
Other tables are Heliad's own and can change.

Files under `store/` are read-only and never rewritten in place. A new version is
a new file, and `current_files` points at it once its row is committed.

## When to read again

The library changes while Studio runs (a download finishing, a delete, a reindex).
Re-run the queries when the Add Layer dialog opens or regains focus. A path read
earlier can disappear after a delete: treat a missing file as "gone from the
library", re-read, and do not cache paths across dialog openings.

## Studio before and after the Heliad app (decision 9, staged)

Studio's own download buttons stay until the Heliad app exists, so Studio is never
left unable to get data.

1. **Now:** if a library is found as above, the new-layer dialog adds a "Heliad
   cache" list read from `current_files` (grouped by `label`, filtered by time).
   Studio's existing download buttons are unchanged. With no library, the dialog is
   exactly as it is today.
2. **When the Heliad app ships:** the dialog shows "Open in Heliad" only when the app
   is installed. How Studio detects it (a bundle id lookup, or an entry the app adds
   to `config.json`) is decided when the app is built; nothing in this contract
   depends on it yet.
3. **Last:** Studio's download buttons are removed once the app is installed and
   detected, so every search, download and delete goes through Heliad.

Whether Studio keeps JPIP streaming from Helioviewer is still open (the recommendation
is to keep it, since it is viewing rather than downloading); none of these steps
touches it.
