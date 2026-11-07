"""The per-frame bodies tag: which bodies a frame's own WCS puts in its field."""

import json

import pytest
from conftest import punch_header, write_fits

from pyheliofits.core import bodies, db
from pyheliofits.core.cli import main

pytest.importorskip("sunpy.coordinates", reason="the bodies tag needs sunpy")

from astropy.coordinates import get_body
from astropy.time import Time
from sunpy.coordinates import get_body_heliographic_stonyhurst

WHEN = "2025-09-22T05:46:29"  # new Moon was 2025-09-21: the Moon about 5 degrees from the Sun


def _frame(path, centre_deg, date=WHEN, observer=True):
    """An 8x8 frame of 2-degree pixels (16 degrees across) centred on (centre_deg, 0), seen from
    Earth's centre. Earth's own place is written, as an L3 mosaic's virtual observer would be."""
    extra = {"NAXIS1": 8, "NAXIS2": 8, "CDELT1": 2.0, "CDELT2": 2.0, "CRPIX1": 4.5, "CRPIX2": 4.5,
             "CRVAL1": centre_deg, "CRVAL2": 0.0, "RSUN_REF": 6.957e8}
    if observer:
        earth = get_body_heliographic_stonyhurst("earth", date)
        extra.update(HGLN_OBS=earth.lon.deg, HGLT_OBS=earth.lat.deg, DSUN_OBS=earth.radius.m)
    return write_fits(path, punch_header(date=date, **extra))


def test_tag_matches_an_independent_elongation(tmp_path):
    from pyheliofits.core import fitsmeta
    header, _, _ = fitsmeta.read_headers(_frame(tmp_path / "a.fits", 0.0))
    placed = {p.body: p for p in bodies.tag(header)}
    assert "earth" not in placed  # the observer is at Earth
    # The oracle: astropy alone, geocentric GCRS, no sunpy frames and no WCS.
    t = Time(WHEN)
    for body in ("moon", "mercury", "venus", "mars"):
        want = get_body(body, t).separation(get_body("sun", t)).deg
        assert placed[body].elongation_deg == pytest.approx(want, abs=0.01), body
    assert bodies.names(list(placed.values())) == ["moon", "mercury"]  # 4.9 and 7.3 deg; Venus at 26 is out


def test_the_field_edge_is_the_pixel_grid(tmp_path):
    from pyheliofits.core import fitsmeta
    def moon(centre, name):
        header, _, _ = fitsmeta.read_headers(_frame(tmp_path / name, centre))
        return next(p for p in bodies.tag(header) if p.body == "moon")
    tx = (moon(0.0, "c.fits").x - 3.5) * 2.0  # the Moon's longitude, from the Sun-centred frame
    inside, outside = moon(tx - 7.0, "in.fits"), moon(tx - 9.0, "out.fits")  # the half-width is 8 degrees
    assert inside.in_field and inside.x == pytest.approx(7.0, abs=0.05)
    assert not outside.in_field and outside.x == pytest.approx(8.0, abs=0.05)


def test_library_tags_filters_and_forgets(lib, src):
    sun = lib.ingest(_frame(src / "sun.fits", 0.0)).file_id
    away = lib.ingest(_frame(src / "away.fits", -60.0, date="2025-09-22T06:46:29")).file_id
    bare = lib.ingest(_frame(src / "bare.fits", 0.0, date="2025-09-22T07:46:29", observer=False)).file_id

    before = {r["file_id"]: r for r in lib.bodies()}
    assert not any(r["tagged"] for r in before.values())
    assert lib.bodies(without=["moon"]) == []  # untagged is unknown, not Moon-free

    result = lib.tag_bodies()
    assert (result["tagged"], result["failed"]) == (2, 0)
    assert result["no_wcs"] == 1  # no observer in the header: nothing to say
    rows = {r["file_id"]: r for r in lib.bodies()}
    assert rows[sun]["bodies"] == ["moon", "mercury"]
    assert rows[away]["bodies"] == []
    assert rows[bare]["tagged"] and rows[bare]["bodies"] is None
    assert [r["file_id"] for r in lib.bodies(without=["Moon"])] == [away]
    assert [r["file_id"] for r in lib.bodies(including=["moon"])] == [sun]

    detail = json.loads(lib.reader.execute("SELECT detail_json FROM file_bodies WHERE file_id = ?",
                                           (sun,)).fetchone()[0])
    assert {d["body"] for d in detail} >= {"moon", "venus", "neptune"}

    assert lib.tag_bodies()["skipped"] == 3
    assert lib.tag_bodies(redo=True)["tagged"] == 2

    lib.delete(file_ids=[sun])  # the tag goes with its file (a dangling row would break the foreign key)
    assert lib.reader.execute("SELECT COUNT(*) FROM file_bodies WHERE file_id = ?", (sun,)).fetchone()[0] == 0


def test_cli_lists_moon_free_frames(lib, src, capsys):
    lib.ingest(_frame(src / "sun.fits", 0.0))
    lib.ingest(_frame(src / "away.fits", -60.0, date="2025-09-22T06:46:29"))
    root = str(lib.root)
    lib.close()
    assert main(["--library", root, "tag-bodies"]) == 0
    capsys.readouterr()
    assert main(["--library", root, "bodies", "--without", "moon"]) == 0
    out = capsys.readouterr().out
    assert "06:46:29" in out and "05:46:29" not in out and "none" in out


def test_schema_7_adds_the_table(lib):
    assert db.schema_version(lib.conn) >= 7 and db.has_table(lib.conn, "file_bodies")
