"""times.py: every form the core reads, and that every caller agrees with it."""

from datetime import datetime, timedelta, timezone

import pytest

from heliofits.core import coverage, times, upgrade
from heliofits.core.library import time_bound
from heliofits.core.sources import jsoc

INDEX = "2025-09-21T16:00:00.000"


@pytest.mark.parametrize("value", [
    INDEX,
    "2025-09-21T16:00:00",
    "2025-09-21T16:00:00Z",
    "2025-09-21 16:00:00",
    "2025-09-21T16:00",
    "2025-09-21T16:00:00.000000",
    "2025-09-21T18:00:00+02:00",
    "  2025-09-21T16:00:00Z  ",
    datetime(2025, 9, 21, 16),
    datetime(2025, 9, 21, 16, tzinfo=timezone.utc),
])
def test_every_form_reads_as_the_same_index_text(value):
    assert times.index_text(value) == INDEX
    assert times.text(times.parse(value)) == INDEX


def test_a_date_alone_is_midnight():
    assert times.index_text("2025-09-21") == "2025-09-21T00:00:00.000"


def test_milliseconds_are_kept_and_rounded_to_three_places():
    assert times.index_text("2025-09-21T16:00:00.1234") == "2025-09-21T16:00:00.123"


def test_jsoc_times_move_from_tai_to_utc():
    # 37 leap seconds in 2025 (ERFA's table), so TAI is 37 s ahead of UTC.
    assert times.index_text("2025.09.21_16:00:37_TAI") == INDEX
    assert times.index_text("2025.09.21_16:00:00_UTC") == INDEX


def test_an_astropy_time_reads_through_isot():
    class Isot:
        isot = "2025-09-21T16:00:00.000"
    assert times.index_text(Isot()) == INDEX


@pytest.mark.parametrize("value", [None, "", "   ", "yesterday", "2025-13-01", 12345, "16:00"])
def test_what_is_not_a_time_is_none(value):
    assert times.parse(value) is None
    assert times.index_text(value) is None
    assert times.seconds(value) is None


def test_seconds_round_trip():
    s = times.seconds(INDEX)
    assert s == (datetime(2025, 9, 21, 16) - datetime(1970, 1, 1)).total_seconds()
    assert times.from_seconds(s) == INDEX
    assert times.from_seconds(s + 0.0004) == INDEX
    assert times.from_seconds(s + 0.0006) == "2025-09-21T16:00:00.001"


def test_text_moves_an_aware_time_to_utc():
    aware = datetime(2025, 9, 21, 18, tzinfo=timezone(timedelta(hours=2)))
    assert times.text(aware) == INDEX


def test_the_old_names_agree_with_times():
    for value in (INDEX, "2025-09-21 16:00", "2025.09.21_16:00:37_TAI", "not a time"):
        assert coverage.utc_text(value) == times.index_text(value)
        assert upgrade.seconds(value) == times.seconds(value)
    assert upgrade._iso(times.seconds(INDEX)) == INDEX


def test_time_bound_passes_text_that_is_not_a_time_through():
    assert time_bound("2025-09-21 16:00") == INDEX
    assert time_bound("anything") == "anything"
    assert time_bound(None) is None
    assert time_bound("") == ""


def test_jsoc_recordset_reads_offsets_as_utc():
    assert jsoc._iso("2025-09-21T18:00:00+02:00") == datetime(2025, 9, 21, 16)
    with pytest.raises(ValueError):
        jsoc._iso("never")
