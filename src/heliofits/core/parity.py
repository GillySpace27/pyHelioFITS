"""Decode parity: do HelioFITS Studio's numbers match sunpy's, plus the corrections Studio names?

The claim being tested is not "Studio equals raw sunpy". Studio makes a few deliberate
changes to what a file says, and each is a named correction (see docs/decode-parity.md).
Parity is "sunpy plus exactly the named corrections the dump says were applied". A number
that differs for any other reason is a finding.

Both sides write the same document, a decode dump (format ``hf-decode-dump/1``):

* ``reference_dump(path)`` is the sunpy side, built here.
* Studio's side is written by a Java check on the Mac (``DecodeParityDump``) and, later, by
  the MCP's ``frame.get``; the format is specified in docs/decode-parity.md.

``compare_dumps(studio, reference)`` then returns a report of named checks, each with the
tolerance it used and the basis for that tolerance. Run it from the command line:

    python -m heliofits.core.parity reference FILE [--plane N] [-o OUT.hfdump.json]
    python -m heliofits.core.parity compare STUDIO.hfdump.json [--source FILE] [--json]

sunpy is an optional dependency (``pip install "heliofits[parity]"``).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DUMP_FORMAT = "hf-decode-dump/1"

# The settings under which Studio's numbers are claimed to equal sunpy's. Each one is a
# display or convenience option in Studio that changes what a layer shows; a dump taken
# with any of them on is a different claim and the comparison refuses it.
PARITY_SETTINGS = {
    "normalize_radius": False,   # DisplaySettings.getNormalizeRadius: rescales the solar radius per wavelength
    "normalize_aia": False,      # DisplaySettings.getNormalizeAIA: divides AIA by the instrument response
    "time_mode": "observer",     # DisplaySettings.getTimeMode: Sun/Earth shift the time by light travel
    "filter": "none",            # no sequence or image filter (RHEF, MGN, ...) in the dump
}

INSTRUMENTS = ("AIA", "LASCO", "PUNCH", "ASPIICS", "SUVI")

# Named corrections Studio applies on top of what a header says. The dump lists the ones it
# applied; the reference applies the same ones through sunpy's numbers. Anything else that
# differs is a finding. (Source: FITSImage.java, FitsMetaData.java, LascoPointing.java in
# HelioFITS Studio, read 2026-11-02.)
CORRECTIONS = {
    "lasco_per_second": (
        "LASCO only, and only when Studio subtracts the monthly background: "
        "(BZERO + raw*BSCALE - OFFSET) / EXPTIME - background, bad pixels to NaN. "
        "Without the subtraction Studio leaves the frame as served, in DN."),
    "time_prefers_date_avg": (
        "Studio reads DATE-AVG, then DATE_AVG, then DATE_OBS, then DATE-OBS (LASCO: DATE-OBS + TIME-OBS). "
        "sunpy's Map.date reads DATE-OBS first."),
    "plane_out_of_range_is_zero": (
        "A plane index outside the file's cube is read as plane 0 rather than refused."),
    "lasco_borrowed_pointing": (
        "LASCO C2 frames from 2025-08-21 to 2025-08-31 with no pointing in the header take CROTA and CRPIX "
        "from a neighbouring frame (LascoPointing). The dump records the values it used in "
        "corrections[].params; the reference uses them in place of the header's."),
}


# ---------------------------------------------------------------------------
# The dump
# ---------------------------------------------------------------------------

@dataclass
class Dump:
    """One decoded frame: physical values, time, and where a grid of pixels lands in arcsec."""

    writer: dict
    source: dict                      # name, sha256, size
    instrument: str
    plane: int
    data: np.ndarray                  # float32, shape (ny, nx), row 0 = lowest y as in the file, bad = NaN
    unit: str
    settings: dict
    corrections: list[dict]           # [{"name": ..., "applied": bool, "params": {...}}]
    time: dict                        # {"keyword": ..., "iso": ...}  (UTC)
    grid_pixels: np.ndarray           # (N, 2) zero-based pixel centres (x = column, y = row)
    grid_arcsec: np.ndarray           # (N, 2) helioprojective (Tx, Ty) in arcsec
    background: np.ndarray | None = None  # per-second background Studio subtracted, same shape/orientation as data
    hdu: int = 0
    notes: list[str] = field(default_factory=list)

    def applied(self, name: str) -> dict | None:
        for c in self.corrections:
            if c["name"] == name and c.get("applied", True):
                return c
        return None


def _npy_bytes(arr: np.ndarray) -> bytes:
    import io
    buf = io.BytesIO()
    np.save(buf, np.ascontiguousarray(arr, dtype="<f4"), allow_pickle=False)
    return buf.getvalue()


def write_dump(dump: Dump, path: str | Path, *, inline: bool = False) -> Path:
    """Write ``NAME.hfdump.json`` and, unless ``inline``, ``NAME.hfdump.npy`` (and ``.bg.npy``) beside it."""
    path = Path(path)
    ny, nx = dump.data.shape

    def payload(arr, suffix):
        if inline:
            return {"b64": base64.b64encode(_npy_bytes(arr)).decode("ascii"), "encoding": "npy-float32-le"}
        npy = path.with_name(path.name.replace(".hfdump.json", "") + suffix)
        npy.write_bytes(_npy_bytes(arr))
        return {"file": npy.name, "encoding": "npy-float32-le"}

    doc = {
        "format": DUMP_FORMAT,
        "writer": dump.writer,
        "source": dump.source,
        "hdu": dump.hdu,
        "plane": dump.plane,
        "instrument": dump.instrument,
        "shape": [ny, nx],
        "orientation": "fits",
        "bad_pixels": "nan",
        "unit": dump.unit,
        "settings": dump.settings,
        "corrections": dump.corrections,
        "time": dump.time,
        "wcs": {"grid_pixels": dump.grid_pixels.tolist(), "grid_arcsec": dump.grid_arcsec.tolist()},
        "data": payload(dump.data, ".hfdump.npy"),
        "notes": dump.notes,
    }
    if dump.background is not None:
        doc["background"] = payload(dump.background, ".bg.hfdump.npy")
    path.write_text(json.dumps(doc, indent=1, allow_nan=False))
    return path


def _load_array(spec: dict, base: Path) -> np.ndarray:
    import io
    if "b64" in spec:
        return np.load(io.BytesIO(base64.b64decode(spec["b64"])), allow_pickle=False)
    return np.load(base.parent / spec["file"], allow_pickle=False)


def read_dump(path: str | Path) -> Dump:
    path = Path(path)
    doc = json.loads(path.read_text())
    if doc.get("format") != DUMP_FORMAT:
        raise ValueError(f"{path}: not a {DUMP_FORMAT} document (format = {doc.get('format')!r})")
    if doc.get("orientation") != "fits":
        raise ValueError(f"{path}: orientation {doc.get('orientation')!r} is not 'fits' (row 0 must be the lowest-y row)")
    data = _load_array(doc["data"], path).astype(np.float32, copy=False)
    if list(data.shape) != list(doc["shape"]):
        raise ValueError(f"{path}: array shape {data.shape} does not match shape {doc['shape']}")
    bg = _load_array(doc["background"], path).astype(np.float32, copy=False) if "background" in doc else None
    return Dump(
        writer=doc["writer"], source=doc["source"], instrument=doc["instrument"], plane=doc["plane"],
        data=data, unit=doc.get("unit", ""), settings=doc["settings"], corrections=doc["corrections"],
        time=doc["time"],
        grid_pixels=np.asarray(doc["wcs"]["grid_pixels"], dtype=float),
        grid_arcsec=np.asarray(doc["wcs"]["grid_arcsec"], dtype=float),
        background=bg, hdu=doc.get("hdu", 0), notes=doc.get("notes", []))


# ---------------------------------------------------------------------------
# The sunpy side
# ---------------------------------------------------------------------------

def instrument_of(header) -> str:
    """The five instrument families this test covers, from the keywords Studio itself reads."""
    instrume = str(header.get("INSTRUME", "")).strip()
    if "_" in instrume:
        instrume = instrume.split("_", 1)[0]  # Studio drops the suffix: AIA_3 -> AIA
    obs = str(header.get("OBSRVTRY", "")).strip()
    if instrume == "AIA":
        return "AIA"
    if instrume == "LASCO":
        return "LASCO"
    if obs == "PUNCH" or "PUNCH" in str(header.get("TELESCOP", "")).upper():
        return "PUNCH"
    if "ASPIICS" in instrume.upper() or "ASPIICS" in str(header.get("DETECTOR", "")).upper():
        return "ASPIICS"
    if instrume.startswith("GOES-R Series Solar Ultraviolet Imager") or instrume == "SUVI":
        return "SUVI"
    return instrume or "unknown"


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pick_hdu(hdul):
    """Studio's findHDU: the first compressed image, else the first image HDU that has data."""
    from astropy.io import fits
    for i, hdu in enumerate(hdul):
        if isinstance(hdu, fits.CompImageHDU):
            return i, hdu
    for i, hdu in enumerate(hdul):
        if isinstance(hdu, fits.ImageHDU | fits.PrimaryHDU) and hdu.header.get("NAXIS", 0) >= 2:
            return i, hdu
    raise ValueError("No image found")


_AXIS_CARD = re.compile(r"^(NAXIS|CTYPE|CRVAL|CRPIX|CDELT|CUNIT|CROTA)([3-9])$")
_MATRIX_CARD = re.compile(r"^(PC|CD)(\d)_(\d)$")


def _two_d_meta(header, ny, nx) -> dict:
    """The header as a dict sunpy can read as a single 2-D image (a cube plane drops its 3rd and later axes)."""
    meta = {}
    for card in header.cards:
        k = card.keyword
        if k in ("", "COMMENT", "HISTORY") or _AXIS_CARD.match(k):
            continue
        m = _MATRIX_CARD.match(k)
        if m and (int(m.group(2)) > 2 or int(m.group(3)) > 2):
            continue
        meta[k] = card.value
    meta["NAXIS"] = 2
    meta["NAXIS1"] = nx
    meta["NAXIS2"] = ny
    return meta


def _grid(ny: int, nx: int, n: int = 9) -> np.ndarray:
    xs = np.unique(np.round(np.linspace(0, nx - 1, n)).astype(int))
    ys = np.unique(np.round(np.linspace(0, ny - 1, n)).astype(int))
    return np.array([[x, y] for y in ys for x in xs], dtype=float)


def _time_studio_rule(header, instrument: str) -> tuple[str, str]:
    """(keyword, raw text) by Studio's chain, FitsMetaData.retrieveTime."""
    for k in ("DATE-AVG", "DATE_AVG", "DATE_OBS", "DATE-OBS"):
        if k in header and str(header[k]).strip():
            return k, str(header[k]).strip()
    raise ValueError("no observation time in the header")


def reference_dump(path: str | Path, plane: int = 0, *, like: Dump | None = None,
                   grid: int = 9, hdu: int | None = None) -> Dump:
    """What sunpy says about this frame, plus the named corrections ``like`` reports applying.

    ``like`` is the other side's dump. It supplies what only Studio can know (whether it
    subtracted a LASCO background, which background, any borrowed pointing); the numbers
    themselves come from sunpy. Without it the reference is plain sunpy, no corrections.
    """
    import astropy.units as u
    import sunpy
    import sunpy.map
    from astropy.io import fits
    path = Path(path)
    notes: list[str] = []
    corrections: list[dict] = []
    with fits.open(path, memmap=False) as hdul:
        if hdu is None:
            hdu, h = _pick_hdu(hdul)
        else:
            h = hdul[hdu]
        header = h.header.copy()
        raw = np.asarray(h.data)
    instrument = instrument_of(header)

    if raw.ndim > 2:
        ny, nx = raw.shape[-2:]
        planes = int(np.prod(raw.shape[:-2]))
        flat = raw.reshape(planes, ny, nx)
        if plane < 0 or plane >= planes:
            notes.append(f"plane {plane} is out of range for {planes} planes; read plane 0 as Studio does")
            corrections.append({"name": "plane_out_of_range_is_zero", "applied": True, "params": {"asked": plane}})
            plane = 0
        data2d = flat[plane]
    else:
        ny, nx = raw.shape
        data2d = raw
        plane = 0

    meta = _two_d_meta(header, ny, nx)
    if like is not None:
        borrowed = like.applied("lasco_borrowed_pointing")
        if borrowed:
            for k, v in borrowed["params"].items():
                meta[k] = v
            corrections.append(borrowed)

    base = np.asarray(data2d, dtype=np.float64)  # astropy has applied BZERO, BSCALE and BLANK
    smap = sunpy.map.Map(base, meta)
    data = np.array(smap.data, dtype=np.float64)

    unit = str(header.get("BUNIT", "")).strip()
    bg = None
    per_second = like.applied("lasco_per_second") if like is not None else None
    if per_second:
        if instrument != "LASCO":
            raise ValueError("the dump reports the LASCO per-second correction on a non-LASCO file")
        if like.background is None:
            raise ValueError("the dump reports a LASCO background subtraction but carries no background array")
        offset = float(header.get("OFFSET", 0.0))
        exposure = float(header.get("EXPTIME", 0.0))
        if not exposure > 0:
            raise ValueError("the dump reports the LASCO per-second correction but EXPTIME is not positive")
        bg = like.background
        data = (data - offset) / exposure - bg.astype(np.float64)
        unit = "DN/s"
        corrections.append({"name": "lasco_per_second", "applied": True,
                            "params": {"offset": offset, "exptime": exposure}})
    data32 = data.astype(np.float32)

    gp = _grid(ny, nx, grid)
    coords = smap.pixel_to_world(gp[:, 0] * u.pix, gp[:, 1] * u.pix)
    garc = np.column_stack([coords.Tx.to_value(u.arcsec), coords.Ty.to_value(u.arcsec)])

    key, text = _time_studio_rule(header, instrument)
    if instrument == "LASCO":
        text = text.replace("/", "-") + "T" + str(header.get("TIME_OBS", header.get("TIME-OBS"))).strip()
    if text.endswith("Z"):
        text = text[:-1]
    if len(text) == 10:
        text += "T00:00:00"
    # sunpy's own parse of the same keyword, not Studio's rule: the date the reference reports.
    sun_date = smap.date_average if key.startswith("DATE") and key.endswith("AVG") else smap.date
    iso = sun_date.utc.isot
    if key != "DATE-OBS":
        corrections.append({"name": "time_prefers_date_avg", "applied": True, "params": {"keyword": key}})
    notes.append(f"time keyword by Studio's rule: {key} = {text!r}")

    return Dump(
        writer={"name": "sunpy-reference", "sunpy": sunpy.__version__, "astropy": __import__("astropy").__version__},
        source={"name": path.name, "sha256": sha256_of(path), "size": path.stat().st_size},
        instrument=instrument, plane=plane, data=data32, unit=unit,
        settings=dict(PARITY_SETTINGS), corrections=corrections, time={"keyword": key, "iso": iso},
        grid_pixels=gp, grid_arcsec=garc, background=bg, hdu=hdu, notes=notes)


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------

# Tolerances. The data tolerance is derived from the arithmetic the frame went through, not
# from instrument noise; see docs/decode-parity.md "Tolerances". The WCS and time ones are
# stated with their own basis below.
F32_EPS = float(np.finfo(np.float32).eps)  # 1.19e-7
DATA_RTOL_FLOAT = 8 * F32_EPS              # ASSUMED headroom: 8 ulp for a chain of at most three float operations
TIME_TOL_MS = 1.0                          # JHVTime stores whole milliseconds (FitsMetaData uses JHVTime.milli)
WCS_TOL_ARCSEC = 1e-3                      # ASSUMED, provisional: replace with the deviation the first real runs show


@dataclass
class Check:
    name: str
    ok: bool
    value: object
    tolerance: object
    basis: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": bool(self.ok), "value": self.value, "tolerance": self.tolerance,
                "basis": self.basis, "detail": self.detail}


@dataclass
class Report:
    source: dict
    instrument: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "source": self.source, "instrument": self.instrument,
                "checks": [c.as_dict() for c in self.checks]}

    def text(self) -> str:
        lines = [f"{'PASS' if self.ok else 'FAIL'}  {self.source.get('name')}  ({self.instrument})"]
        for c in self.checks:
            lines.append(f"  {'ok  ' if c.ok else 'FAIL'} {c.name}: {c.value} (tolerance {c.tolerance}) {c.detail}".rstrip())
        return "\n".join(lines)


def data_tolerance(ref: np.ndarray, *, integer_exact: bool) -> tuple[float, float, str]:
    """(rtol, atol, basis) for comparing two float32 decodes of the same frame."""
    scale = float(np.abs(ref[np.isfinite(ref)]).max()) if np.isfinite(ref).any() else 0.0  # +-inf must not make atol infinite
    if integer_exact:
        return 0.0, 0.0, ("values are whole numbers below 2^24 and no arithmetic touched them, so float32 holds them "
                          "exactly and any difference is a defect")
    return DATA_RTOL_FLOAT, DATA_RTOL_FLOAT * scale, (
        f"float32 rounding: rtol = atol/max|ref| = 8 x float32 epsilon ({F32_EPS:.3g}); the 8 is assumed headroom "
        "for at most three float operations, not measured")


def _is_integer_exact(ref: np.ndarray, corrections: list[dict]) -> bool:
    if any(c.get("applied", True) and c["name"] == "lasco_per_second" for c in corrections):
        return False
    finite = ref[np.isfinite(ref)]
    if finite.size == 0:
        return False
    return bool(np.all(finite == np.round(finite)) and np.abs(finite).max() < 2 ** 24)


def _flip_hint(studio: np.ndarray, ref: np.ndarray, rtol: float, atol: float) -> str:
    for label, cand in (("flipped vertically", studio[::-1, :]), ("flipped horizontally", studio[:, ::-1]),
                        ("transposed", studio.T if studio.T.shape == ref.shape else None)):
        if cand is not None and cand.shape == ref.shape and np.allclose(cand, ref, rtol=rtol, atol=atol, equal_nan=True):
            return f"Studio's array matches the reference {label}"
    return ""


def compare_dumps(studio: Dump, ref: Dump, *, wcs_tol_arcsec: float = WCS_TOL_ARCSEC,
                  time_tol_ms: float = TIME_TOL_MS) -> Report:
    """Named checks of one Studio dump against the sunpy reference built from the same file."""
    from astropy.time import Time

    checks: list[Check] = []

    def add(*a, **k):
        checks.append(Check(*a, **k))

    add("same_file", studio.source.get("sha256") == ref.source.get("sha256"),
        studio.source.get("sha256", "")[:12], ref.source.get("sha256", "")[:12],
        "both dumps name the file by SHA-256")
    bad_settings = {k: studio.settings.get(k) for k, v in PARITY_SETTINGS.items() if studio.settings.get(k) != v}
    add("settings_in_scope", not bad_settings, bad_settings or "as required", PARITY_SETTINGS,
        "parity is only claimed with these Studio options at their defaults")
    add("same_plane", studio.plane == ref.plane, studio.plane, ref.plane, "plane index")

    add("shape", studio.data.shape == ref.data.shape, list(studio.data.shape), list(ref.data.shape),
        "pixel counts must match")
    if studio.data.shape == ref.data.shape:
        smask, rmask = np.isnan(studio.data), np.isnan(ref.data)
        mism = int(np.count_nonzero(smask != rmask))
        add("bad_pixels", mism == 0, mism, 0, "NaN positions must agree exactly (Studio's BAD_PIXEL sentinel is written as NaN)")
        rtol, atol, basis = data_tolerance(ref.data, integer_exact=_is_integer_exact(ref.data, ref.corrections))
        close = np.isclose(studio.data, ref.data, rtol=rtol, atol=atol, equal_nan=True)
        both = np.isfinite(studio.data) & np.isfinite(ref.data)
        worst = float(np.abs(studio.data[both].astype(np.float64) - ref.data[both].astype(np.float64)).max()) \
            if both.any() else float("nan")
        hint = "" if close.all() else _flip_hint(studio.data, ref.data, rtol, atol)
        add("data", bool(close.all()), {"max_abs_difference": worst, "pixels_outside": int((~close).sum())},
            {"rtol": rtol, "atol": atol}, basis, hint)
    if studio.unit or ref.unit:
        add("unit", studio.unit.strip() == ref.unit.strip(), studio.unit, ref.unit, "BUNIT, or DN/s after the LASCO correction")

    t_s, t_r = Time(studio.time["iso"], scale="utc"), Time(ref.time["iso"], scale="utc")
    dt_ms = abs((t_s - t_r).to_value("s")) * 1000
    add("time", dt_ms <= time_tol_ms, dt_ms, time_tol_ms,
        "JHVTime stores whole milliseconds", f"Studio read {studio.time.get('keyword')}, reference {ref.time.get('keyword')}")

    if studio.grid_pixels.shape == ref.grid_pixels.shape and np.array_equal(studio.grid_pixels, ref.grid_pixels):
        d = np.abs(studio.grid_arcsec - ref.grid_arcsec)
        add("wcs_grid_arcsec", bool(d.max() <= wcs_tol_arcsec), float(d.max()), wcs_tol_arcsec,
            "ASSUMED and provisional: no measured basis yet; replace with the deviations the first real runs show",
            f"{len(ref.grid_pixels)} pixel centres, zero-based, x = column, y = row")
    else:
        add("wcs_grid_arcsec", False, "grids differ", "identical grids",
            "the two dumps must sample the same pixel centres")

    extra = [c["name"] for c in studio.corrections if c.get("applied", True)
             and c["name"] not in CORRECTIONS]
    add("only_named_corrections", not extra, extra or "none", "names in CORRECTIONS",
        "a correction Studio applied that the test has no name for is a finding")
    return Report(source=ref.source, instrument=ref.instrument, checks=checks)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m heliofits.core.parity", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reference", help="write the sunpy reference dump for a FITS file")
    r.add_argument("file")
    r.add_argument("--plane", type=int, default=0)
    r.add_argument("--like", help="a Studio dump whose corrections the reference should apply")
    r.add_argument("-o", "--out")
    c = sub.add_parser("compare", help="compare a Studio dump with the sunpy reference of the same file")
    c.add_argument("studio_dump")
    c.add_argument("--source", help="the FITS file; default: the path beside the dump named in the dump's source.name")
    c.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)

    if ns.cmd == "reference":
        like = read_dump(ns.like) if ns.like else None
        d = reference_dump(ns.file, ns.plane, like=like)
        out = Path(ns.out) if ns.out else Path(ns.file).with_name(Path(ns.file).name + ".ref.hfdump.json")
        write_dump(d, out)
        print(out)
        return 0

    studio = read_dump(ns.studio_dump)
    src = Path(ns.source) if ns.source else Path(ns.studio_dump).with_name(studio.source["name"])
    ref = reference_dump(src, studio.plane, like=studio)
    rep = compare_dumps(studio, ref)
    print(json.dumps(rep.as_dict(), indent=1) if ns.json else rep.text())
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
