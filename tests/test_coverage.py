"""Coverage for the app's Timeline and search pane, and the list of views."""

import io
import json
import sys
import types

import pytest

from heliofits.core import coverage, views
from heliofits.core.rpc import Server
from conftest import aia_header, punch_header, write_fits


def _fill(lib, src):
    """AIA 193 at 16:00, 16:10, 16:20 and 16:59; AIA 171 at 16:30; PUNCH at 17:00 in two versions."""
    for i, t in enumerate(["16:00:00.000", "16:10:00.000", "16:20:00.000", "16:59:59.999"]):
        lib.ingest(write_fits(src / f"a193_{i}.fits", aia_header(f"2025-09-21T{t}")))
    lib.ingest(write_fits(src / "a171.fits", aia_header("2025-09-21T16:30:00.000", wave=171)))
    lib.ingest(write_fits(src / "p0k.fits", punch_header("2025-09-21T17:00:00.000", version="0k")))
    lib.ingest(write_fits(src / "p0l.fits", punch_header("2025-09-21T17:00:00.000", version="0l")))


def _by_label(cov):
    return {row["label"]: row for row in cov["datasets"]}


def test_whole_library_spans_first_to_last(lib, src):
    _fill(lib, src)
    cov = coverage.library(lib, bins=6)
    assert (cov["start"], cov["end"]) == ("2025-09-21T16:00:00.000", "2025-09-21T17:00:00.000")
    assert cov["bin_seconds"] == pytest.approx(600)
    rows = _by_label(cov)
    assert [r["label"] for r in cov["datasets"]] == sorted(rows)  # library.datasets order
    aia193 = next(r for r in cov["datasets"] if r["wavelength"] == 193)
    assert aia193["counts"] == [1, 1, 1, 0, 0, 1] and aia193["frames"] == 4
    assert (aia193["first"], aia193["last"]) == ("2025-09-21T16:00:00.000", "2025-09-21T16:59:59.999")
    punch = next(r for r in cov["datasets"] if r["wavelength"] is None)
    assert punch["counts"] == [0, 0, 0, 0, 0, 1]  # the end is in the last bin; two versions count once
    assert sum(sum(r["counts"]) for r in cov["datasets"]) == 6


def test_a_range_keeps_every_row_and_counts_only_inside(lib, src):
    _fill(lib, src)
    cov = coverage.library(lib, start="2025-09-21T16:05:00", end="2025-09-21T16:25:00", bins=2)
    assert len(cov["datasets"]) == 3  # rows with nothing in range stay, so a zoom does not reshuffle them
    aia193 = next(r for r in cov["datasets"] if r["wavelength"] == 193)
    assert aia193["counts"] == [1, 1] and aia193["frames"] == 2
    empty = [r for r in cov["datasets"] if r["frames"] == 0]
    assert len(empty) == 2 and all(r["counts"] == [0, 0] and r["first"] is None for r in empty)


def test_filters_match_a_search(lib, src):
    _fill(lib, src)
    only = coverage.library(lib, instrument="aia", wavelength=193)
    assert [r["wavelength"] for r in only["datasets"]] == [193]
    assert only["end"] == "2025-09-21T16:59:59.999"  # the range follows the filtered datasets
    near = coverage.library(lib, instrument="AIA", wavelength=193.3)
    assert [r["wavelength"] for r in near["datasets"]] == [193]
    assert coverage.library(lib, instrument="AIA", wavelength=194)["datasets"] == []  # 0.2% is 0.39 Å here
    band = coverage.library(lib, wavelength=150, wavelength_max=180)
    assert [r["wavelength"] for r in band["datasets"]] == [171]
    key = only["datasets"][0]["dataset_key"]
    assert [r["dataset_key"] for r in coverage.library(lib, dataset_keys=[key])["datasets"]] == [key]
    assert coverage.library(lib, instrument="LASCO")["datasets"] == []


def test_empty_single_and_bad_ranges(lib, src):
    empty = coverage.library(lib)
    assert empty["start"] is None and empty["datasets"] == []
    lib.ingest(write_fits(src / "one.fits", aia_header("2025-09-21T16:00:00.000")))
    one = coverage.library(lib, bins=3)
    assert (one["start"], one["end"]) == ("2025-09-21T15:59:30.000", "2025-09-21T16:00:30.000")
    assert one["datasets"][0]["counts"] == [0, 1, 0]
    with pytest.raises(ValueError):
        coverage.library(lib, start="2025-09-21T17:00:00", end="2025-09-21T16:00:00")
    with pytest.raises(ValueError):
        coverage.library(lib, start="yesterday", end="2025-09-21T16:00:00")
    with pytest.raises(ValueError):
        coverage.library(lib, bins=0)


def test_an_end_without_milliseconds_includes_that_second(lib, src):
    _fill(lib, src)
    for end in ["2025-09-21T16:30:00", "2025-09-21 16:30", "2025-09-21T18:30:00+02:00"]:
        files = lib.current_files(start="2025-09-21T16:30:00", end=end)
        assert [f["date_obs"] for f in files] == ["2025-09-21T16:30:00.000"], end


def test_undated_observations_are_counted_not_lost(lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header("2025-09-21T16:00:00.000")))
    lib.conn.execute("UPDATE observations SET date_obs = 'not a time'")
    (row,) = coverage.library(lib)["datasets"]
    assert row["undated"] == 1 and row["frames"] == 0


@pytest.mark.parametrize("value, utc", [
    ("2025-09-21T16:00:04.840", "2025-09-21T16:00:04.840"),     # Fido (astropy isot)
    ("2025-09-21T16:00:04Z", "2025-09-21T16:00:04.000"),        # JSOC T_REC in ISO form
    ("2025.09.21_16:00:37_TAI", "2025-09-21T16:00:00.000"),     # JSOC form: TAI-UTC was 37 s in 2025
    ("2016.12.31_23:59:59_UTC", "2016-12-31T23:59:59.000"),
    ("2025-09-21T18:00:04+02:00", "2025-09-21T16:00:04.000"),
    ("MISSING", None),
    (None, None),
])
def test_archive_times_are_read_as_utc(value, utc):
    assert coverage.utc_text(value) == utc


def test_archive_times_bin_like_the_library(lib, src):
    _fill(lib, src)
    mine = coverage.library(lib, start="2025-09-21T16:00:00", end="2025-09-21T17:00:00", bins=6)
    theirs = coverage.times(["2025-09-21T16:00:00.000", "2025-09-21T16:10:00.000", "2025-09-21T16:20:00.000",
                             "2025-09-21T16:59:59.999", "2025-09-21T17:00:01", "--"],
                            "2025-09-21T16:00:00", "2025-09-21T17:00:00", bins=6)
    aia193 = next(r for r in mine["datasets"] if r["wavelength"] == 193)
    assert theirs["counts"] == aia193["counts"]
    assert (theirs["start"], theirs["end"], theirs["bin_seconds"]) == (mine["start"], mine["end"], mine["bin_seconds"])
    assert (theirs["outside"], theirs["undated"]) == (1, 1)


def _server(lib):
    server = Server(lib, out=io.StringIO())

    def call(method, **params):
        return server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    return server, call


def test_rpc_coverage_and_views(lib, src):
    _fill(lib, src)
    _, call = _server(lib)
    cov = call("library.coverage", bins=6, instrument="AIA", wavelength=193)["result"]
    assert cov["datasets"][0]["counts"] == [1, 1, 1, 0, 0, 1]
    assert call("library.coverage", bogus=1)["error"]["code"] == -32602
    assert call("view.list")["result"] == []
    views.create(lib, "flare", description="M1", start="2025-09-21T16:00:00", end="2025-09-21T16:30:00")
    (made,) = call("view.list")["result"]
    assert (made["name"], made["observations"], made["datasets"], made["materialized"]) == ("flare", 4, 2, False)
    assert (made["first"], made["last"]) == ("2025-09-21T16:00:00.000", "2025-09-21T16:30:00.000")
    assert made["path"].replace("\\", "/").endswith("views/flare")
    views.materialize(lib, "flare")
    assert call("view.list")["result"][0]["materialized"] is True


def test_search_bins_every_record_beside_its_table(lib, monkeypatch):
    fake = types.ModuleType("fake_source")
    fake.search = lambda q, settings=None: {
        "total": 3,
        "providers": [{"client": "VSOClient", "count": 3, "columns": ["Start Time"],
                       "rows": [{"Start Time": "2025-09-21T16:00:00.000"}],  # rows shown are cut short
                       "times": ["2025-09-21T16:00:00.000", "2025-09-21T16:30:00.000", "2025-09-21T16:59:00.000"]},
                      {"client": "Other", "count": 0, "columns": [], "rows": []}]}
    monkeypatch.setitem(sys.modules, "heliofits.core.sources.fake", fake)
    monkeypatch.setitem(__import__("heliofits.core.sources", fromlist=["SOURCES"]).SOURCES, "fake", "heliofits.core.sources.fake")
    server, call = _server(lib)
    assert call("search", query={"start": "2025-09-21T16:00:00", "end": "2025-09-21T17:00:00"},
                source="fake", bins=2) is None
    server._pool.shutdown(wait=True)
    (reply,) = [json.loads(line) for line in server.out.getvalue().splitlines()]
    first, other = reply["result"]["providers"]
    assert "times" not in first and first["coverage"]["counts"] == [1, 2]
    assert "coverage" not in other  # a source that reports no times gets no coverage
