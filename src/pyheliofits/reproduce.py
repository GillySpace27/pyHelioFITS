"""Reproducing an export from its processing record: pixel digests, comparison and preflight.

The file bytes of two exports of one scene never match: a Studio PNG carries a fresh ``record_id``
and ``created`` time in its sidecar chunk. What can match is the picture. ``hfpix/1`` is the
canonical pixel stream this module hashes (see ``docs/reproduce.md``): an ASCII header
``hfpix/1 <width> <height> <channels> <bit_depth>\\n`` followed by every sample, row-major and
channel-interleaved, as a big-endian unsigned integer of ``bit_depth`` bits. A record of format
1.1 stores that digest as ``output.pixel_sha256`` (and one per frame in ``output.frame_digests``),
so a replay can be judged without the original file.

Four verdicts:

* ``identical``: the digests are equal.
* ``equivalent``: the pixels differ by at most ``tolerance`` levels, and a tolerance was given.
  Nothing here invents a tolerance; it has to come from measurement on named hardware.
* ``different``: the pixels differ beyond the tolerance. When the two environments are known to
  be the same, the report says so: that is a bug or a hidden input, not a GPU difference.
* ``cannot_check``: nothing to compare against (no digest in the record and no original beside
  it, or no record at all), or the two images differ in size, channels or bit depth, which is a
  fault of the replay's export rather than of the picture. Whether a replay was ever promised exact is ``preflight``'s answer,
  a separate question from how a given replay turned out.

The four names are the ones HelioFITS Studio shows (Identical, Equivalent, Different, Cannot
check); the JSON values are lower case with an underscore.

PNG reading uses Pillow when it is installed (``pip install "pyheliofits[reproduce]"``) and a
small pure-Python decoder otherwise, which is correct for the non-interlaced grey, RGB and RGBA
files Studio writes but slow on large frames with Average or Paeth filtering.

    python -m pyheliofits.reproduce digest FILE.png
    python -m pyheliofits.reproduce compare ORIGINAL.png REPLAY.png [--tolerance N] [--diff OUT.png]
    python -m pyheliofits.reproduce preflight FILE.hfrec
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .core import record as hfrecord

DIGEST_FORMAT = "hfpix/1"
VERDICTS = ("identical", "equivalent", "different", "cannot_check")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The chunks that carry the picture; everything else is ancillary and left out of content_sha256.
_PNG_CONTENT_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS"}


# ---- images ---------------------------------------------------------------------------------


@dataclass
class Image:
    """Decoded samples: shape (height, width, channels), dtype uint8 or uint16."""

    samples: np.ndarray
    bit_depth: int

    @property
    def height(self) -> int:
        return int(self.samples.shape[0])

    @property
    def width(self) -> int:
        return int(self.samples.shape[1])

    @property
    def channels(self) -> int:
        return int(self.samples.shape[2])

    @property
    def shape(self) -> tuple[int, int, int, int]:
        return (self.width, self.height, self.channels, self.bit_depth)


def read_image(path: str | Path) -> Image:
    """Decode a PNG into samples. Other containers raise ValueError (EXR and movies are frame
    digests on the Studio side; this reader compares what it can open without extra tools)."""
    path = Path(path)
    head = path.read_bytes()[:8] if path.is_file() else b""
    if head != _PNG_SIGNATURE:
        raise ValueError(f"{path.name}: not a PNG (only PNG stills and frames are compared here)")
    bit_depth = head_depth = data_bit_depth(path)
    try:
        from PIL import Image as PILImage  # optional
    except ImportError:
        return _read_png(path)
    if head_depth == 16:
        # Pillow keeps 16-bit grey as I;16 but reduces 16-bit RGB(A) to 8; ours keeps every sample.
        return _read_png(path)
    with PILImage.open(path) as im:
        if im.mode == "P":
            im = im.convert("RGBA" if "transparency" in im.info else "RGB")
        elif im.mode not in ("L", "LA", "RGB", "RGBA"):
            return _read_png(path)
        arr = np.asarray(im)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    return Image(np.ascontiguousarray(arr.astype(np.uint8)), bit_depth)


def data_bit_depth(path: Path) -> int:
    """The bit depth in a PNG's IHDR."""
    with open(path, "rb") as fh:
        head = fh.read(29)
    if len(head) < 29 or head[12:16] != b"IHDR":
        raise ValueError(f"{path.name}: damaged PNG header")
    return head[24]


def _read_png(path: Path) -> Image:
    """Pure-Python PNG decoder for non-interlaced 8- and 16-bit grey, grey+alpha, RGB, RGBA and
    palette images. Filters are undone per row; Sub, Up and None are vectorised, Average and Paeth
    walk the row."""
    data = path.read_bytes()
    pos = 8
    ihdr = None
    idat = bytearray()
    plte = None
    trns = None
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"PLTE":
            plte = body
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IEND":
            break
    if ihdr is None:
        raise ValueError(f"{path.name}: no IHDR chunk")
    width, height, bit_depth, ctype_, _comp, _filt, interlace = ihdr
    if interlace:
        raise ValueError(f"{path.name}: interlaced PNG is not supported by the built-in decoder (install Pillow)")
    if bit_depth not in (8, 16):
        raise ValueError(f"{path.name}: {bit_depth}-bit PNG is not supported by the built-in decoder (install Pillow)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype_)
    if channels is None:
        raise ValueError(f"{path.name}: unknown PNG colour type {ctype_}")
    bps = bit_depth // 8
    bpp = channels * bps
    stride = width * bpp
    raw = zlib.decompress(bytes(idat))
    if len(raw) != height * (stride + 1):
        raise ValueError(f"{path.name}: PNG data length does not match its size")
    out = np.empty((height, stride), dtype=np.uint8)
    prior = np.zeros(stride, dtype=np.uint8)
    for y in range(height):
        start = y * (stride + 1)
        ftype = raw[start]
        row = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=start + 1)
        out[y] = _unfilter(ftype, row, prior, bpp)
        prior = out[y]
    if bit_depth == 16:
        samples = out.reshape(height, width, channels, 2).astype(np.uint16)
        samples = (samples[..., 0] << 8) | samples[..., 1]
    else:
        samples = out.reshape(height, width, channels)
    if ctype_ == 3:  # palette: expand to RGB(A) so the digest describes colours, not indices
        if plte is None:
            raise ValueError(f"{path.name}: palette PNG without PLTE")
        table = np.frombuffer(plte, dtype=np.uint8).reshape(-1, 3)
        idx = samples[:, :, 0]
        rgb = table[idx]
        if trns is not None:
            alpha = np.full(len(table), 255, dtype=np.uint8)
            alpha[:len(trns)] = np.frombuffer(trns, dtype=np.uint8)
            samples = np.concatenate([rgb, alpha[idx][:, :, None]], axis=2)
        else:
            samples = rgb
    return Image(np.ascontiguousarray(samples), bit_depth)


def _unfilter(ftype: int, row: np.ndarray, prior: np.ndarray, bpp: int) -> np.ndarray:
    if ftype == 0:
        return row.copy()
    if ftype == 2:
        return (row.astype(np.uint16) + prior).astype(np.uint8)
    if ftype == 1:
        # recon[i] = raw[i] + recon[i - bpp]: a running sum along each byte position of a pixel
        n = len(row)
        padded = np.zeros(((n + bpp - 1) // bpp) * bpp, dtype=np.uint64)
        padded[:n] = row
        groups = padded.reshape(-1, bpp)
        return (np.cumsum(groups, axis=0) & 0xFF).astype(np.uint8).reshape(-1)[:n]
    out = bytearray(len(row))
    r = row.tolist()
    p = prior.tolist()
    if ftype == 3:
        for i in range(len(r)):
            left = out[i - bpp] if i >= bpp else 0
            out[i] = (r[i] + ((left + p[i]) >> 1)) & 0xFF
    elif ftype == 4:
        for i in range(len(r)):
            a = out[i - bpp] if i >= bpp else 0
            b = p[i]
            c = p[i - bpp] if i >= bpp else 0
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            out[i] = (r[i] + pred) & 0xFF
    else:
        raise ValueError(f"bad PNG filter type {ftype}")
    return np.frombuffer(bytes(out), dtype=np.uint8)


def write_png(path: str | Path, samples: np.ndarray) -> Path:
    """A small 8-bit PNG writer (grey, grey+alpha, RGB or RGBA) for difference images."""
    path = Path(path)
    arr = np.ascontiguousarray(samples)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.dtype != np.uint8:
        raise ValueError("write_png takes uint8 samples")
    h, w, c = arr.shape
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[c]
    rows = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    data = _PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")
    path.write_bytes(data)
    return path


# ---- digests --------------------------------------------------------------------------------


def pixel_digest(image: Image) -> str:
    """The hfpix/1 SHA-256 of an image: header line, then big-endian samples row by row."""
    h = hashlib.sha256()
    h.update(f"{DIGEST_FORMAT} {image.width} {image.height} {image.channels} {image.bit_depth}\n".encode("ascii"))
    arr = image.samples
    if image.bit_depth == 16:
        arr = arr.astype(">u2")
    else:
        arr = arr.astype(np.uint8)
    h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def content_sha256(path: str | Path) -> str | None:
    """SHA-256 of a PNG with every ancillary chunk removed (IHDR, PLTE, tRNS, IDAT and IEND kept,
    in file order), so an export and its re-encoding by the same encoder compare equal whatever
    text chunks they carry. None for other containers."""
    data = Path(path).read_bytes()
    if data[:8] != _PNG_SIGNATURE:
        return None
    h = hashlib.sha256()
    h.update(_PNG_SIGNATURE)
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if ctype in _PNG_CONTENT_CHUNKS:
            h.update(data[pos:end])
        pos = end
        if ctype == b"IEND":
            break
    return h.hexdigest()


def digests(path: str | Path) -> dict:
    """Both digests of a file, as a record's output block names them."""
    image = read_image(path)
    return {
        "path": str(path),
        "width": image.width,
        "height": image.height,
        "channels": image.channels,
        "bit_depth": image.bit_depth,
        "pixel_sha256": pixel_digest(image),
        "content_sha256": content_sha256(path),
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


# ---- comparison -----------------------------------------------------------------------------


@dataclass
class Comparison:
    verdict: str
    original: str
    replay: str
    original_digest: str | None = None
    replay_digest: str | None = None
    comparable: bool = True
    tolerance: int | None = None
    metrics: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    difference_image: str | None = None

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "original": self.original,
            "replay": self.replay,
            "original_digest": self.original_digest,
            "replay_digest": self.replay_digest,
            "comparable": self.comparable,
            "tolerance": self.tolerance,
            "metrics": self.metrics,
            "environment": self.environment,
            "reasons": self.reasons,
            "difference_image": self.difference_image,
        }

    def text(self) -> str:
        lines = [f"Verdict: {self.verdict.upper()}", f"Original: {self.original}", f"Replay:   {self.replay}"]
        if self.original_digest or self.replay_digest:
            lines.append(f"Pixel digest (hfpix/1): original {self.original_digest or '-'}")
            lines.append(f"                        replay   {self.replay_digest or '-'}")
        m = self.metrics
        if m:
            if "shape" in m:
                lines.append(f"Size: original {m['shape']['original']}, replay {m['shape']['replay']}")
            if "max_abs_diff" in m:
                lines.append(f"Max difference: {m['max_abs_diff']} of {m['levels']} levels"
                             + (f" (per channel {m['max_abs_diff_by_channel']})" if m.get("max_abs_diff_by_channel") else ""))
                lines.append(f"Mean difference: {m['mean_abs_diff']:.4g}; 99.9th percentile: {m['p999_abs_diff']}")
                lines.append(f"Differing pixels: {m['differing_pixels']} of {m['pixels']} ({100 * m['differing_fraction']:.3g}%)")
                if m.get("first_difference"):
                    fd = m["first_difference"]
                    lines.append(f"First difference at row {fd['row']}, column {fd['column']}")
        if self.tolerance is not None:
            lines.append(f"Tolerance: {self.tolerance} level(s)")
        if self.environment.get("differences"):
            lines.append("Environment differences:")
            for d in self.environment["differences"]:
                lines.append(f"  {d['key']}: original {d['original']!r}, replay {d['replay']!r}")
        elif self.environment.get("compared"):
            lines.append("Environment: same on both sides")
        for r in self.reasons:
            lines.append(f"Note: {r}")
        if self.difference_image:
            lines.append(f"Difference image: {self.difference_image}")
        return "\n".join(lines) + "\n"


def compare_images(a: Image, b: Image, *, tolerance: int | None = None) -> tuple[dict, bool]:
    """Metrics between two decoded images. The second value says whether they were comparable
    (same size, channels and bit depth)."""
    metrics: dict[str, Any] = {"shape": {"original": list(a.shape), "replay": list(b.shape)}}
    if a.shape != b.shape:
        return metrics, False
    levels = (1 << a.bit_depth) - 1
    diff = np.abs(a.samples.astype(np.int64) - b.samples.astype(np.int64))
    per_pixel = diff.max(axis=2)
    differing = int(np.count_nonzero(per_pixel))
    metrics.update({
        "levels": levels,
        "pixels": int(per_pixel.size),
        "max_abs_diff": int(diff.max()) if diff.size else 0,
        "max_abs_diff_by_channel": [int(x) for x in diff.reshape(-1, a.channels).max(axis=0)] if diff.size else [],
        "mean_abs_diff": float(diff.mean()) if diff.size else 0.0,
        "p999_abs_diff": int(np.percentile(per_pixel, 99.9)) if per_pixel.size else 0,
        "differing_pixels": differing,
        "differing_fraction": differing / per_pixel.size if per_pixel.size else 0.0,
        "within_tolerance": None if tolerance is None else bool(diff.max() <= tolerance) if diff.size else True,
    })
    if differing:
        rows, cols = np.nonzero(per_pixel)
        metrics["first_difference"] = {"row": int(rows[0]), "column": int(cols[0])}
    return metrics, True


def difference_image(a: Image, b: Image) -> np.ndarray:
    """|a - b| scaled so the largest difference is white; 8-bit, one channel per input channel."""
    diff = np.abs(a.samples.astype(np.int64) - b.samples.astype(np.int64))
    top = diff.max()
    if top == 0:
        return np.zeros(diff.shape, dtype=np.uint8)
    return (diff * 255 // top).astype(np.uint8)


# The environment keys that can move pixels, as "a.b" paths into a record's environment block.
ENVIRONMENT_KEYS = ("gpu.vendor", "gpu.renderer", "gpu.version", "gpu.glsl", "os.name", "os.version", "os.arch",
                    "jvm.version", "display.dither", "display.high_bit_depth_capture", "display.msaa",
                    "core.commit", "ffmpeg.version")


def compare_environments(original: dict | None, replay: dict | None) -> dict:
    """Which of ENVIRONMENT_KEYS differ between two environment blocks, plus the producer lines
    when passed under the key 'producer'."""
    if not isinstance(original, dict) or not isinstance(replay, dict):
        return {"compared": False, "differences": []}
    diffs = []
    for key in ENVIRONMENT_KEYS + ("producer.commit", "producer.version", "producer.dirty"):
        a = _dig(original, key)
        b = _dig(replay, key)
        if a != b and (a is not None or b is not None):
            diffs.append({"key": key, "original": a, "replay": b})
    return {"compared": True, "differences": diffs}


def _dig(obj: dict, path: str):
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def compare(original: str | Path, replay: str | Path, *, tolerance: int | None = None,
            original_environment: dict | None = None, replay_environment: dict | None = None,
            diff_out: str | Path | None = None) -> Comparison:
    """Compare two PNG files and give a verdict.

    ``identical`` when the hfpix/1 digests match. Otherwise ``equivalent`` only when a tolerance
    was given and the largest difference is within it; else ``different``. When both environments
    are given and equal, a difference is flagged as not explainable by hardware.
    """
    a = read_image(original)
    b = read_image(replay)
    da, db = pixel_digest(a), pixel_digest(b)
    result = Comparison("different", str(original), str(replay), da, db, tolerance=tolerance)
    result.environment = compare_environments(original_environment, replay_environment)
    if da == db:
        result.verdict = "identical"
        return result
    metrics, comparable = compare_images(a, b, tolerance=tolerance)
    result.metrics = metrics
    result.comparable = comparable
    if not comparable:
        # A size mismatch is the replay's export, not the picture: the same rule Studio applies.
        result.verdict = "cannot_check"
        result.reasons.append("the two images differ in size, channels or bit depth, so pixels were not compared")
        return result
    if tolerance is not None and metrics["within_tolerance"]:
        result.verdict = "equivalent"
    elif tolerance is None:
        result.reasons.append("no tolerance was given; pass one measured on named hardware to allow 'equivalent'")
    if result.environment.get("compared") and not result.environment["differences"]:
        result.reasons.append("the environments are the same, so this difference is a bug or a hidden input, not the GPU")
    if diff_out is not None:
        result.difference_image = str(write_png(diff_out, difference_image(a, b)))
    return result


def verify(record_path: str | Path, replay: str | Path, *, tolerance: int | None = None,
           diff_out: str | Path | None = None) -> Comparison:
    """Judge a replayed PNG against a record.

    With ``output.pixel_sha256`` in the record (format 1.1), the replay's digest is compared to it
    and the original file is not needed. Without it, the export beside the sidecar is read and
    compared pixel by pixel; when that file is missing too, the verdict is ``cannot_check``.
    The replay's own sidecar, when it has one, supplies its environment for the report.
    """
    found = hfrecord.read(record_path)
    if not found.get("found"):
        return Comparison("cannot_check", str(record_path), str(replay),
                          reasons=[f"no processing record for {record_path}"], comparable=False)
    rec = found["record"]
    pre = preflight(rec)
    out = rec.get("output") if isinstance(rec.get("output"), dict) else {}
    expected = out.get("pixel_sha256")
    replay_env = _environment_of(replay)
    original_env = {**(rec.get("environment") or {}), "producer": rec.get("producer") or {}}
    export = _export_file(found, record_path)
    if export is not None and export.is_file():
        result = compare(export, replay, tolerance=tolerance, original_environment=original_env,
                         replay_environment=replay_env, diff_out=diff_out)
        if isinstance(expected, str) and result.original_digest != expected:
            result.reasons.append("the export beside the record does not match the record's own pixel digest; "
                                  "it was edited or re-encoded after export")
    elif isinstance(expected, str):
        image = read_image(replay)
        digest = pixel_digest(image)
        result = Comparison("identical" if digest == expected else "different", str(record_path), str(replay),
                            expected, digest, tolerance=tolerance)
        result.environment = compare_environments(original_env, replay_env)
        if digest != expected:
            result.reasons.append("the original file was not found beside the record, so only the digests were "
                                  "compared and no difference metrics are available")
            w, h = out.get("width"), out.get("height")
            if isinstance(w, int) and isinstance(h, int) and (w, h) != (image.width, image.height):
                result.comparable = False
                result.verdict = "cannot_check"
                result.reasons.append(f"the record says {w}x{h} and the replay is {image.width}x{image.height}")
    else:
        return Comparison("cannot_check", str(record_path), str(replay), comparable=False,
                          reasons=["the record has no pixel digest (format 1.0) and the export is not beside it"]
                          + pre["reasons"])
    result.reasons.extend(pre["reasons"])
    return result


def _export_file(found: dict, record_path: str | Path) -> Path | None:
    check = found.get("output_check") if isinstance(found.get("output_check"), dict) else {}
    if isinstance(check.get("file"), str):
        return Path(check["file"])
    where = (found.get("found_in") or {}).get("path")
    if (found.get("found_in") or {}).get("kind") != "sidecar" and isinstance(where, str) and where.lower().endswith(".png"):
        return Path(where)  # the record was embedded in the export itself
    p = Path(record_path)
    name = p.name
    for suffix in (".hfrec.json", ".hfrec"):
        if name.endswith(suffix):
            return p.with_name(name[:-len(suffix)])
    return p if p.suffix.lower() == ".png" else None


def _environment_of(path: str | Path) -> dict | None:
    try:
        found = hfrecord.read(path)
    except (hfrecord.RecordError, OSError):
        return None
    if not found.get("found"):
        return None
    rec = found["record"]
    return {**(rec.get("environment") or {}), "producer": rec.get("producer") or {}}


# ---- preflight ------------------------------------------------------------------------------


def preflight(rec: dict) -> dict:
    """What the record itself says about replaying it, before anything is loaded.

    Returns ``{"can_replay", "exact", "reasons", "scope", "frames", "has_digest"}``. ``can_replay``
    is false only when nothing could be rebuilt (a legacy record, or no scene at all); ``exact``
    is false whenever the record names a reason the pixels may not come back the same.
    """
    reasons = hfrecord.replay_reasons(rec)
    out = rec.get("output") if isinstance(rec.get("output"), dict) else {}
    rep = rec.get("reproducibility") if isinstance(rec.get("reproducibility"), dict) else {}
    native = rec.get("native") if isinstance(rec.get("native"), dict) else {}
    has_scene = bool(native.get("studio_session")) or isinstance(rec.get("native_ref"), str)
    can = has_scene and not rec.get("legacy")
    if not has_scene:
        reasons = ["the record carries no scene to rebuild from"] + reasons
    frames = out.get("frames") if isinstance(out.get("frames"), int) else 1
    return {
        "can_replay": can,
        "exact": can and not reasons,
        "reasons": reasons,
        "scope": rep.get("replay_scope") or "one",
        "frames": frames,
        "has_digest": isinstance(out.get("pixel_sha256"), str),
        "has_frame_digests": isinstance(out.get("frame_digests"), list) and bool(out.get("frame_digests")),
    }


# ---- command line ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pyheliofits.reproduce", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("digest", help="the hfpix/1 pixel digest and content digest of a PNG")
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("compare", help="compare two PNGs")
    p.add_argument("original", type=Path)
    p.add_argument("replay", type=Path)
    p.add_argument("--tolerance", type=int, default=None, help="largest allowed difference in levels for 'equivalent'")
    p.add_argument("--diff", type=Path, default=None, help="write the difference image here")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("verify", help="judge a replayed PNG against an export's record")
    p.add_argument("record", type=Path, help="the .hfrec sidecar or the original export")
    p.add_argument("replay", type=Path)
    p.add_argument("--tolerance", type=int, default=None)
    p.add_argument("--diff", type=Path, default=None)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("preflight", help="what a record says about replaying it")
    p.add_argument("record", type=Path)
    p.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    if ns.cmd == "digest":
        d = digests(ns.file)
        if ns.json:
            print(json.dumps(d, indent=2))
        else:
            print(f"{d['pixel_sha256']}  pixel (hfpix/1, {d['width']}x{d['height']}x{d['channels']}, {d['bit_depth']}-bit)")
            print(f"{d['content_sha256']}  content (image chunks only)")
            print(f"{d['sha256']}  file")
        return 0
    if ns.cmd == "preflight":
        found = hfrecord.read(ns.record)
        if not found.get("found"):
            print(f"no processing record for {ns.record}", file=sys.stderr)
            return 2
        pre = preflight(found["record"])
        if ns.json:
            print(json.dumps(pre, indent=2))
        else:
            print("Exact replay possible" if pre["exact"] else ("Replay possible, not exact" if pre["can_replay"] else "Cannot replay"))
            for r in pre["reasons"]:
                print(f"  {r}")
            print(f"Default scope: {pre['scope']} ({pre['frames']} frame(s)); pixel digest: {'yes' if pre['has_digest'] else 'no'}")
        return 0 if pre["can_replay"] else 1
    if ns.cmd == "compare":
        result = compare(ns.original, ns.replay, tolerance=ns.tolerance, diff_out=ns.diff)
    else:
        result = verify(ns.record, ns.replay, tolerance=ns.tolerance, diff_out=ns.diff)
    print(json.dumps(result.to_dict(), indent=2) if ns.json else result.text(), end="")
    return 0 if result.verdict in ("identical", "equivalent") else 1


if __name__ == "__main__":
    sys.exit(main())
