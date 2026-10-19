# Heliad

Find solar data, download it and keep it in one library. Heliad is the "find it and keep it"
half of HelioFITS Software; the HelioFITS apps look at what it keeps.

Status: core library, CONNECT import validated on real files; Fido downloads (AIA 193
through VSO) run against the real archives; the JSOC export route written but not yet run.

## How it fits together

- **Store**: each file kept once, read-only, under `store/<observatory>/<instrument>/.../YYYY/MM/DD/`.
- **Index** (`heliad.sqlite`, WAL mode): the source of truth. Datasets, observations, files
  (SHA-256, version, source URL, the query that found it), header checks, download jobs, views.
  Reprocessed versions (FILEVRSN 0k, 0l) are one observation; `current_files` serves the newest.
- **Views**: named selections such as an event. `views/<name>/manifest.csv` plus a folder of
  relative symlinks, rebuilt on demand. Programs resolve paths through the index, never the links.
- **One writer**: the core holds a lock on the library root. Everything else opens the index
  read-only (`file:...?mode=ro`); `tools/ReadIndex.java` shows the sqlite-jdbc way Studio will.
- **Config**: `config.json` in the platform's app-support folder names the library root, so
  other programs can find the index. Only `heliad init` writes it. `docs/reader-contract.md`
  is what Studio and scripts may rely on.
- **Downloads** run as jobs on a worker thread, each in its own staging folder; a file cut off
  in transit is refused by a size check against its FITS header. A job ends done, partial
  (some files stored) or failed; records a VSO mirror cannot serve are asked for from the
  next one (setting `vso_mirrors`), and a retry fetches only what is still missing.
- **Thumbnails**: `heliad thumb FILE_ID` renders a greyscale preview into `.thumbs/`, once per file
  and size (the app's grid uses 256 px, its frame viewer 1024 px); deleting a file removes them all.
- **Coverage**: `library.coverage` counts each dataset's observations in equal bins of a range,
  for the app's Timeline; an archive search returns its records' times binned the same way
  (JSOC's TAI record times moved to UTC), so the two can be drawn one above the other.
- **Links**: `heliad://search?...` and `heliad://get?...` carry a query (the same field names)
  from another program to the Heliad app.
- **Rules version**: when the rules that turn headers into datasets change, `heliad stats`
  says `reindex_due`, and `heliad reindex` applies them to every file already kept.

## Use

    pip install -e .[archives]
    heliad --library ~/HeliadLibrary import ~/Documents/NWRA/PUNCH_Science/events/2025-09-21_CONNECT
    heliad datasets
    heliad view create "2025-09-21 CONNECT" --start 2025-09-20T00:00 --end 2025-09-23T00:00
    heliad search --start 2025-09-21T16:00 --end 2025-09-21T16:10 --instrument AIA --wavelength 193
    heliad get --start 2025-09-21T16:00 --end 2025-09-21T16:10 --instrument AIA --wavelength 193
    heliad settings jsoc_email you@example.org      # JSOC exports need a registered address
    heliad get --via jsoc --jsoc-series aia.lev1_euv_12s --start ... --end ... --wavelength 193
    heliad reindex --dry-run && heliad reindex
    heliad delete --dataset KEY                     # shows what would go; add --yes to delete
    heliad serve        # JSON-RPC 2.0, one JSON object per line on stdin/stdout
    heliad api          # the methods serve answers

## API

Programs (the Heliad app, HelioFITS Studio, your scripts) change a library through
`heliad serve`, which speaks JSON-RPC 2.0 over stdin and stdout. `heliad api` lists every
method and notification with its params; `heliad api METHOD` shows one. The full reference,
with examples, error codes and how several clients share one library, is
[docs/api.md](docs/api.md). To only read a library, open its index as
[docs/reader-contract.md](docs/reader-contract.md) describes.

## Tests

    pip install -e .[test] && pytest
