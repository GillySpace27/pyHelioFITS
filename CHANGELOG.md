# Changelog

Versions follow `pyproject.toml`. The dates are release dates.

## 0.1.0 (2026-11-08, first PyPI release)

The first release under the name `pyheliofits` (import `pyheliofits`, commands `hfcore` and
`heliofits-mcp`). The code was developed as the core of the HelioFITS Cache Manager before this.

### Library (HelioFITS Core, `pyheliofits.core`)
- An indexed, read-only store of solar FITS files: each file kept once, SHA-256 and version recorded,
  reprocessed versions grouped as one observation, header checks, named views and thumbnails.
- Import (CONNECT, PUNCH, ASPIICS, AIA, LASCO), and archive search and download through VSO and JSOC,
  with partial and failed jobs reported and other VSO mirrors tried.
- One writer per library, shared by other clients through its lock and endpoint; JSON-RPC 2.0 over
  `hfcore serve` and a command line (`hfcore api` lists every method). See `docs/api.md`.
- Stable dataset ids, cache editing (reject, refetch, cadences, upgrade), `layer.upgrade` and
  `template.latest` for Studio sessions, `.hfrec` sidecar reading, and a report of a processing record.
- Tests run on Linux, macOS and Windows, Python 3.10 to 3.13.

### Studio client and MCP server
- `pyheliofits.studio`: a client for a running HelioFITS Studio over its local API.
  `pyheliofits.cache`: the library's cache verbs in-process. A fake Studio runs the notebooks in tests.
- `heliofits-mcp`: one stdio MCP server over both (`studio_*` and `cache_*` tools; `--read-only`,
  `--include-ui`). See `docs/mcp.md`.

### Reproducing an export
- hfrecord 1.1 keys, `record.locate` (find files by SHA-256), `hfcore record digest` and
  `hfcore record verify`, and `pyheliofits.reproduce`, which replays a record in an isolated Studio and
  judges it: identical, equivalent, different or cannot_check. First real replay: identical, one
  machine, one GPU; no cross-GPU tolerance is claimed. See `docs/reproduce.md`.

### Notebooks and docs
- Six notebooks (load and step, filter and projection sweep, fill a cadence gap, preview then export,
  CME height-time, reproduce from a sidecar) and the manual under `docs/`.
- The sunpy decode-parity test (`docs/decode-parity.md`).
- Packaging: MPL-2.0, trusted-publishing workflow (`docs/releasing.md`), `CITATION.cff`, `.zenodo.json`.
