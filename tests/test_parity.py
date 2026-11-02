"""Tests of the decode-parity machinery (src/heliofits_core/parity.py).

Everything here runs on SYNTHETIC files: small arrays with headers shaped like each instrument's
(the keyword sets follow the ones Studio's decoder reads; the values are made up). They prove that
the harness reads sunpy correctly, applies the named corrections, and CATCHES a wrong decode
(a flip, a dropped correction, a shifted WCS). They say nothing about real Studio output; that
is tests/test_parity_real.py, which needs the real fixtures (docs/decode-parity.md).
"""

import numpy as np
import pytest
from astropy.io import fits

pytest.importorskip("sunpy.map", reason="decode parity needs sunpy: pip install 'heliofits-core[parity]'")

from heliofits_core import parity  # noqa: E402

NY, NX = 12, 16


def _rng(seed=7):
    return np.random.default_rng(seed)


def _hpc(extra=None, scale=2.0, crpix=(8.5, 6.5), unit="arcsec"):
    h = {
        "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "CUNIT1": unit, "CUNIT2": unit,
        "CDELT1": scale, "CDELT2": scale, "CRPIX1": crpix[0], "CRPIX2": crpix[1],
        "CRVAL1": 0.0, "CRVAL2": 0.0, "DSUN_OBS": 1.495978707e11, "RSUN_REF": 6.96e8,
        "HGLN_OBS": 0.0, "HGLT_OBS": 1.0,
    }
    h.update(extra or {})
    return h


def _write(path, data, header, compressed=False, bitpix_scaling=None):
    if compressed:
        hdul = fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(data=data)])
        target = hdul[1].header
    else:
        hdul = fits.HDUList([fits.PrimaryHDU(data=data)])
        target = hdul[0].header
    for k, v in header.items():
        target[k] = v
    hdul.writeto(path)
    return path


@pytest.fixture
def aia(tmp_path):
    data = _rng().integers(0, 4000, size=(NY, NX)).astype(np.int16)
    h = _hpc({"TELESCOP": "SDO/AIA", "INSTRUME": "AIA_3", "WAVELNTH": 193, "WAVEUNIT": "angstrom", "DATE-OBS": "2025-09-21T16:00:04.840",
              "BUNIT": "DN", "CROTA2": 0.0}, scale=0.6)
    return _write(tmp_path / "aia.fits", data, h, compressed=True)


@pytest.fixture
def lasco(tmp_path):
    raw = _rng(3).integers(-3000, 3000, size=(NY, NX)).astype(np.int16)  # stored as signed, BZERO makes it unsigned
    h = _hpc({"TELESCOP": "SOHO", "INSTRUME": "LASCO", "DETECTOR": "C2", "DATE-OBS": "2026/04/11", "TIME-OBS": "22:30:23.000",
              "BZERO": 32768, "BSCALE": 1, "OFFSET": 747.3, "EXPTIME": 25.1, "CROTA1": -178.0, "CROTA2": -178.0}, scale=11.9)
    return _write(tmp_path / "lasco.fits", raw, h)


@pytest.fixture
def punch(tmp_path):
    cube = _rng(5).normal(1e-12, 3e-13, size=(3, NY, NX)).astype(np.float32)
    h = _hpc({"OBSRVTRY": "PUNCH", "INSTRUME": "WFI+NFI", "TYPECODE": "PT", "OBSCODE": "M", "FILEVRSN": "0l",
              "DATE-OBS": "2026-04-21T00:00:00.000", "DATE-AVG": "2026-04-21T00:02:30.000", "BUNIT": "MSB",
              "OBSLAYR1": "Polar_B", "OBSLAYR2": "Polar_pB", "OBSLAYR3": "Polar_pBp",
              "CTYPE1": "HPLN-ARC", "CTYPE2": "HPLT-ARC", "CUNIT1": "deg", "CUNIT2": "deg",
              "CDELT1": 0.0225, "CDELT2": 0.0225})
    return _write(tmp_path / "punch.fits", cube, h)


@pytest.fixture
def suvi(tmp_path):
    data = (_rng(9).random((NY, NX)) * 100).astype(np.float32)
    data[2, 3] = np.nan
    h = _hpc({"TELESCOP": "GOES-16", "INSTRUME": "GOES-R Series Solar Ultraviolet Imager", "WAVELNTH": 195.0, "WAVEUNIT": "angstrom",
              "DATE-OBS": "2025-09-21T12:00:00.000", "BUNIT": "DN"}, scale=2.5)
    return _write(tmp_path / "suvi.fits", data, h)


def studio_stand_in(path, ref, **change):
    """A Studio dump made without sunpy: what a correct decode would write, then deliberately changed.

    It reads the file with astropy only and writes the arrays the way the dump format specifies,
    so the tests check the comparison, not the reference against itself.
    """
    d = parity.Dump(
        writer={"name": "test-stand-in"}, source=dict(ref.source), instrument=ref.instrument, plane=ref.plane,
        data=ref.data.copy(), unit=ref.unit, settings=dict(parity.PARITY_SETTINGS),
        corrections=[dict(c) for c in ref.corrections], time=dict(ref.time),
        grid_pixels=ref.grid_pixels.copy(), grid_arcsec=ref.grid_arcsec.copy(),
        background=None if ref.background is None else ref.background.copy())
    for k, v in change.items():
        setattr(d, k, v)
    return d


def test_instrument_detection(aia, lasco, punch, suvi):
    assert [parity.instrument_of(fits.getheader(p, ext=e)) for p, e in
            ((aia, 1), (lasco, 0), (punch, 0), (suvi, 0))] == ["AIA", "LASCO", "PUNCH", "SUVI"]


def test_aia_exact_integers_pass_with_zero_tolerance(aia):
    ref = parity.reference_dump(aia)
    assert ref.instrument == "AIA" and ref.data.shape == (NY, NX)
    rep = parity.compare_dumps(studio_stand_in(aia, ref), ref)
    assert rep.ok, rep.text()
    data = next(c for c in rep.checks if c.name == "data")
    assert data.tolerance == {"rtol": 0.0, "atol": 0.0}


def test_vertical_flip_is_caught_and_named(aia):
    ref = parity.reference_dump(aia)
    rep = parity.compare_dumps(studio_stand_in(aia, ref, data=ref.data[::-1]), ref)
    assert not rep.ok
    data = next(c for c in rep.checks if c.name == "data")
    assert "flipped vertically" in data.detail


def test_one_count_change_is_caught_on_integer_data(aia):
    ref = parity.reference_dump(aia)
    bad = ref.data.copy()
    bad[4, 4] += 1
    assert not parity.compare_dumps(studio_stand_in(aia, ref, data=bad), ref).ok


def test_lasco_as_served_is_raw_dn_with_bzero(lasco):
    ref = parity.reference_dump(lasco)
    raw = fits.getdata(lasco, do_not_scale_image_data=True).astype(np.float64)
    np.testing.assert_array_equal(ref.data, (raw + 32768).astype(np.float32))
    assert not ref.corrections or all(c["name"] != "lasco_per_second" for c in ref.corrections)
    assert parity.compare_dumps(studio_stand_in(lasco, ref), ref).ok


def test_lasco_time_combines_date_and_time_obs(lasco):
    ref = parity.reference_dump(lasco)
    assert ref.time["iso"].startswith("2026-04-11T22:30:23")


def test_lasco_per_second_correction_matches_studios_formula(lasco):
    plain = parity.reference_dump(lasco)
    bg = (_rng(11).random((NY, NX)) * 5).astype(np.float32)
    like = studio_stand_in(lasco, plain, background=bg,
                           corrections=[{"name": "lasco_per_second", "applied": True, "params": {}}])
    raw = fits.getdata(lasco, do_not_scale_image_data=True).astype(np.float64)
    expect = ((32768 + raw - 747.3) / 25.1 - bg).astype(np.float32)  # FITSImage.lascoPerSecond, written out
    like.data = expect
    like.unit = "DN/s"
    ref = parity.reference_dump(lasco, like=like)
    np.testing.assert_allclose(ref.data, expect, rtol=1e-6)
    rep = parity.compare_dumps(like, ref)
    assert rep.ok, rep.text()
    assert "float32 rounding" in next(c for c in rep.checks if c.name == "data").basis


def test_lasco_per_second_without_background_array_is_refused(lasco):
    plain = parity.reference_dump(lasco)
    like = studio_stand_in(lasco, plain, corrections=[{"name": "lasco_per_second", "applied": True, "params": {}}])
    with pytest.raises(ValueError, match="no background array"):
        parity.reference_dump(lasco, like=like)


def test_dropping_the_exposure_division_is_caught(lasco):
    plain = parity.reference_dump(lasco)
    bg = np.zeros((NY, NX), np.float32)
    like = studio_stand_in(lasco, plain, background=bg,
                           corrections=[{"name": "lasco_per_second", "applied": True, "params": {}}])
    raw = fits.getdata(lasco, do_not_scale_image_data=True).astype(np.float64)
    like.data = (32768 + raw - 747.3).astype(np.float32)  # forgot / EXPTIME
    like.unit = "DN/s"
    rep = parity.compare_dumps(like, parity.reference_dump(lasco, like=like))
    assert not rep.ok
    assert next(c for c in rep.checks if c.name == "data").ok is False


def test_punch_plane_selection_and_date_avg(punch):
    for plane in range(3):
        ref = parity.reference_dump(punch, plane=plane)
        np.testing.assert_array_equal(ref.data, fits.getdata(punch)[plane])
        assert ref.plane == plane
    ref = parity.reference_dump(punch, plane=1)
    assert ref.time["keyword"] == "DATE-AVG" and ref.time["iso"].startswith("2026-04-21T00:02:30")
    assert any(c["name"] == "time_prefers_date_avg" for c in ref.corrections)
    studio = studio_stand_in(punch, ref)
    assert parity.compare_dumps(studio, ref).ok


def test_punch_wrong_plane_is_caught(punch):
    ref = parity.reference_dump(punch, plane=1)
    wrong = fits.getdata(punch)[2]
    assert not parity.compare_dumps(studio_stand_in(punch, ref, data=wrong), ref).ok


def test_out_of_range_plane_reads_plane_zero_and_says_so(punch):
    ref = parity.reference_dump(punch, plane=9)
    assert ref.plane == 0
    np.testing.assert_array_equal(ref.data, fits.getdata(punch)[0])
    assert any(c["name"] == "plane_out_of_range_is_zero" for c in ref.corrections)


def test_suvi_nan_positions_must_agree(suvi):
    ref = parity.reference_dump(suvi)
    assert np.isnan(ref.data[2, 3])
    assert parity.compare_dumps(studio_stand_in(suvi, ref), ref).ok
    filled = ref.data.copy()
    filled[2, 3] = 0.0
    rep = parity.compare_dumps(studio_stand_in(suvi, ref, data=filled), ref)
    assert not rep.ok and not next(c for c in rep.checks if c.name == "bad_pixels").ok


def test_float_data_gets_rounding_tolerance_not_exact(suvi):
    ref = parity.reference_dump(suvi)
    nudged = ref.data * np.float32(1 + 2e-7)  # a couple of float32 ulps: allowed
    assert parity.compare_dumps(studio_stand_in(suvi, ref, data=nudged), ref).ok
    off = ref.data * np.float32(1 + 1e-4)     # a 0.01% error: not allowed
    assert not parity.compare_dumps(studio_stand_in(suvi, ref, data=off), ref).ok


def test_wcs_shift_of_a_tenth_of_a_pixel_is_caught(aia):
    ref = parity.reference_dump(aia)
    moved = ref.grid_arcsec + np.array([0.06, 0.0])  # 0.1 AIA pixel, 0.6 arcsec/pixel
    rep = parity.compare_dumps(studio_stand_in(aia, ref, grid_arcsec=moved), ref)
    assert not rep.ok and not next(c for c in rep.checks if c.name == "wcs_grid_arcsec").ok


def test_wcs_grid_matches_a_hand_computed_tan_plane(aia):
    """Independent of sunpy: at small angles TAN is linear, arcsec = CDELT * (pixel - CRPIX + 1)."""
    ref = parity.reference_dump(aia)
    x, y = ref.grid_pixels[:, 0], ref.grid_pixels[:, 1]
    expect = np.column_stack([0.6 * (x + 1 - 8.5), 0.6 * (y + 1 - 6.5)])
    np.testing.assert_allclose(ref.grid_arcsec, expect, atol=1e-3)  # TAN vs linear at <10 arcsec: far below 1e-3


def test_time_off_by_a_second_is_caught(aia):
    ref = parity.reference_dump(aia)
    late = dict(ref.time, iso="2025-09-21T16:00:05.840")
    assert not parity.compare_dumps(studio_stand_in(aia, ref, time=late), ref).ok


def test_display_options_on_void_the_claim(aia):
    ref = parity.reference_dump(aia)
    s = studio_stand_in(aia, ref)
    s.settings["normalize_aia"] = True
    rep = parity.compare_dumps(s, ref)
    assert not next(c for c in rep.checks if c.name == "settings_in_scope").ok


def test_unnamed_correction_is_a_finding(aia):
    ref = parity.reference_dump(aia)
    s = studio_stand_in(aia, ref)
    s.corrections.append({"name": "mystery_gain", "applied": True, "params": {}})
    assert not next(c for c in parity.compare_dumps(s, ref).checks if c.name == "only_named_corrections").ok


def test_different_file_is_refused(aia, suvi):
    ref = parity.reference_dump(aia)
    other = parity.reference_dump(suvi)
    s = studio_stand_in(aia, ref, source=other.source)
    assert not next(c for c in parity.compare_dumps(s, ref).checks if c.name == "same_file").ok


@pytest.mark.parametrize("inline", [False, True])
def test_dump_round_trip(tmp_path, suvi, inline):
    ref = parity.reference_dump(suvi)
    out = parity.write_dump(ref, tmp_path / "x.hfdump.json", inline=inline)
    back = parity.read_dump(out)
    np.testing.assert_array_equal(back.data, ref.data)  # assert_array_equal treats NaN in the same place as equal
    assert back.time == ref.time and back.source == ref.source
    np.testing.assert_array_equal(back.grid_pixels, ref.grid_pixels)


def test_reading_a_foreign_orientation_is_refused(tmp_path, suvi):
    import json
    out = parity.write_dump(parity.reference_dump(suvi), tmp_path / "y.hfdump.json")
    doc = json.loads(out.read_text())
    doc["orientation"] = "image"
    out.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="orientation"):
        parity.read_dump(out)


def test_command_line_reference_then_compare(tmp_path, aia):
    out = tmp_path / "studio.hfdump.json"
    parity.write_dump(studio_stand_in(aia, parity.reference_dump(aia)), out)
    assert parity.main(["compare", str(out), "--source", str(aia)]) == 0


def test_infinite_reference_pixel_does_not_make_the_data_tolerance_infinite():
    # found on a real ASPIICS L3 frame (10 +inf pixels): atol = 8 eps * max|ref| was inf, so the check could not fail
    ref = np.array([[1.0, 2.0], [np.inf, 4.0]], dtype=np.float32)
    rtol, atol, _ = parity.data_tolerance(ref, integer_exact=False)
    assert np.isfinite(atol) and atol < 1e-5
