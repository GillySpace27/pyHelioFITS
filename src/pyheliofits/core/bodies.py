"""Which solar-system bodies are in a frame's field: the per-frame bodies tag.

The library answers "a date without the Moon in PUNCH" from this tag, the way HelioFITS Studio's
identifier and transit index answer it from the ephemeris. A body is in the field when the frame's
own WCS puts it on the image (between the edges of the pixel grid) and the Sun does not hide it.
It is not a test of the pixel values: a body behind an occulter or in a NaN corner still counts.

The observer is the one the header gives (HGLN_OBS, HGLT_OBS, DSUN_OBS and the other keys sunpy
reads), seen at the header's observation time. Positions come from sunpy's
get_body_heliographic_stonyhurst with the observer, so light travel time is included, and from
astropy's current solar_system_ephemeris (the built-in one unless a caller sets another).

sunpy is needed for the solar frames. Without it, tag() raises Unavailable and the library says so.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

# Earth is a body only for an observer away from it (STEREO, Solar Orbiter); the Sun is the frame.
BODIES = ("moon", "mercury", "venus", "earth", "mars", "jupiter", "saturn", "uranus", "neptune")
NEAR_EARTH_KM = 1.0e6  # an observer within this of Earth's centre does not see Earth as a body
METHOD = "wcs-footprint-1"  # bump when the rule changes, so stale tags can be found
NEEDS_SUNPY = "the per-frame bodies tag needs sunpy (pip install 'pyheliofits[archives]')"


class Unavailable(RuntimeError):
    """sunpy is not installed, so no frame can be tagged."""


@dataclass
class Placement:
    body: str
    x: float              # pixel, 0-based, as astropy's world_to_pixel gives it
    y: float
    elongation_deg: float  # angle from the Sun's centre, seen by the frame's observer
    in_field: bool
    hidden: bool          # behind the solar disk

    def to_dict(self) -> dict:
        return {"body": self.body, "x": round(self.x, 2), "y": round(self.y, 2),
                "elongation_deg": round(self.elongation_deg, 4), "in_field": self.in_field,
                "hidden": self.hidden}


def tag(header) -> list[Placement] | None:
    """Every body's place in this frame, or None when the header has no solar sky WCS or no
    observer (nothing to say about it). Raises Unavailable without sunpy."""
    try:
        import astropy.units as u
        from astropy.coordinates import SkyCoord
        from astropy.wcs import WCS
        from sunpy.coordinates import Helioprojective, get_body_heliographic_stonyhurst
        from sunpy.coordinates.wcs_utils import solar_wcs_frame_mapping
    except ImportError as exc:
        raise Unavailable(NEEDS_SUNPY) from exc

    nx, ny = header.get("NAXIS1"), header.get("NAXIS2")
    if not nx or not ny:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # FITSFixedWarning and sunpy's missing-observer notes
        try:
            wcs = WCS(header, naxis=2)
        except (ValueError, KeyError, MemoryError):
            return None
        frame = solar_wcs_frame_mapping(wcs)
    if not isinstance(frame, Helioprojective):
        return None
    observer, time = frame.observer, frame.obstime
    if observer is None or isinstance(observer, str) or time is None:
        return None

    obs_km = observer.radius.to_value(u.km)
    rsun_km = frame.rsun.to_value(u.km)
    sun_radius = math.asin(min(1.0, rsun_km / obs_km))
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if "earth" in BODIES:
            earth = get_body_heliographic_stonyhurst("earth", time)
            near_earth = SkyCoord(earth).separation_3d(SkyCoord(observer)).to_value(u.km) < NEAR_EARTH_KM
        for body in BODIES:
            if body == "earth" and near_earth:
                continue
            where = get_body_heliographic_stonyhurst(body, time, observer=observer)
            hpc = SkyCoord(where).transform_to(frame)
            tx, ty = hpc.Tx.to_value(u.rad), hpc.Ty.to_value(u.rad)
            elongation = math.acos(max(-1.0, min(1.0, math.cos(tx) * math.cos(ty))))
            hidden = elongation < sun_radius and hpc.distance.to_value(u.km) > obs_km
            x, y = (float(v) for v in wcs.world_to_pixel(hpc))
            on_grid = math.isfinite(x) and math.isfinite(y) and -0.5 <= x < nx - 0.5 and -0.5 <= y < ny - 0.5
            out.append(Placement(body, x, y, math.degrees(elongation), on_grid and not hidden, hidden))
    return out


def names(placements: list[Placement] | None) -> list[str]:
    """The bodies in the field, in BODIES order."""
    return [p.body for p in placements or [] if p.in_field]
