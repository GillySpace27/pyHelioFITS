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

# Keywords copied into the index for every file, so readers can filter and
# display without opening the FITS file.
INDEXED_KEYWORDS = (
    "OBSRVTRY", "TELESCOP", "INSTRUME", "DETECTOR", "WAVELNTH", "WAVEUNIT", "WAVE_STR",
    "LEVEL", "LVL_NUM", "TYPECODE", "OBSCODE", "FILEVRSN", "DATE-OBS", "DATE_OBS",
    "DATE-BEG", "DATE-END", "TIMESYS", "BTYPE", "EXPTIME", "NAXIS1", "NAXIS2", "CTYPE1", "CTYPE2",
    "CUNIT1", "CUNIT2", "CDELT1", "CDELT2", "CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2",
    "CROTA2", "PC1_1", "PC1_2", "PC2_1", "PC2_2", "RSUN_OBS", "RSUN_REF", "RSUN_ARC",
    "R_SUN", "DSUN_OBS", "QUALITY", "OBS_MODE",
)

_WAVE_TO_ANGSTROM = {"angstrom": 1.0, "a": 1.0, "nm": 10.0, "um": 1e4, "m": 1e10}
_VERSION_IN_NAME = re.compile(r"[_-]v(\d+[a-z]?|[a-z]{1,2}\d*|\d+(?:\.\d+)+)\.(?:fits|fts|fit)(?:\.gz)?$", re.I)
_LEVEL_IN_NAME = re.compile(r"(?:^|[_.-])l(?:ev)?([0-9](?:\.[0-9])?)(?:[_.-]|$)", re.I)
_PUNCH_NAME = re.compile(r"^PUNCH_L(\w)_([A-Z0-9]{3})", re.I)
_AIA_CAMERA = re.compile(r"^AIA_\d$", re.I)


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

    @property
    def dataset_key(self) -> str:
        parts = [self.observatory, self.instrument, self.detector, self.product, self.level]
        wave = None if self.wavelength is None else f"{self.wavelength:g}"
        return "/".join(_norm(p) for p in [*parts, wave])

    @property
    def obs_key(self) -> str:
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


def _version(header: fits.Header, name: str) -> str | None:
    value = _clean(header.get("FILEVRSN"))
    if value:
        return value
    match = _VERSION_IN_NAME.search(name)
    return match.group(1) if match else None


def read(path: Path) -> FitsMeta:
    path = Path(path)
    header, bad_cards = read_headers(path)
    name = path.name
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
        bad_cards=bad_cards,
    )


def version_sort_key(version: str | None) -> tuple:
    """Natural ordering for FILEVRSN-like strings: 0k < 0l < 1a, 1.2 < 1.10, None oldest."""
    if version is None:
        return ((-1,),)
    parts = re.findall(r"\d+|[a-z]+", version.lower())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts) or ((-1,),)
