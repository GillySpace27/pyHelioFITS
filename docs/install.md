# Install

```
$ pip install pyheliofits
$ hfcore --help
```

Python 3.10 to 3.13, on macOS, Linux and Windows. The base install needs only `astropy`, `numpy` and
`jsonschema`. Extras add what a feature needs:

| Extra | Adds | For |
|---|---|---|
| `archives` | sunpy, drms | searching and downloading from VSO and JSOC |
| `mcp` | the `mcp` SDK | the `heliofits-mcp` server |
| `reproduce` | pillow | `pyheliofits.reproduce` |
| `parity` | sunpy[map] | the sunpy decode-parity check |
| `test` | pytest | running the tests |

For example `pip install "pyheliofits[archives,mcp]"`.

The Studio client and `reproduce` need a HelioFITS Studio on the same computer, with its local API turned
on (Settings > General > Local API). Install it from <https://gilly.space>. The Studio installer already
carries its own copy of HelioFITS Core, so you do not need this package just to use Studio.

From a clone: `pip install -e ".[archives,test,mcp,reproduce]"` then `pytest`.
