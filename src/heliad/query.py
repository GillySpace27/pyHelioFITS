"""A structured, re-runnable description of what to search for.

The CONNECT manifest stored queries as printed sunpy objects, which cannot be
run again. A Query is plain data: it round-trips through JSON exactly, and
to_fido() rebuilds the sunpy search from it whenever it is needed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Query:
    start: str                       # ISO 8601 UTC
    end: str                         # ISO 8601 UTC
    instrument: str | None = None
    wavelength: float | None = None  # Angstrom; single passband
    wavelength_max: float | None = None
    source: str | None = None        # sunpy attrs.Source, e.g. "SDO"
    provider: str | None = None      # sunpy attrs.Provider, e.g. "JSOC"
    detector: str | None = None
    level: str | None = None
    physobs: str | None = None
    sample_s: float | None = None    # cadence; one record per this many seconds
    jsoc_series: str | None = None   # e.g. "aia.lev1_euv_12s"; JSOC export instead of VSO
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, no nulls, so equal queries compare equal as text."""
        data = {k: v for k, v in asdict(self).items() if v not in (None, {}, "")}
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | dict) -> "Query":
        data = json.loads(text) if isinstance(text, str) else dict(text)
        return cls(**data)

    def to_fido(self, email: str | None = None) -> list:
        """The sunpy attrs for Fido.search(*query.to_fido()). Imports sunpy lazily.

        A JSOC series sends the search to sunpy's JSOC client, which needs the
        email address registered with JSOC for the export.
        """
        import astropy.units as u
        from sunpy.net import attrs as a

        attrs = [a.Time(self.start, self.end)]
        if self.instrument:
            attrs.append(a.Instrument(self.instrument))
        if self.wavelength is not None:
            high = self.wavelength_max if self.wavelength_max is not None else self.wavelength
            attrs.append(a.Wavelength(self.wavelength * u.AA, high * u.AA))
        if self.source:
            attrs.append(a.Source(self.source))
        if self.provider:
            attrs.append(a.Provider(self.provider))
        if self.detector:
            attrs.append(a.Detector(self.detector))
        if self.level:
            attrs.append(a.Level(self.level))
        if self.physobs:
            attrs.append(a.Physobs(self.physobs))
        if self.sample_s:
            attrs.append(a.Sample(self.sample_s * u.s))
        if self.jsoc_series:
            attrs.append(a.jsoc.Series(self.jsoc_series))
            if email:
                attrs.append(a.jsoc.Notify(email))
        return attrs
