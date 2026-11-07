"""The Python side of the HelioFITS Suite: the data cache engine (HelioFITS Core, `pyheliofits.core`),
a client for a running HelioFITS Studio (`pyheliofits.studio`), the cache verbs for scripts
(`pyheliofits.cache`), and the MCP server over both (`heliofits-mcp`).

    from pyheliofits import core, studio, cache
    studio.seek_time(time="2024-05-10T12:00")
    cache.library_stats()
"""

from . import cache, core, studio
from .core import __version__
from .errors import CommandError, StudioNotRunning

__all__ = ["cache", "core", "studio", "__version__", "CommandError", "StudioNotRunning"]
