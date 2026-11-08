# Notebooks

Six notebooks in [`notebooks/`](https://github.com/GillySpace27/pyHelioFITS/tree/main/notebooks) walk
through the Studio client. Each is also kept as a plain `.py` file (`pct2ipynb.py` in `tools/` makes the
`.ipynb` from it). The tests run them against a fake Studio, so they stay working without a display.

1. **Load and step**: add a layer, step through time, export a still.
2. **Filter and projection sweep**: change filter and projection in a loop and export each.
3. **Fill a cadence gap**: find a gap in a dataset's cadence, fetch it, reload the layer.
4. **Preview then export**: render a small preview, then export stills and movies at full size.
5. **CME height-time**: from an event list to a height-time plot.
6. **Reproduce from a sidecar**: replay a figure from its `.hfrec` record and read the verdict
   (see [Reproducing an export](reproduce.md)).

Notebook 1 also runs against a live Studio.
