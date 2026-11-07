"""Processing records (hfrecord 1.x): what made an exported image, and from which files.

HelioFITS Studio (and later Send to Python) writes a record beside or inside each export:
a sidecar `<export file name>.hfrec` (such as frame.png.hfrec; `.hfrec.json`, its older name, is read too), a PNG tEXt chunk, an OpenEXR header attribute, an MP4
metadata tag or a FITS extension named HFRECORD. The format is
schemas/hfrecord-1.schema.json, copied byte for byte from the design folder.

find(path) locates and checks the record for an exported file; display(record) turns it
into rows the app shows as they are; resolve(conn, record) looks every source up in the
index by SHA-256 (files.sha256 is unique) and groups what is missing by dataset, with the
core call that would fetch it. Reading a record writes nothing anywhere.

Studio's older exports carry only its `hfstudio` session block (Provenance.session()).
find() converts that into a minimal record marked legacy: layers and their steps come from
the saved scene, sources only as archive addresses (no SHA-256, so they cannot be resolved).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import struct
import subprocess
import uuid
import zlib
from pathlib import Path
from typing import Any

from . import db, upgrade
from .library import sha256_of

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "hfrecord-1.schema.json"
FORMAT_MAJOR = 1       # the hfrecord major version this reader understands
READER_VERSION = 1     # a record whose reader_compat is above this needs a newer reader
SIDECAR_SUFFIX = ".hfrec"  # the name Studio writes from 0.9.2: Finder types a file by its last extension
SIDECAR_SUFFIXES = (".hfrec", ".hfrec.json")  # both are read; .hfrec.json is the older name
KEYWORD = "hfrecord"   # PNG tEXt/iTXt keyword, EXR attribute, MP4 tag
LEGACY_KEYWORD = "hfstudio"
FITS_EXTNAME = "HFRECORD"
FOLDER_RECORD = "hfrecord.json"  # beside manifest.json in a Send to Python export folder
FFPROBE_TIMEOUT_S = 30

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXR_MAGIC = b"\x76\x2f\x31\x01"

# Copied from Studio's Provenance.java (JHV_CITATION, RHEF_CITATION, RHEF_CAVEAT), for legacy records.
JHV_CITATION = "Müller et al. (2017), Astronomy & Astrophysics, https://doi.org/10.1051/0004-6361/201730893"
RHEF_CITATION = "Gilly & Cranmer (2025), Solar Physics, https://doi.org/10.1007/s11207-025-02578-x"
RHEF_CAVEAT = "not a calibrated radiance"

KNOWN_OPS = frozenset(
    json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["step"]["properties"]["op"]["enum"])


class RecordError(ValueError):
    """A record was found but cannot be read."""


class UnsupportedRecord(RecordError):
    """A record of a format this reader does not know (a newer major version)."""


# ---- the schema --------------------------------------------------------------

_schema_cache: dict | None = None


def schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _schema_cache


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
    or (isinstance(v, float) and v.is_integer()),
}


def validate(record: Any) -> list[str]:
    """Problems with a record against the schema, as "path: problem" lines; empty when valid.

    Covers the keywords the schema uses (type, required, properties, additionalProperties,
    items, $ref, enum, pattern, minimum, minItems, maxItems), so the core needs no jsonschema.
    Annotations (format, description, examples) are not checked, as JSON Schema says.
    """
    errors: list[str] = []
    root = schema()
    _check(record, root, root, "$", errors)
    return errors


def _check(value, sch, root, where, errors) -> None:
    if sch is True or not isinstance(sch, dict):
        return
    if "$ref" in sch:
        ref = sch["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"unsupported $ref {ref!r}")
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        _check(value, target, root, where, errors)
    if "type" in sch:
        types = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(_TYPES[t](value) for t in types):
            errors.append(f"{where}: expected {' or '.join(types)}, got {type(value).__name__}")
            return
    if "enum" in sch and value not in sch["enum"]:
        errors.append(f"{where}: {value!r} is not one of {sch['enum']}")
    if isinstance(value, str) and "pattern" in sch and not re.search(sch["pattern"], value):
        errors.append(f"{where}: {value!r} does not match {sch['pattern']}")
    if _TYPES["number"](value) and "minimum" in sch and value < sch["minimum"]:
        errors.append(f"{where}: {value} is below the minimum {sch['minimum']}")
    if isinstance(value, list):
        if "minItems" in sch and len(value) < sch["minItems"]:
            errors.append(f"{where}: fewer than {sch['minItems']} items")
        if "maxItems" in sch and len(value) > sch["maxItems"]:
            errors.append(f"{where}: more than {sch['maxItems']} items")
        if "items" in sch:
            for i, item in enumerate(value):
                _check(item, sch["items"], root, f"{where}[{i}]", errors)
    if isinstance(value, dict):
        for key in sch.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing {key!r}")
        props = sch.get("properties", {})
        extra = sch.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                _check(item, props[key], root, f"{where}.{key}", errors)
            elif extra is False:
                errors.append(f"{where}: unexpected key {key!r}")
            elif isinstance(extra, dict):
                _check(item, extra, root, f"{where}.{key}", errors)


def check_version(record: Any) -> None:
    """Refuse a record this reader must not read: another major version, or a reader_compat
    above READER_VERSION. A higher minor version is read (minor versions only add keys)."""
    if not isinstance(record, dict):
        raise RecordError("a processing record is a JSON object")
    version = record.get("hfrecord")
    m = re.fullmatch(r"(\d+)\.(\d+)", version) if isinstance(version, str) else None
    if m is None:
        raise RecordError(f"not a processing record: hfrecord is {version!r}, expected MAJOR.MINOR")
    if int(m.group(1)) != FORMAT_MAJOR:
        raise UnsupportedRecord(f"processing record format {version} needs a newer HelioFITS Core "
                                f"(this one reads {FORMAT_MAJOR}.x)")
    compat = record.get("reader_compat")
    if isinstance(compat, int) and not isinstance(compat, bool) and compat > READER_VERSION:
        raise UnsupportedRecord(f"processing record needs reader {compat} or newer "
                                f"(this HelioFITS Core is reader {READER_VERSION})")


# ---- finding a record ------------------------------------------------------------


def sidecar_paths(path: Path) -> list[Path]:
    """Where a sidecar for an export may be: <file>.hfrec (the full export name, the canonical
    form), <file>.hfrec.json (the older name), then the same two on the basename as a fallback."""
    out = [path.with_name(path.name + suffix) for suffix in SIDECAR_SUFFIXES]
    if "." in path.name:
        stem = path.name.rsplit(".", 1)[0]
        out += [path.with_name(stem + suffix) for suffix in SIDECAR_SUFFIXES]
    return list(dict.fromkeys(out))


def migrate_sidecars(paths: list[str | Path], *, apply: bool = False) -> list[dict]:
    """Rename `<name>.hfrec.json` sidecars to `<name>.hfrec` (the name Finder can give Peek's
    previews). A folder is searched recursively. Nothing changes unless apply is true; a sidecar
    whose new name is taken, or that is not a readable record, is left alone and says why.
    Each answer: {"from", "to", "status"} with status renamed, would_rename, exists or not_a_record."""
    legacy = SIDECAR_SUFFIXES[1]
    todo: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            todo += sorted(q for q in p.rglob("*" + legacy) if q.is_file())
        elif p.name.endswith(legacy):
            todo.append(p)
        else:
            raise FileNotFoundError(f"not a {legacy} file or a folder: {p}")
    out = []
    for old in todo:
        new = old.with_name(old.name[: -len(".json")])
        row = {"from": str(old), "to": str(new)}
        try:
            if _parse(old.read_bytes(), str(old), []) is None:
                row["status"] = "not_a_record"
            elif new.exists():
                row["status"] = "exists"
            elif apply:
                old.rename(new)
                row["status"] = "renamed"
            else:
                row["status"] = "would_rename"
        except OSError as exc:
            row["status"] = f"error: {exc}"
        out.append(row)
    return out


def find(path: str | Path) -> dict | None:
    """The processing record for an exported file, or None when it carries none.

    Looks, in order, at a sidecar, a PNG tEXt/iTXt/zTXt chunk, an OpenEXR string attribute,
    an MP4/MOV metadata tag (through ffprobe, when it is on PATH), a FITS extension named
    HFRECORD, and for a FITS file an hfrecord.json in its folder (Send to Python). In each container the `hfrecord` copy is preferred; Studio's older `hfstudio`
    block is used only when no record is found anywhere, converted and marked legacy. A path
    that is itself a sidecar is read directly.

    Returns {"record", "found_in": {"kind", "container", "path"}, "legacy", "valid", "errors",
    "skipped", "output_check"}. Raises UnsupportedRecord for a record of a newer major format,
    FileNotFoundError when the path does not exist.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"no such file: {p}")
    skipped: list[dict] = []
    legacy: tuple[dict, str, str] | None = None  # (session, kind, container)

    if p.name.endswith(SIDECAR_SUFFIXES):
        record = _parse(p.read_bytes(), str(p), skipped)
        if record is None:
            raise RecordError(f"{p.name} is not a readable processing record: {skipped[-1]['problem']}")
        export = _export_beside(p, record)
        return _found(record, "sidecar", "sidecar", p, skipped, export=export)

    for side in sidecar_paths(p):
        if side.is_file():
            record = _parse(side.read_bytes(), str(side), skipped)
            if record is not None:
                return _found(record, "sidecar", "sidecar", side, skipped, export=p)

    kind = _sniff(p)
    texts: dict[str, str] = {}
    container_of: dict[str, str] = {}
    if kind == "png":
        for key, (text, chunk) in _png_texts(p).items():
            texts[key], container_of[key] = text, f"png:{chunk}:{key}"
    elif kind == "exr":
        for key, text in _exr_strings(p).items():
            texts[key], container_of[key] = text, f"exr:attr:{key}"
    elif kind == "mp4":
        tags = _ffprobe_tags(p, skipped)
        for key in (KEYWORD, LEGACY_KEYWORD, "comment"):
            if key in tags:
                texts[key], container_of[key] = tags[key], f"mp4:{key}"
    elif kind == "fits":
        text = _fits_record(p, skipped)
        if text is not None:
            texts[KEYWORD], container_of[KEYWORD] = text, f"fits:hdu:{FITS_EXTNAME}"

    if KEYWORD in texts:
        record = _parse(texts[KEYWORD].encode("utf-8"), container_of[KEYWORD], skipped)
        if record is not None:
            return _found(record, kind, container_of[KEYWORD], p, skipped)
    for key in (LEGACY_KEYWORD, "comment"):
        if key in texts:
            try:
                session = json.loads(texts[key])
            except json.JSONDecodeError as exc:
                skipped.append({"location": container_of[key], "problem": f"not JSON: {exc}"})
                continue
            if isinstance(session, dict) and ("scene" in session or "writer" in session):
                legacy = (session, kind, container_of[key])
                break
            skipped.append({"location": container_of[key], "problem": "not a Studio session block"})
    if kind == "fits":  # Send to Python writes one hfrecord.json beside the files it exports
        folder = p.parent / FOLDER_RECORD
        if folder.is_file():
            record = _parse(folder.read_bytes(), str(folder), skipped)
            if record is not None:
                return _found(record, "folder", f"folder:{FOLDER_RECORD}", folder, skipped)
    if legacy is not None:
        session, kind, container = legacy
        record = from_hfstudio(session, container=container, file_name=p.name, kind=kind)
        return _found(record, kind, container, p, skipped, legacy=True)
    return None


def _parse(raw: bytes, where: str, skipped: list) -> dict | None:
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        skipped.append({"location": where, "problem": f"not JSON: {exc}"})
        return None
    check_version(record)  # a record too new is refused outright, not skipped
    return record


def _found(record: dict, kind: str, container: str, where: Path, skipped: list, *,
           export: Path | None = None, legacy: bool = False) -> dict:
    errors = validate(record)
    return {
        "record": record,
        "found_in": {"kind": kind, "container": container, "path": str(where)},
        "legacy": legacy,
        "valid": not errors,
        "errors": errors,
        "skipped": skipped,
        "output_check": _output_check(record, export) if kind == "sidecar" else None,
    }


def _export_beside(sidecar: Path, record: dict) -> Path | None:
    """The export a sidecar describes: its output.file_name beside it, else the sidecar's stem
    with any extension that is there."""
    name = (record.get("output") or {}).get("file_name") if isinstance(record.get("output"), dict) else None
    if isinstance(name, str) and name and "/" not in name and (sidecar.parent / name).is_file():
        return sidecar.parent / name
    stem = sidecar.name[: -len(next(x for x in SIDECAR_SUFFIXES if sidecar.name.endswith(x)))]
    if (sidecar.parent / stem).is_file():
        return sidecar.parent / stem
    matches = sorted(q for q in sidecar.parent.glob(f"{_glob_escape(stem)}.*")
                     if q.is_file() and q != sidecar and not q.name.endswith(SIDECAR_SUFFIXES))
    return matches[0] if len(matches) == 1 else None


def _glob_escape(text: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", text)


def _output_check(record: dict, export: Path | None) -> dict | None:
    """Whether the export's bytes are the ones a sidecar describes (output.sha256)."""
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    expected = output.get("sha256")
    if not isinstance(expected, str):
        return None
    if export is None or not export.is_file():
        return {"sha256": expected, "file": None, "actual": None, "matches": None}
    actual = sha256_of(export)
    return {"sha256": expected, "file": str(export), "actual": actual, "matches": actual == expected}


def _sniff(path: Path) -> str | None:
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head.startswith(PNG_SIGNATURE):
        return "png"
    if head.startswith(EXR_MAGIC):
        return "exr"
    if head.startswith(b"SIMPLE  ="):
        return "fits"
    if head[4:8] == b"ftyp" or path.suffix.lower() in (".mp4", ".mov", ".m4v"):
        return "mp4"
    return None


# ---- PNG ----------------------------------------------------------------------------


def _png_texts(path: Path) -> dict[str, tuple[str, str]]:
    """The text of our keywords in a PNG's tEXt, zTXt and iTXt chunks: {keyword: (text, chunk)}.

    The first good chunk of each keyword wins; a chunk with a bad CRC is not trusted. Stops at
    IEND or where the file is cut off.
    """
    found: dict[str, tuple[str, str]] = {}
    wanted = {KEYWORD.encode(), LEGACY_KEYWORD.encode()}
    with open(path, "rb") as fh:
        if fh.read(8) != PNG_SIGNATURE:
            return found
        while True:
            head = fh.read(8)
            if len(head) < 8:
                break
            length, ctype = struct.unpack(">I4s", head)
            if ctype == b"IEND":
                break
            if ctype not in (b"tEXt", b"zTXt", b"iTXt"):
                fh.seek(length + 4, 1)
                continue
            body = fh.read(length)
            crc = fh.read(4)
            if len(body) < length or len(crc) < 4:
                break
            if zlib.crc32(ctype + body) != struct.unpack(">I", crc)[0]:
                continue
            key, _, rest = body.partition(b"\0")
            if key not in wanted or key.decode() in found:
                continue
            try:
                text = _png_text(ctype, rest)
            except (zlib.error, UnicodeDecodeError, ValueError):
                continue
            found[key.decode()] = (text, ctype.decode())
    return found


def _png_text(ctype: bytes, rest: bytes) -> str:
    if ctype == b"tEXt":
        return rest.decode("latin-1")
    if ctype == b"zTXt":
        return zlib.decompress(rest[1:]).decode("latin-1")
    # iTXt: compression flag, method, language tag\0, translated keyword\0, UTF-8 text
    if len(rest) < 2:
        raise ValueError("short iTXt")
    flag, tail = rest[0], rest[2:]
    _lang, _, tail = tail.partition(b"\0")
    _translated, _, text = tail.partition(b"\0")
    return (zlib.decompress(text) if flag else text).decode("utf-8")


# ---- OpenEXR --------------------------------------------------------------------------

EXR_MAX_HEADER = 64 << 20


def _exr_strings(path: Path) -> dict[str, str]:
    """String attributes named hfrecord or hfstudio in an OpenEXR file's (first) header.

    The header is a run of attributes: name\\0, type\\0, a little-endian int32 size, the value;
    an empty name ends it. Read without OpenEXR itself.
    """
    out: dict[str, str] = {}
    with open(path, "rb") as fh:
        if fh.read(4) != EXR_MAGIC:
            return out
        fh.read(4)  # version and flags
        read = 8
        while read < EXR_MAX_HEADER:
            name = _cstring(fh)
            if not name:
                break
            atype = _cstring(fh)
            raw = fh.read(4)
            if atype is None or len(raw) < 4:
                break
            (size,) = struct.unpack("<i", raw)
            if size < 0 or read + size > EXR_MAX_HEADER:
                break
            value = fh.read(size)
            if len(value) < size:
                break
            read += len(name) + len(atype) + 6 + size
            if atype == "string" and name in (KEYWORD, LEGACY_KEYWORD) and name not in out:
                out[name] = value.decode("utf-8", errors="replace")
    return out


def _cstring(fh, limit: int = 256) -> str | None:
    buf = bytearray()
    while len(buf) <= limit:
        c = fh.read(1)
        if not c:
            return None
        if c == b"\0":
            return buf.decode("latin-1")
        buf += c
    return None


# ---- MP4 / MOV ---------------------------------------------------------------------------


def _ffprobe_tags(path: Path, skipped: list) -> dict[str, str]:
    """The container's metadata tags (keys lower-cased) through ffprobe; empty without it."""
    exe = shutil.which("ffprobe")
    if exe is None:
        skipped.append({"location": "mp4", "problem": "ffprobe is not on PATH; movie tags not read"})
        return {}
    try:
        run = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", str(path)],
                             capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        skipped.append({"location": "mp4", "problem": f"ffprobe failed: {exc}"})
        return {}
    if run.returncode != 0:
        skipped.append({"location": "mp4", "problem": f"ffprobe failed: {run.stderr.strip()[:200]}"})
        return {}
    try:
        tags = (json.loads(run.stdout).get("format") or {}).get("tags") or {}
    except (json.JSONDecodeError, AttributeError):
        return {}
    return {str(k).lower(): v for k, v in tags.items() if isinstance(v, str)}


# ---- FITS -------------------------------------------------------------------------------


def _fits_record(path: Path, skipped: list) -> str | None:
    """The JSON in a FITS extension named HFRECORD: bytes as an 8-bit image, text in a table's
    first column (rows joined), or a header keyword HFRECORD (long-string CONTINUE cards)."""
    from astropy.io import fits

    try:
        with fits.open(path, memmap=False, lazy_load_hdus=True) as hdus:
            for hdu in hdus:
                if hdu.name.upper() != FITS_EXTNAME:
                    continue
                data = hdu.data
                if data is None:
                    value = hdu.header.get(FITS_EXTNAME)
                    return value if isinstance(value, str) else None
                if getattr(data, "dtype", None) is not None and data.dtype.names:
                    column = data[data.dtype.names[0]]
                    parts = [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in column]
                    return "".join(parts)
                return bytes(data.astype("uint8").ravel().tobytes()).rstrip(b"\0").decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        skipped.append({"location": f"fits:hdu:{FITS_EXTNAME}", "problem": f"unreadable: {exc}"})
    return None


# ---- Studio's older provenance block ----------------------------------------------------------

_MEDIA = {"png": ("still", "image/png"), "exr": ("exr_series", "image/x-exr"),
          "mp4": ("movie", "video/mp4"), "fits": ("fits_data", "application/fits")}


def from_hfstudio(session: dict, *, container: str, file_name: str | None = None,
                  kind: str | None = "png") -> dict:
    """A minimal hfrecord 1.0 from Studio's hfstudio session block (Provenance.session()).

    What the block holds is kept: the writer stamp, build, commit, dirty, created, the scene
    (as native.studio_session), each layer's archive URIs (as sources with source_url; the
    block keeps no local paths and no hashes) and the RHEF caveat and stamp. Steps are read
    from each layer's saved imageParams in the order the design note gives; they are HelioFITS Core's
    reading of the scene, not Studio's own, and the record says so under "legacy".
    """
    scene = session.get("scene") if isinstance(session.get("scene"), dict) else {}
    state = scene.get("org.helioviewer.jhv.state") if isinstance(scene.get("org.helioviewer.jhv.state"), dict) else {}
    saved_layers = [x for x in (state.get("imageLayers") or []) if isinstance(x, dict)]
    by_name: dict[str, dict] = {}
    for src in session.get("sources") or []:
        if isinstance(src, dict) and isinstance(src.get("layer"), str):
            by_name.setdefault(src["layer"], src)
    out_kind, media = _MEDIA.get(kind or "", ("still", "application/octet-stream"))
    layers = []
    any_rhef = False
    for i, saved in enumerate(saved_layers):
        data = saved.get("data") if isinstance(saved.get("data"), dict) else {}
        name = str(saved.get("name") or f"layer {i + 1}")
        src = by_name.get(name, {})
        uris = [u for u in (src.get("uris") or []) if isinstance(u, str)]
        layer = {
            "id": str(saved.get("id") or i),
            "name": name,
            "sources": [{"source_url": u} for u in uris],
            "steps": _legacy_steps(data),
            "native": saved,
        }
        if isinstance(saved.get("enabled"), bool):
            layer["enabled"] = saved["enabled"]
        if isinstance(src.get("request"), dict):
            layer["source_spec"] = {"kind": "helioviewer_api", "request": src["request"]}
        elif isinstance(src.get("query"), dict):
            layer["source_spec"] = {"kind": "fits_request", "request": src["query"]}
        elif uris:
            layer["source_spec"] = {"kind": "uri_list"}
        any_rhef |= data.get("filter") == "RHEF"
        layers.append(layer)
    caveats = []
    if session.get("caveat"):
        caveats.append(f"RHEF output is {session['caveat']}." if session["caveat"] == RHEF_CAVEAT
                       else str(session["caveat"]))
    caveats.append("Converted by HelioFITS Core from Studio's older hfstudio block: no source hashes, "
                   "and steps read from the saved scene.")
    created = session.get("created") if isinstance(session.get("created"), str) else None
    record: dict = {
        "hfrecord": "1.0",
        "reader_compat": 1,
        # Stable for the same block, so reopening a file gives the same id.
        "record_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "hfstudio:" + json.dumps(session, sort_keys=True))),
        "created": created or "1970-01-01T00:00:00Z",
        "producer": {
            "program": "HelioFITS Studio",
            "version": str(session.get("buildId") or "unknown"),
        },
        "output": {"kind": out_kind, "media_type": media, "display_referred": True},
        "layers": layers,
        "caveats": caveats,
        "citations": [JHV_CITATION] + ([RHEF_CITATION] if any_rhef or session.get("caveat") else []),
        "native_ref": container,
        "native": {"studio_session": scene},
        "legacy": {
            "from": container,
            "created_known": created is not None,
            "rhef_stamp": session.get("rhef_stamp"),
            "note": "Studio's hfstudio session block, converted by HelioFITS Core. Sources are archive "
                    "addresses only (local files were never written) and carry no SHA-256.",
        },
    }
    for key, field in (("buildId", "build_id"), ("commit", "commit"), ("writer", "stamp")):
        if isinstance(session.get(key), str):
            record["producer"][field] = session[key]
    if isinstance(session.get("dirty"), bool):
        record["producer"]["dirty"] = session["dirty"]
    if file_name:
        record["output"]["file_name"] = file_name
    if isinstance(state.get("time"), str):
        record["scene"] = {"time": state["time"]}
    record["summary"] = generated_summary(record)
    return record


# (op, stage, imageParams keys) in the design note's order; filter and sequence live beside imageParams.
_LEGACY_STEPS = [
    ("select_plane", "decode", ("plane", "planes")),
    ("clip", "decode", ("clippingMode", "clippingMin", "clippingMax")),
    ("stretch", "decode", ("scalingMode", "gamma", "beta", "alpha")),
    ("image_filter", "cpu", ("filter",)),
    ("sequence_filter", "cpu", ("sequence",)),
    ("levels", "gpu", ("brightOffset", "brightScale")),
    ("radial_gain", "gpu", ("enhanced",)),
    ("difference", "gpu", ("differenceMode",)),
    ("sharpen", "gpu", ("sharpen",)),
    ("rhef_upsilon", "gpu", ("upsilonLow", "upsilonHigh")),
    ("colormap", "gpu", ("lut", "invert")),
    ("channel_mask", "gpu", ("color",)),
    ("geometric_mask", "gpu", ("innerMask", "outerMask", "slitLeft", "slitRight", "sectorCenter", "sectorWidth")),
    ("opacity_blend", "gpu", ("opacity", "blend")),
]


def _legacy_steps(data: dict) -> list[dict]:
    params = dict(data.get("imageParams") or {}) if isinstance(data.get("imageParams"), dict) else {}
    for key in ("filter", "sequence"):
        if key in data:
            params[key] = data[key]
    rhef = params.get("filter") == "RHEF"
    steps = []
    for op, stage, keys in _LEGACY_STEPS:
        found = {k: params[k] for k in keys if k in params}
        if not found or (op == "rhef_upsilon" and not rhef):
            continue
        step = {"op": op, "stage": stage, "params": found}
        if op == "stretch" and isinstance(found.get("scalingMode"), str):
            step["algorithm"] = found["scalingMode"]
        if op == "image_filter" and isinstance(found.get("filter"), str) and found["filter"] != "None":
            step["algorithm"] = found["filter"]
            if rhef:
                step["citation"] = RHEF_CITATION
                step["caveat"] = RHEF_CAVEAT
        steps.append(step)
    return steps


# ---- rows for people -----------------------------------------------------------------------


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _s(value: Any) -> str | None:
    """A string field for the app: as it is, null as None, anything else as JSON text."""
    return None if value is None else _text(value)


def _pairs(obj: Any) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    return [{"name": str(k), "value": _text(v)} for k, v in obj.items()]


def _filter_of(layer: dict) -> str:
    for step in layer.get("steps") or []:
        if isinstance(step, dict) and step.get("op") == "image_filter":
            f = (step.get("params") or {}).get("filter") or step.get("algorithm")
            return "no filter" if f in (None, "None") else str(f)
    return "no filter"


def _lut_of(layer: dict) -> str | None:
    for step in layer.get("steps") or []:
        if isinstance(step, dict) and step.get("op") == "colormap":
            lut = (step.get("params") or {}).get("lut")
            return str(lut) if lut is not None else None
    return None


def _frames(layer: dict) -> int:
    count = layer.get("source_count")
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    return len(layer.get("sources") or [])


def generated_summary(record: dict) -> str:
    """The plain text summary a writer would give, for a record that has none."""
    producer = record.get("producer") or {}
    output = record.get("output") or {}
    scene = record.get("scene") or {}
    when = scene.get("time") or " to ".join(scene.get("time_range") or []) or record.get("created", "")
    head = f"{producer.get('program', 'Unknown program')} {producer.get('version', '')}".strip()
    lines = [f"{head}, {output.get('kind', 'export')} at {when}."]
    for i, layer in enumerate(record.get("layers") or [], 1):
        if not isinstance(layer, dict):
            continue
        n = _frames(layer)
        bits = [f"{n} frame{'s' if n != 1 else ''}", _filter_of(layer)]
        lut = _lut_of(layer)
        if lut:
            bits.append(f"LUT {lut}")
        label = (layer.get("dataset") or {}).get("label") or layer.get("name", f"layer {i}")
        lines.append(f"{i}. {label}: " + ", ".join(bits))
    cites = [c.split(",")[0] for c in record.get("citations") or [] if isinstance(c, str)]
    if cites:
        lines.append("Cite: " + "; ".join(cites) + ".")
    return "\n".join(lines)


def _time_span(layer: dict, scene: dict) -> tuple[str | None, str | None]:
    """The layer's first and last source time; else the scene's range; else its time twice."""
    times = sorted(t for t in (upgrade.seconds(s.get("date_obs")) for s in layer.get("sources") or []
                               if isinstance(s, dict)) if t is not None)
    if times:
        return upgrade._iso(times[0]), upgrade._iso(times[-1])
    rng = scene.get("time_range")
    if isinstance(rng, list) and len(rng) == 2:
        return rng[0], rng[1]
    t = scene.get("time")
    return (t, t) if isinstance(t, str) else (None, None)


def studio_launch(layer: dict, scene: dict) -> dict | None:
    """The Studio command line that opens this layer's dataset over its time span:
    -dataset <dataset_uid, else dataset_key> -start ISO -end ISO. None without a dataset."""
    ds = layer.get("dataset") if isinstance(layer.get("dataset"), dict) else {}
    ident = ds.get("dataset_uid") or ds.get("dataset_key")
    start, end = _time_span(layer, scene)
    if not all(isinstance(v, str) and v for v in (ident, start, end)):
        return None
    return {"dataset": ident, "start": start, "end": end,
            "args": ["-dataset", ident, "-start", start, "-end", end]}


def _step_row(i: int, step: dict) -> dict:
    varies = step.get("varies") if isinstance(step.get("varies"), dict) else None
    ref = step.get("reference") if isinstance(step.get("reference"), dict) else None
    reference = None
    if ref:
        kind = ref.get("kind")
        if kind == "running":
            reference = "previous frame (running difference)"
        elif kind == "base":
            reference = f"base frame: source {ref.get('source_index')}"
        elif kind == "compare":
            reference = f"compared with layer {ref.get('layer_id')}"
        else:
            reference = _text(ref)
    op = str(step.get("op", ""))
    return {
        "index": i,
        "op": op,
        "known_op": op in KNOWN_OPS,
        "stage": _s(step.get("stage")),
        "algorithm": _s(step.get("algorithm")),
        "implementation": _s(step.get("implementation")),
        "automatic": bool(step.get("automatic", False)),
        "params": _pairs(step.get("params")),
        "resolved": _pairs(step.get("resolved")),
        "units": _pairs(step.get("units")),
        "varies": varies is not None,
        "varies_params": [str(p) for p in (varies or {}).get("params") or []],
        "automation": [str(a) for a in (varies or {}).get("automation") or []],
        "changed_by_user": (varies or {}).get("changed_by_user") if isinstance((varies or {}).get("changed_by_user"), bool) else None,
        "reference": reference,
        "caveat": _s(step.get("caveat")),
        "citation": _s(step.get("citation")),
        "stamp": _s(step.get("stamp")),
    }


def display(record: dict) -> dict:
    """Display-ready rows for a record: strings and flat lists, nothing the app must interpret.

    layers lists the top layer first (the record's own order is drawing order, bottom first);
    each row's `drawn` is its index in the record.
    """
    scene = record.get("scene") if isinstance(record.get("scene"), dict) else {}
    producer = record.get("producer") if isinstance(record.get("producer"), dict) else {}
    layers_in = [x for x in record.get("layers") or [] if isinstance(x, dict)]
    layers = []
    master = None
    acknowledgements = []
    for drawn, layer in enumerate(layers_in):
        ds = layer.get("dataset") if isinstance(layer.get("dataset"), dict) else {}
        steps = [s for s in layer.get("steps") or [] if isinstance(s, dict)]
        launch = studio_launch(layer, scene)
        native = layer.get("native") if isinstance(layer.get("native"), dict) else {}
        if native.get("master") is True and launch:
            master = launch
        ack = layer.get("acknowledgement")
        if isinstance(ack, str) and ack:
            acknowledgements.append({"layer": _s(layer.get("name")), "text": ack})
        spec = layer.get("source_spec") if isinstance(layer.get("source_spec"), dict) else {}
        layers.append({
            "drawn": drawn,
            "id": _s(layer.get("id")),
            "name": _s(layer.get("name")),
            "enabled": layer.get("enabled") is not False,
            "dataset_key": _s(ds.get("dataset_key")),
            "dataset_uid": _s(ds.get("dataset_uid")),
            "dataset_label": _s(ds.get("label") or layer.get("name")),
            "frames": _frames(layer),
            "shortened": "source_count" in layer,
            "source_spec": _s(spec.get("kind")),
            "filter": _filter_of(layer),
            "lut": _lut_of(layer),
            "automatic_steps": sum(1 for s in steps if s.get("automatic")),
            "varies": any(isinstance(s.get("varies"), dict) for s in steps),
            "steps": [_step_row(i, s) for i, s in enumerate(steps, 1)],
            "caveats": [s["caveat"] for s in steps if isinstance(s.get("caveat"), str)],
            "acknowledgement": ack if isinstance(ack, str) else None,
            "studio": launch,
        })
    if master is None:  # the top layer, the one drawn last
        master = next((row["studio"] for row in reversed(layers) if row["studio"]), None)
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    head = " ".join(str(producer.get(k)) for k in ("program", "version") if producer.get(k))
    build = ", ".join(f"{label} {producer[k]}" for k, label in (("build_id", "build"), ("commit", "commit"))
                      if producer.get(k))
    rebuild = []
    if producer.get("dirty"):
        rebuild.append("made by a build with uncommitted changes")
    if any((layer.get("source_spec") or {}).get("kind") == "helioviewer_api" for layer in layers_in):
        rebuild.append("a Helioviewer layer cannot promise the same pixels")
    return {
        "summary": record.get("summary") if isinstance(record.get("summary"), str) else generated_summary(record),
        "producer": head + (f" ({build})" if build else ""),
        "producer_stamp": _s(producer.get("stamp")),
        "dirty": bool(producer.get("dirty", False)),
        "created": _s(record.get("created")),
        "record_id": _s(record.get("record_id")),
        "format": _s(record.get("hfrecord")),
        "output": _pairs(output),
        "scene": _pairs(scene),
        "layers": list(reversed(layers)),
        "caveats": [c for c in record.get("caveats") or [] if isinstance(c, str)],
        "citations": [c for c in record.get("citations") or [] if isinstance(c, str)],
        "acknowledgements": acknowledgements,
        "provenance": _pairs(record.get("provenance")),
        # hfrecord-1 field, named by Studio's schema (docs/spec/hfrecord-1.schema.json in HelioFITS-Studio)
        "heliad_view": record.get("heliad_view") if isinstance(record.get("heliad_view"), dict) else None,
        "snapshot": _pairs(record.get("snapshot")),
        "not_guaranteed_to_rebuild": rebuild,
        "studio": master,
    }


def read(path: str | Path) -> dict:
    """record.read: the record for an exported file with its display rows, or {"found": false}."""
    found = find(path)
    if found is None:
        return {"found": False, "path": str(path), "record": None, "view": None}
    from . import report  # imported here: report reads this module
    view = display(found["record"])
    stored = found["record"].get("report")
    view["report"] = [x for x in stored if isinstance(x, str)] if isinstance(stored, list) else report.block(found["record"])
    return {"found": True, "path": str(path), **found, "view": view}


# ---- against the library ------------------------------------------------------------------------


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _library_dataset(conn, ds: dict) -> sqlite3.Row | None:
    """The index's dataset for a record's: by dataset_uid when both have one, else by key."""
    uid = ds.get("dataset_uid")
    if uid and _has_column(conn, "datasets", "dataset_uid"):
        row = conn.execute("SELECT * FROM datasets WHERE dataset_uid = ?", (uid,)).fetchone()
        if row is not None:
            return row
    key = ds.get("dataset_key")
    if key:
        return conn.execute("SELECT * FROM datasets WHERE dataset_key = ?", (key,)).fetchone()
    return None


_FILE_SQL = (
    "SELECT f.id AS file_id, f.relpath, f.size, f.version, o.id AS observation_id, o.obs_key, "
    "o.date_obs, o.current_file_id, d.dataset_key, d.label FROM files f "
    "JOIN observations o ON o.id = f.observation_id JOIN datasets d ON d.id = o.dataset_id "
    "WHERE f.sha256 = ?")


def resolve(conn: sqlite3.Connection, record: dict) -> dict:
    """Every source of a record looked up in the index by SHA-256.

    Per source: status in_library (the observation's current file), superseded (kept, but a
    newer version is current), missing, or no_hash (a Helioviewer frame or a legacy record:
    nothing to look up). Missing sources are grouped by dataset with their time span and the
    core call that would fetch them ("fetch": method and params), or why there is none.
    """
    check_version(record)
    rejected_ok = db.has_table(conn, "rejections")
    layers_out = []
    groups: dict[str, dict] = {}
    counts = {"in_library": 0, "superseded": 0, "missing": 0, "no_hash": 0}
    for li, layer in enumerate(x for x in record.get("layers") or [] if isinstance(x, dict)):
        ds = layer.get("dataset") if isinstance(layer.get("dataset"), dict) else {}
        lib_ds = _library_dataset(conn, ds)
        rows = []
        for si, src in enumerate(s for s in layer.get("sources") or [] if isinstance(s, dict)):
            sha = src.get("sha256")
            row = {"layer": li, "source": si, "sha256": _s(sha), "date_obs": _s(src.get("date_obs")),
                   "original_name": _s(src.get("original_name")), "status": "no_hash", "in_library": False,
                   "dataset_key": _s(ds.get("dataset_key")), "relpath": None, "file_id": None,
                   "observation_id": None, "rejected": None, "current": None}
            hit = conn.execute(_FILE_SQL, (sha,)).fetchone() if isinstance(sha, str) else None
            if hit is not None:
                current = hit["current_file_id"]
                row.update(status="in_library" if current == hit["file_id"] else "superseded",
                           in_library=True, dataset_key=hit["dataset_key"], relpath=hit["relpath"],
                           file_id=hit["file_id"], observation_id=hit["observation_id"])
                if current != hit["file_id"] and current is not None:
                    cur = conn.execute("SELECT relpath, version FROM files WHERE id = ?", (current,)).fetchone()
                    row["current"] = dict(cur) if cur else None
                if rejected_ok:
                    r = conn.execute("SELECT reason FROM rejections WHERE observation_id = ?",
                                     (hit["observation_id"],)).fetchone()
                    row["rejected"] = r["reason"] if r else None
            elif isinstance(sha, str):
                row["status"] = "missing"
                if lib_ds is not None:
                    row["dataset_key"] = lib_ds["dataset_key"]
                obs_key = src.get("heliad_obs_key")  # hfrecord-1 field name, from Studio's schema
                if isinstance(obs_key, str):  # the same observation, kept as another version
                    other = conn.execute(
                        "SELECT o.id, f.relpath, f.version FROM observations o "
                        "JOIN files f ON f.id = o.current_file_id WHERE o.obs_key = ?", (obs_key,)).fetchone()
                    if other is not None:
                        row["observation_id"] = other["id"]
                        row["current"] = {"relpath": other["relpath"], "version": other["version"]}
                _add_missing(groups, ds, lib_ds, layer, src)
            counts[row["status"]] += 1
            rows.append(row)
        layers_out.append({"layer": li, "name": _s(layer.get("name")), "dataset_key": _s(ds.get("dataset_key")),
                           "dataset_uid": _s(ds.get("dataset_uid")),
                           "dataset_in_library": lib_ds["dataset_key"] if lib_ds is not None else None,
                           "sources": rows})
    missing = [_fetch_plan(conn, g) for g in groups.values()]
    return {"counts": counts, "layers": layers_out, "missing_by_dataset": missing,
            "complete": counts["missing"] == 0}


def _add_missing(groups, ds, lib_ds, layer, src) -> None:
    ident = _s(ds.get("dataset_uid") or ds.get("dataset_key")) or f"layer:{layer.get('id')}"
    g = groups.setdefault(ident, {
        "dataset": ident, "dataset_key": lib_ds["dataset_key"] if lib_ds is not None else _s(ds.get("dataset_key")),
        "dataset_uid": _s(ds.get("dataset_uid")), "label": _s(ds.get("label") or layer.get("name")),
        "in_library": lib_ds is not None, "record_dataset": ds, "source_spec": layer.get("source_spec"),
        "sha256": [], "times": []})
    g["sha256"].append(src.get("sha256"))
    t = upgrade.seconds(src.get("date_obs"))
    if t is not None:
        g["times"].append(t)


def _fetch_plan(conn, g: dict) -> dict:
    times = sorted(g.pop("times"))
    ds = g.pop("record_dataset")
    spec = g.pop("source_spec")
    out = {**g, "count": len(g["sha256"]), "start": None, "end": None, "fetch": None, "reason": None}
    if not times:
        out["reason"] = "the record gives no observation times for these sources"
        return out
    key = g["dataset_key"]
    if g["in_library"]:
        tol = upgrade.dataset_tolerance(conn, key)
        out["start"], out["end"] = upgrade._iso(times[0] - tol), upgrade._iso(times[-1] + tol)
        out["fetch"] = {"method": "dataset.upgrade",
                        "params": {"dataset_key": key, "start": out["start"], "end": out["end"],
                                   "dry_run": False},
                        "inferred": False,
                        "note": "fetches the frames the dataset lacks in this span, through the "
                                "dataset's own query; a frame kept as another version is not fetched again"}
        return out
    tol = upgrade.MAX_TOLERANCE_S
    out["start"], out["end"] = upgrade._iso(times[0] - tol), upgrade._iso(times[-1] + tol)
    if not ds.get("instrument"):
        out["reason"] = ("the dataset is not in the library and the record names no instrument to ask "
                         "the archive for" + (f" (source_spec {spec.get('kind')})" if isinstance(spec, dict) else ""))
        return out
    query = {"start": out["start"], "end": out["end"], "instrument": ds.get("instrument"),
             "wavelength": ds.get("wavelength"), "detector": ds.get("detector"), "level": ds.get("level"),
             "source": upgrade._sunpy_source(ds.get("observatory"))}
    out["fetch"] = {"method": "download.enqueue",
                    "params": {"query": {k: v for k, v in query.items() if v is not None}, "source": "fido"},
                    "inferred": True,
                    "note": "inferred from the record's dataset (instrument, wavelength, detector, level); "
                            "the dataset is not in the library, so the archive may name it differently"}
    return out
