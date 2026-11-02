"""The Python side of HelioFITS: the data cache engine (HelioFITS Core, `heliofits.core`), a client
for a running HelioFITS Studio (`heliofits.studio`), the cache verbs for scripts (`heliofits.cache`),
and the MCP server over both (`heliofits-mcp`).

    from heliofits import core, studio, cache
    studio.seek_time(time="2024-05-10T12:00")
    cache.library_stats()
"""

from . import cache, core, studio
from .core import __version__
from .errors import CommandError, StudioNotRunning

__all__ = ["cache", "core", "studio", "__version__", "CommandError", "StudioNotRunning"]
