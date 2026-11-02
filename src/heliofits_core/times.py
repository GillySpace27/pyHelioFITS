"""One way to read and write times, for the whole core.

The index writes every time as ISO 8601 UTC with milliseconds and no zone
letter (2025-09-21T16:00:00.000), so that comparing text compares times.
Everything that reads a time from a person, a query, an archive or the index
goes through parse(); everything that writes one goes through text(). Before
this module, library, coverage, upgrade and the JSOC source each had their
own parser and they did not accept quite the same forms.

Forms parse() reads, all taken as UTC unless they carry an offset:

    2025-09-21T16:00:00.000      the index form
    2025-09-21T16:00:00Z         with a zone letter
    2025-09-21 16:00:00          a space for the T
    2025-09-21T16:00             no seconds
    2025-09-21                   a date alone (midnight)
    2025-09-21T16:00:00+02:00    an offset, moved to UTC
    2025.09.21_16:00:00_TAI      JSOC's T_REC, moved from TAI to UTC
    an astropy Time (its .isot), or a datetime

A value that is none of these is None, never a guess.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

INDEX_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
_EPOCH = datetime(1970, 1, 1)
_JSOC = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})_(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)_(TAI|UTC)$")


def parse(value) -> datetime | None:
    """The time as a naive datetime in UTC, or None when value is not a time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        when = value
    else:
        if not isinstance(value, str):
            value = getattr(value, "isot", None) or str(value)
        text = value.strip()
        if not text:
            return None
        m = _JSOC.match(text)
        if m:
            return _from_jsoc(m)
        try:
            iso = text.removesuffix("Z").replace(" ", "T")
            # Python 3.10 reads only 3 or 6 fractional digits; pad or cut to 6 (newer Pythons read any)
            iso = re.sub(r"(\.\d+)", lambda f: (f.group(1) + "000000")[:7], iso, count=1)
            when = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def _from_jsoc(m: re.Match) -> datetime:
    y, mo, d, h, mi, s, scale = m.groups()
    when = datetime(int(y), int(mo), int(d), int(h), int(mi)) + timedelta(seconds=float(s))
    if scale == "TAI":
        import erfa  # comes with astropy; its leap-second table needs no network
        when -= timedelta(seconds=float(erfa.dat(when.year, when.month, when.day, 0.5)))
    return when


def text(when: datetime) -> str:
    """The index form of a time, to the millisecond."""
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.strftime(INDEX_FORMAT)[:-3]


def index_text(value) -> str | None:
    """The index form of any time parse() reads, or None."""
    when = parse(value)
    return None if when is None else text(when)


def seconds(value) -> float | None:
    """Seconds since 1970 UTC, or None."""
    when = parse(value)
    return None if when is None else (when - _EPOCH).total_seconds()


def from_seconds(value: float) -> str:
    """The index form of a time given as seconds since 1970 UTC."""
    return text(_EPOCH + timedelta(milliseconds=round(value * 1000)))
