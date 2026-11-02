"""layer.upgrade: from a layer of pictures (Helioviewer's JPEG 2000 frames, each carrying the
FITS header it was made from) to the FITS files the library keeps or would fetch. Archives
are faked; nothing here reaches one."""

import io
import json

import pytest

from heliofits_core import cli, jobs, layers
from heliofits_core.library import Library
from heliofits_core.rpc import Server
from conftest import aia_header, write_fits
from test_upgrade import Archive

AIA171 = "sdo/aia/-/-/1/171"


def picture(date, wave=171, **extra):
    """A picture's header: what Helioviewer keeps of the FITS file it was made from. A
    level 1.5 picture of a level 1 file, so a level match would be wrong."""
    h = aia_header(date=date, wave=wave, LVL_NUM=1.5)
    h.update(extra)
    return h


@pytest.fixture
def server(lib):
    return Server(lib, out=io.StringIO())


def test_a_picture_finds_its_fits_whatever_the_level(server, lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header(date="2025-09-21T16:00:04.840", wave=171)))
    out = server.methods["layer.upgrade"](headers=[picture("2025-09-21T16:00:05.000"),
                                                   picture("2025-09-21T16:30:05.000"),
                                                   picture("2025-09-21T17:00:05.000")])
    assert out["dry_run"] is True and out["complete"] is False
    assert out["counts"] == {"in_library": 1, "rejected": 0, "missing": 2, "unreadable": 0}
    first = out["frames"][0]
    assert first["status"] == "in_library" and first["dataset_key"] == AIA171
    assert first["fits_date_obs"] == "2025-09-21T16:00:04.840"
    assert first["path"].replace("\\", "/").endswith(first["relpath"])
    (g,) = out["groups"]
    assert g["datasets"] == [AIA171] and g["spacing_s"] == 1800.0 and g["tolerance_s"] == 60.0
    f = g["fetch"]
    assert f["options"] == {"dataset_key": AIA171, "missing_only": True, "tolerance_s": 60.0}
    assert f["inferred"] is True and f["query"]["sample_s"] == 1800.0  # the pictures' own cadence
    assert (f["query"]["start"], f["query"]["end"]) == ("2025-09-21T16:29:05.000", "2025-09-21T17:01:05.000")
    assert jobs.list_jobs(lib) == []


def test_another_passband_is_not_the_same_frame(server, lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header(date="2025-09-21T16:00:04.840", wave=193)))
    out = server.methods["layer.upgrade"](headers=[picture("2025-09-21T16:00:05.000")])
    (g,) = out["groups"]
    assert out["frames"][0]["status"] == "missing" and g["datasets"] == []


def test_without_the_dataset_the_query_comes_from_the_headers(server, lib):
    out = server.methods["layer.upgrade"](headers=[picture("2025-09-21T16:00:05.000")])
    f = out["groups"][0]["fetch"]
    q = f["query"]
    assert f["inferred"] is True and f["source"] == "fido" and f["options"] == {"missing_only": False}
    assert (q["instrument"], q["wavelength"], q["detector"], q["level"]) == ("AIA", 171.0, None, None)
    assert q["sample_s"] is None  # one frame: just that frame, within the tolerance
    assert (q["start"], q["end"]) == ("2025-09-21T15:59:05.000", "2025-09-21T16:01:05.000")


def test_a_rejected_frame_is_not_fetched_again(server, lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header(date="2025-09-21T16:00:04.840", wave=171)))
    (row,) = lib.current_files()
    lib.reject([row["observation_id"]], "smeared")
    out = server.methods["layer.upgrade"](headers=[picture("2025-09-21T16:00:05.000")])
    assert out["frames"][0]["status"] == "rejected" and "smeared" in out["frames"][0]["reason"]
    assert out["groups"][0]["fetch"] is None and out["complete"] is True


def test_a_header_without_instrument_or_time_is_unreadable(server):
    out = server.methods["layer.upgrade"](headers=[{"DATE-OBS": "2025-09-21T16:00:00"},
                                                   {"INSTRUME": "AIA_3", "WAVELNTH": 171}])
    assert [f["status"] for f in out["frames"]] == ["unreadable", "unreadable"]
    assert "instrument" in out["frames"][0]["reason"] and "time" in out["frames"][1]["reason"]
    assert out["groups"] == [] and out["complete"] is False


def test_hmi_products_are_told_apart_by_content(server):
    hmi = {"TELESCOP": "SDO/HMI", "INSTRUME": "HMI_FRONT2", "WAVELNTH": 6173.0,
           "DATE-OBS": "2025-09-21T16:00:00.000"}
    out = server.methods["layer.upgrade"](headers=[{**hmi, "CONTENT": "MAGNETOGRAM"},
                                                   {**hmi, "CONTENT": "CONTINUUM INTENSITY"}])
    assert sorted(g["physobs"] for g in out["groups"]) == ["LOS_magnetic_field", "intensity"]
    assert sorted(g["fetch"]["query"]["physobs"] for g in out["groups"]) == ["LOS_magnetic_field", "intensity"]
    assert {g["fetch"]["query"]["instrument"] for g in out["groups"]} == {"HMI"}


def test_upgrading_fetches_the_missing_frames_and_then_finds_them(server, lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header(date="2025-09-21T16:00:04.840", wave=171)))
    pics = ["2025-09-21T16:00:05.000", "2025-09-21T16:30:05.000", "2025-09-21T17:00:05.000"]
    archive = Archive(["2025-09-21T16:00:04.840", "2025-09-21T16:30:04.840", "2025-09-21T17:00:04.840"],
                      header=lambda t: aia_header(date=t, wave=171))
    server.jobs._modules["fido"] = archive
    out = server.methods["layer.upgrade"](headers=[picture(t) for t in pics], dry_run=False)
    job = out["groups"][0]["job"]
    assert job["status"] == "queued" and job["options"]["missing_only"] is True
    (done,) = server.jobs.run_pending()
    assert done["status"] == "done" and done["result"]["ingest"] == {"added": 2}
    again = server.methods["layer.upgrade"](headers=[picture(t) for t in pics])
    assert again["complete"] is True and again["groups"][0]["fetch"] is None
    assert [f["fits_date_obs"] for f in again["frames"]] == [
        "2025-09-21T16:00:04.840", "2025-09-21T16:30:04.840", "2025-09-21T17:00:04.840"]


def test_headers_are_checked(server):
    with pytest.raises(ValueError):
        server.methods["layer.upgrade"](headers=[])
    with pytest.raises(ValueError):
        server.methods["layer.upgrade"](headers=["not a header"])
    with pytest.raises(ValueError):
        layers.plan(server.lib, [{}] * (layers.MAX_FRAMES + 1))


def test_cli_layer_upgrade_is_a_dry_run_without_yes(lib, tmp_path, capsys):
    path = tmp_path / "headers.json"
    path.write_text(json.dumps([picture("2025-09-21T16:00:05.000")]), encoding="utf-8")
    root = lib.root
    lib.close()  # the command opens the library itself
    assert cli.main(["--library", str(root), "--json", "layer-upgrade", str(path)]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["dry_run"] is True and out["counts"]["missing"] == 1
    assert "Nothing queued" in captured.err
    with Library.open(root, readonly=True) as again:
        assert jobs.list_jobs(again) == []


def test_a_lasco_picture_is_dated_from_its_slashed_date_and_time_obs(server, lib, src):
    # The shape of a Helioviewer LASCO C3 picture's header: date with slashes, time apart, no wavelength.
    pic = {"TELESCOP": "SOHO", "INSTRUME": "LASCO", "DETECTOR": "C3", "FILTER": "Clear", "POLAR": "Clear",
           "DATE_OBS": "2026/10/24", "TIME_OBS": "16:18:10.000"}
    lib.ingest(write_fits(src / "c3.fits", {"TELESCOP": "SOHO", "INSTRUME": "LASCO", "DETECTOR": "C3",
                                            "DATE-OBS": "2026-10-24T16:18:10.000"}))
    out = server.methods["layer.upgrade"](headers=[pic, {**pic, "TIME_OBS": "17:18:10.000"}])
    assert [f["date_obs"] for f in out["frames"]] == ["2026-10-24T16:18:10.000", "2026-10-24T17:18:10.000"]
    assert [f["status"] for f in out["frames"]] == ["in_library", "missing"]
    q = out["groups"][0]["fetch"]["query"]
    assert (q["instrument"], q["detector"], q["wavelength"]) == ("LASCO", "C3", None)


def test_a_lasco_file_as_nrl_writes_it_is_dated_and_found(server, lib, src):
    # NRL's LASCO FITS: date with slashes in DATE-OBS, time in TIME-OBS. Undated, the dataset had
    # "no dated observations" and Use FITS failed for every LASCO layer.
    lib.ingest(write_fits(src / "c3.fts", {"TELESCOP": "SOHO", "INSTRUME": "LASCO", "DETECTOR": "C3",
                                           "DATE-OBS": "2026/10/24", "TIME-OBS": "16:18:10.000"}))
    (row,) = lib.current_files()
    assert row["date_obs"] == "2026-10-24T16:18:10.000"
    pic = {"TELESCOP": "SOHO", "INSTRUME": "LASCO", "DETECTOR": "C3", "DATE_OBS": "2026/10/24",
           "TIME_OBS": "16:18:10.000"}
    out = server.methods["layer.upgrade"](headers=[pic, {**pic, "TIME_OBS": "17:18:10.000"}])
    assert [f["status"] for f in out["frames"]] == ["in_library", "missing"]
    f = out["groups"][0]["fetch"]
    assert f["options"]["dataset_key"] == row["dataset_key"]
    assert f["query"]["start"].startswith("2026-10-24T17:")


def test_an_hmi_picture_finds_a_file_from_the_other_camera(server, lib, src):
    lib.ingest(write_fits(src / "hmi.fits", {"TELESCOP": "SDO/HMI", "INSTRUME": "HMI_SIDE1", "WAVELNTH": 6173.0,
                                             "DATE-OBS": "2026-10-24T14:54:27.800"}))
    pic = {"TELESCOP": "SDO", "INSTRUME": "HMI_FRONT2", "WAVELNTH": "6173.00", "CONTENT": "MAGNETOGRAM",
           "DATE-OBS": "2026-10-24T14:54:27.800"}
    out = server.methods["layer.upgrade"](headers=[pic])
    assert out["frames"][0]["status"] == "in_library"
