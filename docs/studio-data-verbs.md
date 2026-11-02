# Studio data verbs (proposed)

Studio's command registry (`docs/commands.json` in HelioFITS Studio) is mostly menu actions: loading
a layer opens a dialog and takes no arguments. A script or an LLM needs commands that do the task.
These are defined in `src/heliofits/verbs.py` with JSON Schemas, implemented by `heliofits.testing.FakeStudio`
over synthetic frames (so the notebooks run), and are what Studio has to register next, with the same
ids and schemas, so the fake and the real thing agree. Ids are camelCase like the rest of the registry.

| id | effect | what it does |
| --- | --- | --- |
| `layerAdd` | write | add a layer: `source` (jp2, fits-library, punch, synoptic), `dataset`, `start`, `end`, `cadence_s`; returns `layer_id`, `frames` |
| `layerList` | read | the layers: id, name, source, frames, start, end, filter |
| `layerRemove` | destructive | remove a layer |
| `layerReload` | write | re-read a layer's frames (after the cache gained files) |
| `filterSet` | write | `layer_id`, `filter`, `params` |
| `viewSet`, `viewGet` | write, read | projection, zoom, Box-Cox `warp_lambda` |
| `frameGet` | read | one frame's decoded values as an `hf-decode-dump/1` document (docs/decode-parity.md); returns the file's path |
| `previewRender` | read | render the scene to a temporary PNG, no export saved |
| `exportStill`, `exportMovie` | write | write a PNG, or an .mp4 of the loaded frames |

Also needed in Studio, not in this list: the localhost listener that serves `commands.list` and
`commands.invoke` (see docs/mcp.md), and `seekTime`/`seekFrame` already exist.

The fake gives a layer one synthetic frame per cadence step, a smooth disc plus a bright blob that
moves outward with the frame number. Nothing it returns is data.

Open points for the Studio side: what `layerAdd` should do when a dataset name is ambiguous (the fake
accepts anything); whether `frameGet` should also accept a time instead of a frame number; progress
for `layerAdd` and `exportMovie`, which can be slow (the client waits up to 10 minutes by default).
