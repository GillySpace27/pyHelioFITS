"""Header and WCS checks run on every file as it arrives.

Each check returns findings, never raises: a file with a bad header still goes
into the library, with the problem recorded beside it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import astropy.units as u

from .fitsmeta import FitsMeta

ROTATION_TOLERANCE_DEG = 0.01
RSUN_TOLERANCE = 0.005  # fractional


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # info | warning | error
    message: str


def _num(header: dict, key: str) -> float | None:
    value = header.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def check_identity(meta: FitsMeta) -> list[Finding]:
    out = []
    if meta.date_obs is None:
        out.append(Finding("no_date_obs", "error", "No usable DATE-OBS, DATE_OBS or DATE-BEG."))
    if meta.instrument is None:
        out.append(Finding("no_instrument", "warning", "No INSTRUME keyword; the dataset is a guess."))
    return out


def check_cards(meta: FitsMeta) -> list[Finding]:
    if not meta.bad_cards:
        return []
    return [Finding(
        "unparsable_card", "warning",
        f"Not valid FITS as written, repaired on read: {', '.join(meta.bad_cards)}.",
    )]


def check_time_scale(header: dict) -> list[Finding]:
    timesys = str(header.get("TIMESYS", "")).strip().upper()
    if not timesys:
        return [Finding("timesys_missing", "info", "No TIMESYS; DATE-OBS assumed to be UTC.")]
    if timesys not in ("UTC", "UT"):
        return [Finding(
            "timesys_not_utc", "warning",
            f"TIMESYS is {timesys}; DATE-OBS is indexed as written, not converted to UTC.",
        )]
    return []


def check_units(header: dict) -> list[Finding]:
    out = []
    for key in ("CUNIT1", "CUNIT2"):
        raw = header.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        try:
            u.Unit(text, format="fits")
        except ValueError:
            out.append(Finding(
                "cunit_nonstandard", "warning",
                f"{key} = '{text}' is not a FITS-standard unit string (readers may scale it wrongly).",
            ))
    return out


def check_rotation(header: dict) -> list[Finding]:
    crota = _num(header, "CROTA2")
    pc11, pc21, pc22 = (_num(header, k) for k in ("PC1_1", "PC2_1", "PC2_2"))
    if crota is None or None in (pc11, pc21, pc22):
        return []
    cdelt1, cdelt2 = _num(header, "CDELT1") or 1.0, _num(header, "CDELT2") or 1.0
    # FITS WCS Paper II: PC2_1 = sin(rho) * CDELT1 / CDELT2 and PC2_2 = cos(rho)
    rho = math.degrees(math.atan2(pc21 * abs(cdelt2) / abs(cdelt1), pc22))
    diff = (rho - crota + 180.0) % 360.0 - 180.0
    if abs(diff) > ROTATION_TOLERANCE_DEG:
        return [Finding(
            "crota_pc_disagree", "warning",
            f"CROTA2 = {crota:.4f} deg but the PC matrix implies {rho:.4f} deg.",
        )]
    return []


def check_solar_radius(header: dict) -> list[Finding]:
    out = []
    rsun_obs = _num(header, "RSUN_OBS") or _num(header, "RSUN_ARC")
    rsun_ref, dsun = _num(header, "RSUN_REF"), _num(header, "DSUN_OBS")
    if rsun_obs and rsun_ref and dsun:
        expected = math.degrees(math.asin(rsun_ref / dsun)) * 3600.0
        if abs(rsun_obs - expected) / expected > RSUN_TOLERANCE:
            out.append(Finding(
                "rsun_distance_disagree", "warning",
                f"RSUN_OBS = {rsun_obs:.2f} arcsec but RSUN_REF/DSUN_OBS gives {expected:.2f} arcsec.",
            ))
    r_sun_px, cdelt1 = _num(header, "R_SUN"), _num(header, "CDELT1")
    cunit1 = str(header.get("CUNIT1", "arcsec")).strip().lower()
    if rsun_obs and r_sun_px and cdelt1 and cunit1 in ("arcsec", "arcsecs"):
        implied = r_sun_px * abs(cdelt1)
        if abs(implied - rsun_obs) / rsun_obs > RSUN_TOLERANCE:
            out.append(Finding(
                "rsun_plate_scale_disagree", "warning",
                f"R_SUN x CDELT1 = {implied:.2f} arcsec but RSUN_OBS = {rsun_obs:.2f} arcsec.",
            ))
    return out


def run(meta: FitsMeta) -> list[Finding]:
    header = meta.header
    return [
        *check_identity(meta),
        *check_cards(meta),
        *check_time_scale(header),
        *check_units(header),
        *check_rotation(header),
        *check_solar_radius(header),
    ]
