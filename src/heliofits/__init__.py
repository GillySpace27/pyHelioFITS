"""HelioFITS Python client: one code path to Studio and the cache, for notebooks, scripts,
the MCP server (`heliofits-mcp`) and the `hfs` command.

    from heliofits import studio
    studio.seek_time("2024-05-10T12:00")
"""

from . import cache, studio
from .errors import CommandError, StudioNotRunning

__all__ = ["cache", "studio", "CommandError", "StudioNotRunning"]
