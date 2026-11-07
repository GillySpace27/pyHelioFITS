# pyHelioFITS

The Python side of the HelioFITS Suite, one package: `pip install pyheliofits`, then `import pyheliofits as hf`.

    from pyheliofits import core, studio, cache

- **HelioFITS Core** (`pyheliofits.core`, command `hfcore`): find solar data, download it and keep it
  in one library. The "find it and keep it" half of the HelioFITS Suite; HelioFITS Peek and HelioFITS
  Studio look at what it keeps. Most of this file is about it.
- **`pyheliofits.studio`**: a client for a running HelioFITS Studio, and **`pyheliofits.cache`**: the
  library's cache verbs from a script. Both for notebooks, with `heliofits-mcp` wrapping them for an
  LLM client ([docs/mcp.md](https://github.com/GillySpace27/pyHelioFITS/blob/main/docs/mcp.md)).

What this package is not: HelioFITS Studio (the desktop viewer) and HelioFITS Peek (the macOS
Quick Look plugin) are separate downloads at <https://gilly.space>. This package is what a script,
notebook or LLM client uses to work with the same library those two read.

Three names from before HelioFITS Core was renamed are kept on purpose, and only these: the
library's `.heliad.lock` and `.heliad.endpoint` files (the core HelioFITS Studio bundles and this
one must find the same lock), the `heliad_*` keys of the processing record (a published format,
append-only), and the Cache Manager app's bundle id.

Status: core library, CONNECT import validated on real files; Fido downloads (AIA 193
through VSO) run against the real archives; the JSOC export route written but not yet run.

## How it fits together

- **Store**: each file kept once, read-only, under `store/<observatory>/<instrument>/.../YYYY/MM/DD/`.
- **Index** (`hfcore.sqlite`, WAL mode): the source of truth. Datasets, observations, files
  (SHA-256, version, source URL, the query that found it), header checks, download jobs, views.
  Reprocessed versions (FILEVRSN 0k, 0l) are one observation; `current_files` serves the newest.
- **Views**: named selections such as an event. `views/<name>/manifest.csv` plus a folder of
  relative symlinks, rebuilt on demand. Programs resolve paths through the index, never the links.
- **One writer**: the core holds a lock on the library root. Everything else opens the index
  read-only (`file:...?mode=ro`); `tools/ReadIndex.java` shows the sqlite-jdbc way Studio will.
- **Config**: `config.json` in the platform's app-support folder names the library root, so
  other programs can find the index. Only `hfcore init` writes it. `docs/reader-contract.md`
  is what Studio and scripts may rely on.
- **Downloads** run as jobs on a worker thread, each in its own staging folder; a file cut off
  in transit is refused by a size check against its FITS header. A job ends done, partial
  (some files stored) or failed; records a VSO mirror cannot serve are asked for from the
  next one (setting `vso_mirrors`), and a retry fetches only what is still missing.
- **Thumbnails**: `hfcore thumb FILE_ID` renders a greyscale preview into `.thumbs/`, once per file
  and size (the app's grid uses 256 px, its frame viewer 1024 px); deleting a file removes them all.
- **Coverage**: `library.coverage` counts each dataset's observations in equal bins of a range,
  for the app's Timeline; an archive search returns its records' times binned the same way
  (JSOC's TAI record times moved to UTC), so the two can be drawn one above the other.
- **Links**: `hfcore://search?...` and `hfcore://get?...` carry a query (the same field names)
  from another program to the HelioFITS Cache Manager app, and `hfcore://dataset/<uid>?start=&end=` names one
  dataset over a range; `link.parse` reads either.
- **Cache editing**: `reject` sets bad frames aside (readers stop seeing them), `refetch` asks
  the archive again for one, `cadences` compares a dataset's cadence with the archive's, and
  `upgrade` fetches only the frames a dataset lacks. Each is a command and a method.
- **Rules version**: when the rules that turn headers into datasets change, `hfcore stats`
  says `reindex_due`, and `hfcore reindex` applies them to every file already kept.

## Use

    pip install -e ".[archives]"
    hfcore --library ~/HelioFITSLibrary import ~/Documents/NWRA/PUNCH_Science/events/2025-09-21_CONNECT
    hfcore datasets
    hfcore view create "2025-09-21 CONNECT" --start 2025-09-20T00:00 --end 2025-09-23T00:00
    hfcore search --start 2025-09-21T16:00 --end 2025-09-21T16:10 --instrument AIA --wavelength 193
    hfcore get --start 2025-09-21T16:00 --end 2025-09-21T16:10 --instrument AIA --wavelength 193
    hfcore settings jsoc_email you@example.org      # JSOC exports need a registered address
    hfcore get --via jsoc --jsoc-series aia.lev1_euv_12s --start ... --end ... --wavelength 193
    hfcore coverage --start 2025-09-21T16:00 --end 2025-09-21T17:00 --bins 12
    hfcore reject 42 --reason "smeared by a flare"  # readers stop seeing it; unreject brings it back
    hfcore cadences sdo/aia/-/-/1/193               # the archive's cadence against the library's
    hfcore tag-bodies && hfcore bodies --without moon  # frames whose field holds no Moon (needs sunpy)
    hfcore upgrade sdo/aia/-/-/1/193 --sample 12    # shows the plan; add --yes to fetch what is missing
    hfcore reindex --dry-run && hfcore reindex
    hfcore latest sdo/aia/-/-/1/193 --span 2h      # fetch the 2 h before the archive's newest frame
    hfcore template refresh session.jhv --dry-run  # the same for a Studio session's library layers
    hfcore delete --dataset KEY                     # shows what would go; add --yes to delete
    hfcore serve        # JSON-RPC 2.0, one JSON object per line on stdin/stdout
    hfcore api          # the methods serve answers
    hfcore --json stats # any command: the JSON-RPC result instead of a table

Every time a command, a query or an archive hands the core is read by `pyheliofits.core.times` (ISO 8601
with T or a space, with or without Z or an offset, JSOC's `_TAI` form) and written in the index's
own form, `2025-09-21T16:00:00.000` UTC.

## API

Programs (the HelioFITS Cache Manager app, HelioFITS Studio, your scripts) change a library through
`hfcore serve`, which speaks JSON-RPC 2.0 over stdin and stdout. `hfcore api` lists every
method and notification with its params; `hfcore api METHOD` shows one. The full reference,
with examples, error codes and how several clients share one library, is
[docs/api.md](https://github.com/GillySpace27/pyHelioFITS/blob/main/docs/api.md). To only read a library, open its index as
[docs/reader-contract.md](https://github.com/GillySpace27/pyHelioFITS/blob/main/docs/reader-contract.md) describes.

## Tests

    pip install -e .[test] && pytest
