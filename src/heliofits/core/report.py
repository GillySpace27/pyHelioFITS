"""Readable renderings of a processing record (hfrecord): a plain text report, a methods
paragraph, the same as LaTeX, and a Python script that opens the same sources.

The text report is the reference for the `report` block that writers put at the top of a
sidecar and for the Quick Look preview in HelioFITS Peek: everything a scientist needs to know
about every layer, as lines of text, with the long file list reduced to a count, the first and
the last file (`full=True` lists every file).
"""

from __future__ import annotations

import re
from typing import Any

from . import record as hfrecord

FORMATS = ("text", "methods", "tex", "python", "json")
WIDTH = 100

_TEX_SPECIALS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                 "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_PARAM_NOISE = {"None", "null", ""}


def _tex(text: str) -> str:
    return "".join(_TEX_SPECIALS.get(c, c) for c in text)


def _kv(pairs: list[dict]) -> str:
    return ", ".join(f"{p['name']}={p['value']}" for p in pairs if p["value"] not in _PARAM_NOISE)


def _size(n: Any) -> str:
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return ""
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def _wrap(label: str, text: str, indent: int) -> list[str]:
    """label then text on the first line; continuation lines hang under the text."""
    pad = " " * indent
    first = f"{pad}{label}"
    start = max(len(first) + 1, indent + 11)
    words, lines, cur = text.split(), [], first.ljust(start)
    for w in words:
        if len(cur) + len(w) + 1 > WIDTH and cur.strip() != "":
            lines.append(cur.rstrip())
            cur = " " * start
        cur += ("" if cur.endswith(" ") else " ") + w
    lines.append(cur.rstrip())
    return lines


def _layer_facts(layer: dict) -> dict:
    ds = layer.get("dataset") if isinstance(layer.get("dataset"), dict) else {}
    sources = [s for s in layer.get("sources") or [] if isinstance(s, dict)]
    return {"ds": ds, "sources": sources}


def _dataset_line(ds: dict) -> str:
    bits = [ds.get("observatory"), ds.get("instrument"), ds.get("detector")]
    head = " ".join(str(b) for b in bits if b)
    extra = []
    if ds.get("product"):
        extra.append(f"product {ds['product']}")
    if ds.get("level"):
        extra.append(f"level {ds['level']}")
    if isinstance(ds.get("wavelength"), (int, float)):
        w = ds["wavelength"]
        extra.append(f"{w:g} nm" if w < 100 or ds.get("wavelength_unit") == "nm" else f"{w:g} Å")
    return ", ".join([head] * bool(head) + extra)


def text(record: dict, *, full: bool = False) -> str:
    """The plain text report: one block for the file, one for the scene, one per layer."""
    view = hfrecord.display(record)
    out = ["HELIOFITS PROCESSING RECORD", ""]
    out += _wrap("Made by:", view["producer"] or "unknown program", 0)
    if view["created"]:
        out += _wrap("Created:", view["created"], 0)
    if view["dirty"]:
        out += _wrap("Warning:", "made by a build with uncommitted changes", 0)
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    if output:
        bits = [output.get("kind"), output.get("file_name"), output.get("media_type")]
        size = f"{output['width']} x {output['height']} px" if output.get("width") and output.get("height") else None
        bits += [size, f"{output['bit_depth']}-bit" if output.get("bit_depth") else None, output.get("colorspace"),
                 "display-referred (pixel values are colours)" if output.get("display_referred") else None]
        out += _wrap("Output:", ", ".join(str(b) for b in bits if b), 0)
        if output.get("sha256"):
            out += _wrap("SHA-256:", str(output["sha256"]), 0)
    if view["scene"]:
        out += _wrap("Scene:", _kv(view["scene"]), 0)
    layers = view["layers"]
    out += ["", f"LAYERS ({len(layers)}, top layer first)", ""]
    raw_layers = [x for x in record.get("layers") or [] if isinstance(x, dict)]
    for pos, row in enumerate(layers, 1):
        raw = raw_layers[row["drawn"]] if row["drawn"] < len(raw_layers) else {}
        facts = _layer_facts(raw)
        out.append(f"{pos}. {row['dataset_label'] or row['name'] or 'unnamed layer'}"
                   + ("" if row["enabled"] else "  [hidden]"))
        dline = _dataset_line(facts["ds"])
        if dline:
            out += _wrap("Dataset:", dline, 4)
        if row["dataset_key"]:
            out += _wrap("Key:", row["dataset_key"], 4)
        out += _wrap("Data:", _data_line(row, facts["sources"], raw), 4)
        out += _files(facts["sources"], raw, full)
        if row["steps"]:
            out.append("    Processing, in order applied:")
            for s in row["steps"]:
                out += _step_lines(s)
        for c in row["caveats"]:
            out += _wrap("Caveat:", c, 4)
        if row["acknowledgement"]:
            out += _wrap("Credit:", row["acknowledgement"], 4)
        out.append("")
    if view["caveats"]:
        out.append("CAVEATS")
        for c in view["caveats"]:
            out += _wrap("-", c, 2)
        out.append("")
    if view["citations"]:
        out.append("PLEASE CITE")
        for c in view["citations"]:
            out += _wrap("-", c, 2)
        out.append("")
    if view["not_guaranteed_to_rebuild"]:
        out += _wrap("Rebuild:", "; ".join(view["not_guaranteed_to_rebuild"]), 0)
    if view["record_id"]:
        out += _wrap("Record:", f"{view['record_id']} (hfrecord {view['format']})", 0)
    return "\n".join(out).rstrip() + "\n"


def _data_line(row: dict, sources: list[dict], layer: dict) -> str:
    n = row["frames"]
    bits = [f"{n} frame{'s' if n != 1 else ''}"]
    times = sorted(str(s["date_obs"]) for s in sources if s.get("date_obs"))
    if times:
        bits.append(times[0] if times[0] == times[-1] else f"{times[0]} to {times[-1]}")
    versions = sorted({str(s["file_version"]) for s in sources if s.get("file_version")})
    if versions:
        bits.append("file version " + ", ".join(versions))
    total = sum(s["size"] for s in sources if isinstance(s.get("size"), int))
    if total:
        bits.append(_size(total))
    if row["shortened"]:
        bits.append("source list shortened in the record")
    spec = row["source_spec"]
    if spec:
        bits.append(f"source: {spec}")
    return "; ".join(bits)


def _files(sources: list[dict], layer: dict, full: bool) -> list[str]:
    if not sources:
        return []
    name = lambda s: s.get("original_name") or s.get("source_url") or (s.get("sha256") or "")[:12]  # noqa: E731
    if full or len(sources) <= 3:
        lines = ["    Files:"] + [f"      {name(s)}" + (f"  sha256 {s['sha256'][:12]}" if s.get("sha256") else "")
                                  for s in sources]
        return lines
    missing = sum(1 for s in sources if s.get("in_library") is False)
    first, last = sources[0], sources[-1]
    lines = _wrap("Files:", f"{len(sources)} files, first {name(first)}, last {name(last)}"
                  + (f"; {missing} not found in the library" if missing else "")
                  + " (hfcore record show --full lists every file)", 4)
    if layer.get("source_set_sha256"):
        lines += _wrap("Set hash:", f"sha256 {layer['source_set_sha256']}", 4)
    return lines


def _step_lines(s: dict) -> list[str]:
    head = f"      {s['index']}. {s['op']}"
    if s["stage"]:
        head += f" ({s['stage']})"
    if s["algorithm"]:
        head += f": {s['algorithm']}"
    if s["implementation"]:
        head += f" via {s['implementation']}"
    if s["automatic"]:
        head += " [automatic]"
    if not s["known_op"]:
        head += " [step not known to this reader]"
    lines = [head]
    for label, pairs in (("parameters", s["params"]), ("resolved", s["resolved"]), ("units", s["units"])):
        body = _kv(pairs)
        if body:
            lines += _wrap(f"{label}:", body, 10)
    if s["reference"]:
        lines += _wrap("reference:", s["reference"], 10)
    if s["varies"]:
        lines += _wrap("varies:", ", ".join(s["varies_params"]) or "over the movie", 10)
    for key in ("caveat", "citation"):
        if s[key]:
            lines += _wrap(f"{key}:", s[key], 10)
    return lines


# ---- methods paragraph and LaTeX ---------------------------------------------------------


def methods(record: dict) -> dict:
    """A methods paragraph with the list of things to cite: {"paragraph": str, "citations": [str]}."""
    view = hfrecord.display(record)
    raw_layers = [x for x in record.get("layers") or [] if isinstance(x, dict)]
    parts = []
    program = view["producer"] or "an unknown program"
    when = (record.get("scene") or {}).get("time") or view["created"] or ""
    parts.append(f"The image was produced with {program}" + (f" for {when}" if when else "") + ".")
    for row in reversed(view["layers"]):
        raw = raw_layers[row["drawn"]] if row["drawn"] < len(raw_layers) else {}
        facts = _layer_facts(raw)
        times = sorted(str(s["date_obs"]) for s in facts["sources"] if s.get("date_obs"))
        span = "" if not times else (f" at {times[0]}" if times[0] == times[-1] else f" from {times[0]} to {times[-1]}")
        n = row["frames"]
        sentence = f"{row['dataset_label'] or row['name']} ({n} frame{'s' if n != 1 else ''}{span})"
        applied = []
        for s in row["steps"]:
            if s["algorithm"] and s["algorithm"] not in ("None",):
                applied.append(f"{s['algorithm']} ({s['op'].replace('_', ' ')})")
        if applied:
            sentence += " was processed with " + ", ".join(applied)
        else:
            sentence += " was shown without filtering"
        parts.append(sentence + ".")
    if view["caveats"]:
        parts.append(" ".join(c if c.endswith(".") else c + "." for c in view["caveats"]))
    for ack in view["acknowledgements"]:
        parts.append(ack["text"])
    return {"paragraph": " ".join(parts), "citations": view["citations"]}


def tex(record: dict) -> str:
    m = methods(record)
    lines = ["% Generated by hfcore record show --format tex from a HelioFITS processing record.", "",
             _tex(m["paragraph"]), ""]
    if m["citations"]:
        lines.append("\\begin{itemize}")
        lines += [f"  \\item {_tex(c)}" for c in m["citations"]]
        lines.append("\\end{itemize}")
    return "\n".join(lines) + "\n"


# ---- a Python script ---------------------------------------------------------------------


def python(record: dict) -> str:
    """A starting script: opens the same source files with sunpy and lists the processing steps.

    It does not promise the same pixels: the steps are comments and data, not a re-implementation.
    """
    view = hfrecord.display(record)
    raw_layers = [x for x in record.get("layers") or [] if isinstance(x, dict)]
    lines = ['"""Generated by hfcore record show --format python from a HelioFITS processing record.',
             "", *(view["summary"].splitlines()), "",
             "This opens the same source files. It does not re-run the processing: the steps below are",
             'what the record says was applied, in order."""', "", "import sunpy.map", ""]
    for row in reversed(view["layers"]):
        raw = raw_layers[row["drawn"]] if row["drawn"] < len(raw_layers) else {}
        facts = _layer_facts(raw)
        var = re.sub(r"\W+", "_", (row["dataset_label"] or row["name"] or "layer").lower()).strip("_") or "layer"
        if var[0].isdigit():
            var = "layer_" + var
        lines.append(f"# {row['dataset_label'] or row['name']}")
        names = [s.get("original_name") for s in facts["sources"] if s.get("original_name")]
        lines.append(f"{var}_files = [")
        lines += [f"    {n!r}," for n in names]
        lines.append("]  # find them by name or sha256 in your library: hfcore files --dataset " + str(row["dataset_key"]))
        hashes = [s.get("sha256") for s in facts["sources"] if s.get("sha256")]
        lines.append(f"{var}_sha256 = [")
        lines += [f"    {h!r}," for h in hashes]
        lines.append("]")
        lines.append(f"# {var}_maps = [sunpy.map.Map(path) for path in {var}_files]")
        lines.append(f"{var}_steps = [")
        for s in row["steps"]:
            params = {p["name"]: p["value"] for p in s["params"]}
            lines.append(f"    {{'op': {s['op']!r}, 'algorithm': {s['algorithm']!r}, 'params': {params!r}}},")
        lines.append("]")
        lines.append("")
    return "\n".join(lines)


def render(record: dict, fmt: str = "text", *, full: bool = False) -> str:
    import json
    if fmt == "text":
        return text(record, full=full)
    if fmt == "methods":
        m = methods(record)
        return m["paragraph"] + "\n" + "".join(f"\n- {c}" for c in m["citations"]) + ("\n" if m["citations"] else "")
    if fmt == "tex":
        return tex(record)
    if fmt == "python":
        return python(record)
    if fmt == "json":
        return json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    raise ValueError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")


def block(record: dict) -> list[str]:
    """The lines a writer stores as the record's top-level `report` (short file list)."""
    return text(record).rstrip("\n").split("\n")
