"""Client for the HelioFITS Core cache: the methods `hfcore serve` answers, called in process,
so it works with Studio closed. `cache.library_stats()` runs `library.stats`; `cache.call(name,
**params)` runs any method by its full name. If another process holds the library's writer lock
the library opens read-only, and a write fails with Core's READ_ONLY error."""

from __future__ import annotations

import io
from typing import Any

from pyheliofits.core import config, rpc
from pyheliofits.core.library import Library, LibraryLocked

from .errors import CommandError


def methods() -> dict[str, tuple[str, str]]:
    """method -> (params, what it does), from Core's own API table."""
    return dict(rpc.API)


def _open() -> Library:
    root = config.load().library_root
    try:
        return Library.open(root)
    except LibraryLocked:
        return Library.open(root, readonly=True)


def call(method: str, _wait: bool = True, **params: Any) -> Any:
    """Run one Core method. A method that queues a download (download.enqueue, dataset.upgrade,
    library.refetch) is run to the end here, because nothing else in this process would run it;
    the result is the finished job. Pass `_wait=False` to get the queued job back instead (a
    separate `hfcore serve` or the Cache Manager app then does the work)."""
    lib = _open()
    server = rpc.Server(lib, out=io.StringIO())
    try:
        name = rpc.ALIASES.get(method, method)
        if name not in server.methods:
            raise CommandError("unknown-command", f"no cache method '{method}'")
        reply = server._call(1, server.methods[name], params)
        if "error" in reply:
            raise CommandError("failed", f"{reply['error']['code']}: {reply['error']['message']}")
        result = reply["result"]
        if _wait and server.jobs is not None and isinstance(result, dict) and result.get("status") == "queued" \
                and "job_id" in result:
            server.jobs.run_pending()
            result = server.jobs.get(result["job_id"])
        return result
    finally:
        server._pool.shutdown(wait=False)
        lib.close()


def __getattr__(name: str):
    if name.startswith("_"):
        raise AttributeError(name)
    return lambda **params: call(name.replace("_", ".", 1), **params)
