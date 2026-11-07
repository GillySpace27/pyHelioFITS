# Studio data commands

Studio registers the data commands a script needs (`DataCommands` in HelioFITS Studio, listed in
its `docs/commands.md` and carried by `docs/commands.json`): `layerAdd`, `layerList`,
`layerRemove`, `layerReload`, `filterSet`, `viewSet`, `viewGet`, `frameGet`, `previewRender`,
`exportStill` and `exportMovie`. Studio is the source of their ids and schemas; this package ships
a copy of that file (`src/pyheliofits/data/studio-commands.json`) for offline use, docs and tests,
and a running Studio's own list wins. Nothing is added on the Python side.

`pyheliofits.testing.FakeStudio` answers the same commands over synthetic frames (a smooth disc plus
a bright blob that moves outward with the frame number), with answers of the shape Studio's have,
so the notebooks and `tests/test_notebooks.py` run without Studio. Nothing it returns is data.

Shapes worth knowing: a layer is named by `layer_id`, a small integer that is not stable across
runs; `layerAdd` answers when every frame is loaded (`wait`: `all`, `first` or `none`), so it can
take minutes; `layerList` reports `source` as `jp2`, `fits` or `file`; `frameGet` answers the path
of an `hf-decode-dump/1` document (docs/decode-parity.md) and works for FITS layers; exports take
`long_side` in pixels and read Studio's recording settings without changing them. A jp2 dataset is
named as the server lists it, `observatory="SDO", dataset="AIA 193"`.

Not in Studio yet: a filter's own parameters, a frame range or rate on `exportMovie`, `frameGet`
for jp2 layers, and SOAR, ASPIICS, synoptic or PUNCH-by-query sources on `layerAdd`.
