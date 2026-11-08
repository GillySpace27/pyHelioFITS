# Security policy

## Supported versions

Only the latest release of pyHelioFITS receives security fixes.

## Reporting a vulnerability

Please do not open a public issue. Report it privately, either with GitHub's "Report a vulnerability"
button on the Security tab of <https://github.com/GillySpace27/pyHelioFITS>, or by email to
gilly@nwra.com. Include the version, what you did and what happened. You will get an answer within
a week.

## What is in scope

- `hfcore serve`, which speaks JSON-RPC on stdin and stdout and writes to the library root.
- `pyheliofits.studio` and `heliofits-mcp`, which send commands to a running HelioFITS Studio over
  its local API (127.0.0.1, bearer token read from `~/HFStudio/api.json`) and, for
  `pyheliofits.reproduce`, start an isolated Studio.
- Reading FITS files, `.hfrec` records and `.data.zip` bundles from untrusted sources.
- The release workflow (`.github/workflows/publish.yml`).

HelioFITS Studio and HelioFITS Peek have their own policies in their own repositories.
