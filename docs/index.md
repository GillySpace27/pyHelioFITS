# pyHelioFITS manual

pyHelioFITS is the Python side of the HelioFITS Suite: one package, `pip install pyheliofits`, then
`import pyheliofits as hf`. It finds solar data and keeps it in one library (HelioFITS Core, the
`hfcore` command), drives a running HelioFITS Studio from a script or notebook, replays a Studio export
from its record alone, and offers all of that to an LLM client through an MCP server.

This manual is for version {{ release }}. HelioFITS Studio (the desktop viewer) and HelioFITS Peek (the
macOS Quick Look plugin) are separate downloads at <https://gilly.space> with their own manuals.

```{toctree}
:caption: Using pyHelioFITS
:maxdepth: 2

install
overview
notebooks
mcp
reproduce
studio-data-verbs
```

```{toctree}
:caption: Reference
:maxdepth: 1

api
reader-contract
decode-parity
licences
```

```{toctree}
:caption: Project
:maxdepth: 1

releasing
changelog
citing
```
