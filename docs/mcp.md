# heliofits: Python client and MCP server

`pip install "heliofits-core[mcp]"` gives one package with three front ends over the same code:

    from heliofits import studio, cache
    studio.seek_time(time="2024-05-10T12:00")   # same as studio.seekTime(...)
    cache.library_stats()                        # runs Core's library.stats in process

    heliofits-mcp                                # MCP server over stdio, for an LLM client

## Studio endpoint

Studio writes `~/HFStudio/api.json` (`{"port": ..., "token": ...}`) when its scripting API
setting is on; `HFSTUDIO_API` names another file. The client speaks JSON-RPC 2.0 over HTTP to
`127.0.0.1:<port>` with `Authorization: Bearer <token>`, methods `commands.list` and
`commands.invoke {id, args}`. Errors carry `data.kind`: unknown-command, invalid-arguments,
disabled, failed, declined. Destructive commands show a confirmation dialog in Studio.
The listener is Studio-side work (not yet built); `heliofits.testing.FakeStudio` implements the
same protocol for tests and for trying notebooks.

`heliofits/data/studio-commands.json` is a copy of Studio's `docs/commands.json`, used when Studio
is not running. The client uses a running Studio's own list when there is one.

## MCP tools

`studio_<command>` for each Studio command, `cache_<method>` for each Core method (dots become
underscores: `cache_library_stats`). Hints: read-only, destructive. Options: `--read-only` hides every
tool that changes anything; `--include-ui` adds Studio's menu and dialog commands.

Client config example: `{"mcpServers": {"heliofits": {"command": "heliofits-mcp"}}}`.

## Notebooks

`notebooks/*.py` (percent format, the source) and `*.ipynb` (built by `python tools/pct2ipynb.py`):

1. load a dataset and step through time
2. sweep a view parameter and export a still for each value
3. find and fill a cadence gap in the cache, then reload the layer
4. preview, then export stills and a movie
5. beyond the GUI: a height-time curve for each event in a list

They need data verbs Studio does not have yet (docs/studio-data-verbs.md). Until it does, try them on
the fake: `python -m heliofits.testing`, then `export HFSTUDIO_API=<the file it prints>`. The fake's
frames are synthetic. `tests/test_notebooks.py` runs every notebook against it.

`cache` calls that queue a download (`dataset_upgrade`, `download_enqueue`, `library_refetch`) are run
to the end in your process and return the finished job; pass `_wait=False` to `cache.call` to queue
and return, for a separate `hfcore serve` or the app to do.
