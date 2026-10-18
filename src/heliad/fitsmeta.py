"""What a FITS file is, read from its headers: dataset, observation time, version.

Missions disagree on keywords, so every field has a fallback chain. Anything
this module cannot work out stays None rather than being guessed; the checks
module reports what is missing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from astropy.io import fits
from astropy.time import Time

# Bump whenever a change here can move a file to a different dataset or
# observation. The index records the version its keys were built with, and
# `heliad reindex` rebuilds them when the two differ.
RULES_VERSION = 3

# Keywords copied into the index for every file, so readers can filter and
# display without opening the FITS file.
INDEXED_KEYWORDS = (
    "OBSRVTRY", "TELESCOP", "INSTRUME", "DETECTOR", "WAVELNTH", "WAVEUNIT", "WAVE_STR",
    "LEVEL", "LVL_NUM", "TYPECODE", "OBSCODE", "FILEVRSN", "DATE-OBS", "DATE_OBS",
    "DATE-BEG", "DATE-END", "DATE-AVG", "TIME-OBS", "TIMESYS", "BTYPE", "EXPTIME", "NAXIS1", "NAXIS2", "CTYPE1", "CTYPE2",
    "CUNIT1", "CUNIT2", "CDELT1", "CDELT2", "CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2",
    "CROTA2", "PC1_1", "PC1_2", "PC2_1", "PC2_2", "RSUN_OBS", "RSUN_REF", "RSUN_ARC",
    "R_SUN", "DSUN_OBS", "QUALITY", "OBS_MODE",
)

_WAVE_TO_ANGSTROM = {"angstrom": 1.0, "a": 1.0, "nm": 10.0, "um": 1e4, "m": 1e10}
_VERSION_IN_NAME = re.compile(r"[_-]v(\d+[a-z]?|[a-z]{1,2}\d*|\d+(?:\.\d+)+)\.(?:fits|fts|fit)(?:\.gz)?$", re.I)
_LEVEL_IN_NAME = re.compile(r"(?:^|[_.-])l(?:ev)?([0-9](?:\.[0-9])?)(?:[_.-]|$)", re.I)
_PUNCH_NAME = re.compile(r"^PUNCH_L(\w)_([A-Z0-9]{3})", re.I)
_AIA_CAMERA = re.compile(r"^AIA_\d$", re.I)
_ASPIICS_SEQUENCE = re.compile(r"(?:^|_)(\d{12})(?=[_.])")


@dataclass
class FitsMeta:
    observatory: str | None
    instrument: str | None
    detector: str | None
    product: str | None
    level: str | None
    wavelength: float | None
    date_obs: str | None
    version: str | None
    header: dict = field(default_factory=dict)
    bad_cards: list[str] = field(default_factory=list)
    sequence: str | None = None  # an acquisition id that outlives reprocessing (ASPIICS)

    @property
    def dataset_key(self) -> str:
        parts = [self.observatory, self.instrument, self.detector, self.product, self.level]
        wave = None if self.wavelength is None else f"{self.wavelength:g}"
        return "/".join(_norm(p) for p in [*parts, wave])

    @property
    def obs_key(self) -> str:
        if self.sequence:
            return f"{self.dataset_key}|seq:{self.sequence}"
        return f"{self.dataset_key}|{self.date_obs}"

    @property
    def label(self) -> str:
        bits = [self.observatory, self.instrument]
        if self.detector and self.detector != self.instrument:
            bits.append(self.detector)
        if self.wavelength is not None:
            bits.append(f"{self.wavelength:g} Å")
        if self.product:
            bits.append(self.product)
        if self.level:
            bits.append(f"L{self.level}")
        seen, out = set(), []
        for b in bits:
            if b and b.upper() not in seen:
                seen.add(b.upper())
                out.append(b)
        return " ".join(out) or "Unknown"


def _norm(value: str | None) -> str:
    if value is None or value == "":
        return "-"
    return re.sub(r"[^a-z0-9.+]+", "-", str(value).strip().lower()).strip("-") or "-"


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def read_headers(path: Path) -> tuple[fits.Header, list[str]]:
    """Primary header merged with the first image extension (compressed files keep WCS there).

    Also returns the keywords of cards astropy could not parse as written. Those
    are repaired the way astropy's verify('silentfix') would (AIA level 1 files
    from JSOC carry OSCNMEAN = nan, for example), or dropped if that fails.
    """
    merged = fits.Header()
    bad: list[str] = []
    with fits.open(path, memmap=False, lazy_load_hdus=True) as hdul:
        for i, hdu in enumerate(hdul):
            if i > 1:
                break
            for card in hdu.header.cards:
                if card.keyword in ("", "COMMENT", "HISTORY"):
                    continue
                try:
                    value = card.value
                except fits.VerifyError:
                    bad.append(card.keyword)
                    try:
                        card.verify("silentfix")
                        value = card.value
                    except (fits.VerifyError, ValueError):
                        continue
                merged[card.keyword] = value
    return merged, bad


def _json_value(value):
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


def _wavelength(header: fits.Header) -> float | None:
    wave = header.get("WAVELNTH")
    if wave is None or isinstance(wave, str) and not wave.strip():
        return None
    try:
        wave = float(wave)
    except (TypeError, ValueError):
        return None
    if wave <= 0:
        return None
    unit = str(header.get("WAVEUNIT", "angstrom")).strip().lower()
    return round(wave * _WAVE_TO_ANGSTROM.get(unit, 1.0), 4)


def _date_obs(header: fits.Header) -> str | None:
    for key in ("DATE-OBS", "DATE_OBS", "DATE-BEG", "DATE-AVG"):
        raw = _clean(header.get(key))
        if not raw:
            continue
        if "T" not in raw and _clean(header.get("TIME-OBS")):
            raw = f"{raw}T{_clean(header.get('TIME-OBS'))}"
        try:
            return Time(raw.rstrip("Z"), format="isot", scale="utc").isot
        except ValueError:
            try:
                return Time(raw.rstrip("Z")).isot
            except ValueError:
                continue
    return None


def _level(header: fits.Header, name: str) -> str | None:
    for key in ("LEVEL", "LVL_NUM"):
        value = header.get(key)
        if value is not None and str(value).strip() != "":
            text = str(value).strip()
            try:
                number = float(text)
                return f"{number:g}"
            except ValueError:
                return text.upper().lstrip("L")
    match = _LEVEL_IN_NAME.search(name)
    return match.group(1) if match else None


def _product(header: fits.Header, name: str) -> str | None:
    typecode, obscode = _clean(header.get("TYPECODE")), _clean(header.get("OBSCODE"))
    if typecode:
        return f"{typecode}{obscode or ''}".upper()
    match = _PUNCH_NAME.match(name)
    if match:
        return match.group(2).upper()
    # ASPIICS L3 says what each image is only in BTYPE ('Polarized brightness',
    # 'Polarization angle'; total brightness leaves it out). Elsewhere BTYPE is a
    # generic quantity (EUI writes 'Flux') and would only clutter the label.
    if str(header.get("INSTRUME", "")).strip().upper() == "ASPIICS":
        return _clean(header.get("BTYPE"))
    return None


def _sequence(header: fits.Header, name: str) -> str | None:
    """The acquisition id of an ASPIICS file, which both of its releases keep.

    Release 1.0 dates a polarized-brightness sequence by its last frame and
    release 1.1 by its first, so DATE-OBS cannot pair them; the 12-digit id in
    the filename (CYCLE_ID plus a 4-digit sequence number) can. The header
    holds only the 8-digit CYCLE_ID, which can cover more than one sequence.
    """
    if str(header.get("INSTRUME", "")).strip().upper() != "ASPIICS":
        return None
    match = _ASPIICS_SEQUENCE.search(name)
    return match.group(1) if match else None


def _version(header: fits.Header, name: str) -> str | None:
    value = _clean(header.get("FILEVRSN"))
    if value:
        return value
    match = _VERSION_IN_NAME.search(name)
    return match.group(1) if match else None


def read(path: Path) -> FitsMeta:
    path = Path(path)
    header, bad_cards = read_headers(path)
    return from_header(header, path.name, bad_cards)


def from_header(header, name: str, bad_cards: list[str] | None = None) -> FitsMeta:
    """The same as read(), from headers already in hand (a dict of the indexed keywords
    works, which is how reindex rebuilds keys for a file that is no longer readable)."""
    if not isinstance(header, fits.Header):
        merged = fits.Header()
        for k, v in header.items():
            merged[k] = v
        header = merged
    observatory = _clean(header.get("OBSRVTRY")) or _clean(header.get("TELESCOP"))
    instrument = _clean(header.get("INSTRUME"))
    if observatory and instrument and observatory.upper() == instrument.upper():
        telescope = _clean(header.get("TELESCOP"))
        if telescope and telescope.upper() != instrument.upper():
            observatory = telescope
    if observatory and "/" in observatory:  # e.g. TELESCOP = 'SDO/AIA'
        head, _, tail = observatory.partition("/")
        observatory = head
        instrument = instrument or tail
    if instrument and _AIA_CAMERA.match(instrument):  # AIA_1..AIA_4 name the camera, not the instrument
        instrument = "AIA"
    detector = _clean(header.get("DETECTOR"))
    if detector and detector.upper() in {(v or "").upper() for v in (observatory, instrument)}:
        detector = None  # PUNCH and ASPIICS repeat the mission name here, and not in every version
    return FitsMeta(
        observatory=observatory,
        instrument=instrument,
        detector=detector,
        product=_product(header, name),
        level=_level(header, name),
        wavelength=_wavelength(header),
        date_obs=_date_obs(header),
        version=_version(header, name),
        header={k: _json_value(header[k]) for k in INDEXED_KEYWORDS if k in header},
        bad_cards=list(bad_cards or []),
        sequence=_sequence(header, name),
    )


def version_sort_key(version: str | None) -> tuple:
    """Natural ordering for FILEVRSN-like strings: 0k < 0l < 1a, 1.2 < 1.10, None oldest."""
    if version is None:
        return ((-1,),)
    parts = re.findall(r"\d+|[a-z]+", version.lower())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts) or ((-1,),)


class IncompleteFile(ValueError):
    """The file is shorter than its own headers say: a download cut off part way."""


def expected_size(path: Path) -> int | None:
    """Bytes a FITS file must have, summed from each HDU's header (FITS 4.0, section 4.4.1).

    Reads only the headers. Returns None when the file cannot be measured this way
    (gzip-compressed, or not FITS at all), so the caller skips the check.
    """
    path = Path(path)
    if path.name.lower().endswith(".gz"):
        return None
    total, size = 0, path.stat().st_size
    with open(path, "rb") as fh:
        while total < size:
            fh.seek(total)
            cards: dict[str, str] = {}
            blocks = 0
            while True:
                block = fh.read(2880)
                if len(block) < 2880:
                    return total + (blocks + 1) * 2880  # header itself is cut off
                blocks += 1
                done = False
                for i in range(0, 2880, 80):
                    card = block[i:i + 80].decode("ascii", "replace")
                    key = card[:8].strip()
                    if key == "END":
                        done = True
                        break
                    if card[8:10] == "= ":
                        cards[key] = card[10:].split("/", 1)[0].strip()
                if done:
                    break
            if total == 0 and "SIMPLE" not in cards:
                return None
            try:
                bitpix = abs(int(cards.get("BITPIX", "8")))
                naxis = int(cards.get("NAXIS", "0"))
                dims = [int(cards.get(f"NAXIS{i}", "0")) for i in range(1, naxis + 1)]
                pcount = int(cards.get("PCOUNT", "0"))
                gcount = int(cards.get("GCOUNT", "1"))
            except ValueError:
                return None
            if cards.get("GROUPS", "") == "T" and dims and dims[0] == 0:
                dims = dims[1:]  # random groups: NAXIS1 = 0 is a marker
            elements = 1
            for d in dims:
                elements *= d
            data = bitpix // 8 * gcount * (pcount + elements) if naxis else 0
            total += blocks * 2880 + -(-data // 2880) * 2880
    return total


def check_complete(path: Path) -> None:
    """Raise IncompleteFile when a FITS file is shorter than its headers require."""
    want = expected_size(path)
    have = Path(path).stat().st_size
    if want is not None and have < want:
        raise IncompleteFile(f"{Path(path).name} has {have} bytes; its headers need {want}")
