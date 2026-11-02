"""The data verbs Studio needs so a script can do a task, not just press a menu item.

Studio's registry (docs/commands.json) is mostly menu actions; loading a layer opens a dialog and
takes no arguments. These are the commands notebooks and the MCP server need beyond that. They are
a proposal until Studio has them (`x-status`: "proposed"): the fake Studio implements them, so
notebooks run today, and the Studio-side work is to register the same ids with the same schemas.
Ids are camelCase like the rest of the registry. See docs/studio-data-verbs.md.
"""

from __future__ import annotations

_obj = lambda props, req=(): {  # noqa: E731
    "type": "object", "properties": props, "additionalProperties": False, **({"required": list(req)} if req else {})}
_str = lambda d: {"type": "string", "description": d}  # noqa: E731
_int = lambda d: {"type": "integer", "description": d}  # noqa: E731
_num = lambda d: {"type": "number", "description": d}  # noqa: E731
_time = _str("UTC time, ISO 8601 (2024-05-10T12:00 or 2024-05-10T12:00:00.000)")


def _verb(name, description, props, req, out, effect):
    return {
        "name": name, "description": description,
        "inputSchema": _obj(props, req), "outputSchema": _obj(out),
        "annotations": {"readOnlyHint": effect == "read", "destructiveHint": effect == "destructive"},
        "x-effect": effect, "x-status": "proposed"}


VERBS: list[dict] = [
    _verb("layerAdd", "Add an image layer for a dataset over a time range, loading its frames.",
          {"source": {"type": "string", "enum": ["jp2", "fits-library", "punch", "synoptic"],
                      "description": "jp2: Helioviewer pictures; fits-library: the HelioFITS Core cache"},
           "dataset": _str("Dataset label or key, e.g. 'SDO/AIA 193' or a Core dataset key"),
           "start": _time, "end": _time,
           "cadence_s": _num("Wanted spacing in seconds; omit for the dataset's own")},
          ("source", "dataset", "start", "end"),
          {"layer_id": _int("Id for later calls"), "frames": _int("Frames loaded")}, "write"),
    _verb("layerList", "The layers in the scene.", {}, (),
          {"layers": {"type": "array", "items": {"type": "object"}}}, "read"),
    _verb("layerRemove", "Remove a layer from the scene.", {"layer_id": _int("From layerAdd or layerList")},
          ("layer_id",), {}, "destructive"),
    _verb("layerReload", "Re-read a layer's frames, for example after the cache gained files.",
          {"layer_id": _int("From layerAdd or layerList")}, ("layer_id",), {"frames": _int("Frames now")}, "write"),
    _verb("filterSet", "Set a layer's image filter and its parameters.",
          {"layer_id": _int("Layer"), "filter": _str("Filter id, e.g. 'rhef', 'mgn', 'none'"),
           "params": {"type": "object", "description": "The filter's parameters by name"}},
          ("layer_id", "filter"), {}, "write"),
    _verb("viewSet", "Set the view: projection, zoom, box-cox lambda.",
          {"projection": {"type": "string", "enum": ["orthographic", "hpc", "helioradial", "helioradialUnrolled",
                                                       "latitudinal"]},
           "zoom": _num("Field of view scale"), "warp_lambda": _num("Box-Cox radial lambda")}, (), {}, "write"),
    _verb("viewGet", "The current view.", {}, (), {"view": {"type": "object"}}, "read"),
    _verb("frameGet", "Write one frame's decoded values to a file (an hf-decode-dump/1 document, "
                      "docs/decode-parity.md) and return its path.",
          {"layer_id": _int("Layer"), "frame": _int("Frame number, 0 is the first; default the current one")},
          ("layer_id",), {"path": _str("A JSON dump on the shared filesystem"),
                          "format": _str("hf-decode-dump/1")}, "read"),
    _verb("previewRender", "Render the scene as Studio would export it, to a temporary PNG, without saving an export.",
          {"width": _int("Pixels"), "height": _int("Pixels")}, (), {"path": _str("A PNG")}, "read"),
    _verb("exportStill", "Export the current frame as a PNG.",
          {"path": _str("Where to write"), "width": _int("Pixels"), "height": _int("Pixels")},
          ("path",), {"path": _str("Written file")}, "write"),
    _verb("exportMovie", "Export a movie of the loaded frames.",
          {"path": _str("Where to write (.mp4)"), "start": _time, "end": _time, "fps": _num("Frames per second")},
          ("path",), {"path": _str("Written file"), "frames": _int("Frames in it")}, "write"),
]
