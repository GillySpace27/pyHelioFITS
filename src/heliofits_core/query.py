"""A structured, re-runnable description of what to search for.

The CONNECT manifest stored queries as printed sunpy objects, which cannot be
run again. A Query is plain data: it round-trips through JSON exactly, and
to_fido() rebuilds the sunpy search from it whenever it is needed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from urllib.parse import parse_qsl, urlencode, urlsplit

URL_SCHEME = "hfcore"
URL_SCHEMES = (URL_SCHEME, "heliad")  # heliad:// is the scheme from before the rename, still read
URL_ACTIONS = ("search", "get")  # open the app's search filled in, or also start the download
_NUMBERS = {"wavelength", "wavelength_max", "sample_s"}


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

    def to_url(self, action: str = "search") -> str:
        """A hfcore:// link to this query, which Studio opens to hand a search to the app:
        hfcore://search?start=2025-09-21T16:00:00&end=...&instrument=AIA&wavelength=193"""
        if action not in URL_ACTIONS:
            raise ValueError(f"action must be one of {URL_ACTIONS}")
        data = json.loads(self.to_json())
        if data.pop("extra", None):
            raise ValueError("a query with extra attributes has no hfcore:// form")
        return f"{URL_SCHEME}://{action}?{urlencode(sorted((k, _plain(v)) for k, v in data.items()))}"

    @classmethod
    def from_url(cls, url: str) -> tuple[str, "Query"]:
        """The action and query in a hfcore:// link. Unknown parameters are refused, not dropped."""
        parts = urlsplit(url)
        if parts.scheme not in URL_SCHEMES:
            raise ValueError(f"not a {URL_SCHEME}:// link: {url!r}")
        action = parts.netloc or parts.path.strip("/")
        if action not in URL_ACTIONS:
            raise ValueError(f"unknown action {action!r} (known: {', '.join(URL_ACTIONS)})")
        known = {f.name for f in fields(cls)} - {"extra"}
        data: dict = {}
        for key, value in parse_qsl(parts.query, keep_blank_values=False, strict_parsing=False):
            if key not in known:
                raise ValueError(f"unknown query parameter {key!r}")
            if key in data:
                raise ValueError(f"{key!r} given twice")
            data[key] = float(value) if key in _NUMBERS else value
        for key in ("start", "end"):
            if key not in data:
                raise ValueError(f"a hfcore:// link needs {key}")
        return action, cls(**data)

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


def _plain(value) -> str:
    """Numbers as written by a person: 193.0 becomes 193."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def dataset_link(dataset_uid: str, start: str | None = None, end: str | None = None) -> str:
    """A hfcore:// link to one dataset over a time range, stable across reindexing:
    hfcore://dataset/<dataset_uid>?start=2025-09-21T16:00:00&end=2025-09-21T17:00:00"""
    params = [(k, v) for k, v in (("start", start), ("end", end)) if v]
    tail = f"?{urlencode(params)}" if params else ""
    return f"{URL_SCHEME}://dataset/{dataset_uid}{tail}"


def parse_dataset_link(url: str) -> dict:
    """{dataset_uid, start, end} from a hfcore://dataset/<uid> link (start and end may be
    None). Unknown parameters are refused, as Query.from_url refuses them."""
    parts = urlsplit(url)
    if parts.scheme not in URL_SCHEMES or parts.netloc != "dataset":
        raise ValueError(f"not a {URL_SCHEME}://dataset/ link: {url!r}")
    uid = parts.path.strip("/")
    if not uid or "/" in uid:
        raise ValueError(f"a {URL_SCHEME}://dataset/ link needs one dataset uid: {url!r}")
    out: dict = {"dataset_uid": uid, "start": None, "end": None}
    for key, value in parse_qsl(parts.query, keep_blank_values=False, strict_parsing=False):
        if key not in ("start", "end"):
            raise ValueError(f"unknown dataset link parameter {key!r}")
        if out[key] is not None:
            raise ValueError(f"{key!r} given twice")
        out[key] = value
    return out
