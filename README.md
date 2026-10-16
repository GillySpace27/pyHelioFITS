# Heliad

Find solar data, download it and keep it in one library. Heliad is the "find it and keep it"
half of HelioFITS Software; the HelioFITS apps look at what it keeps.

Status: milestone 1 of the build plan (core library). Not yet tested against real archives.

## How it fits together

- **Store**: each file kept once, read-only, under `store/<observatory>/<instrument>/.../YYYY/MM/DD/`.
- **Index** (`heliad.sqlite`, WAL mode): the source of truth. Datasets, observations, files
  (SHA-256, version, source URL, the query that found it), header checks, download jobs, views.
  Reprocessed versions (FILEVRSN 0k, 0l) are one observation; `current_files` serves the newest.
- **Views**: named selections such as an event. `views/<name>/manifest.csv` plus a folder of
  relative symlinks, rebuilt on demand. Programs resolve paths through the index, never the links.
- **One writer**: the core holds a lock on the library root. Everything else opens the index
  read-only (`file:...?mode=ro`); `tools/ReadIndex.java` shows the sqlite-jdbc way Studio will.
- **Config**: `config.json` in the platform's app-support folder names the library root and
  schema version, so other programs can find the index.

## Use

    pip install -e .[archives]
    heliad --library ~/HeliadLibrary import ~/Documents/NWRA/PUNCH_Science/events/2025-09-21_CONNECT
    heliad datasets
    heliad view create "2025-09-21 CONNECT" --start 2025-09-20T00:00 --end 2025-09-23T00:00
    heliad search --start 2025-09-21T16:00 --end 2025-09-21T16:10 --instrument AIA --wavelength 193
    heliad serve        # JSON-RPC 2.0, one JSON object per line on stdin/stdout

## Tests

    pip install -e .[test] && pytest
