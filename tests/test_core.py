import json
import os
import stat

import pytest

from heliad import checks, db, fitsmeta, views
from heliad.library import Library, LibraryLocked
from heliad.query import Query
from heliad.rpc import Server
from conftest import aia_header, punch_header, write_fits


def test_ingest_aia_lands_once_read_only(lib, src):
    path = write_fits(src / "aia.lev1.193A_2025_09_21T16_00_04.84Z.image_lev1.fits", aia_header())
    r = lib.ingest(path)
    assert r.status == "added"
    stored = lib.abspath(r.relpath)
    assert r.relpath.startswith("store/sdo/aia/1/193/2025/09/21/")
    assert not os.stat(stored).st_mode & stat.S_IWUSR
    assert path.exists()  # copied, the user's file is untouched
    assert [f for f in r.findings if f.severity != "info"] == []
    again = lib.ingest(path)
    assert again.status == "duplicate" and again.file_id == r.file_id
    assert lib.stats()["files"] == 1
    (d,) = lib.datasets()
    assert d["label"] == "SDO AIA 193 Å L1"


def test_versions_are_one_observation_newest_current(lib, src):
    k = write_fits(src / "PUNCH_L3_CTM_20250921170000_v0k.fits", punch_header(version="0k"), compressed=True)
    l = write_fits(src / "PUNCH_L3_CTM_20250921170000_v0l.fits", punch_header(version="0l"), compressed=True)
    j = write_fits(src / "PUNCH_L3_CTM_20250921170000_v0j.fits", punch_header(version="0j"), compressed=True)
    assert lib.ingest(k).status == "added"
    assert lib.ingest(l).status == "new_version"
    assert lib.ingest(j).status == "older_version"
    stats = lib.stats()
    assert stats["observations"] == 1 and stats["files"] == 3
    (cur,) = lib.current_files()
    assert cur["version"] == "0l" and cur["product"] == "CTM" and cur["level"] == "3"


def test_version_from_filename_when_no_filevrsn(src):
    h = punch_header()
    del h["FILEVRSN"]
    meta = fitsmeta.read(write_fits(src / "PUNCH_L3_PTM_20250921170000_v0k.fits", h))
    assert meta.version == "0k"


def test_version_ordering():
    key = fitsmeta.version_sort_key
    assert key(None) < key("0k") < key("0l") < key("1a")
    assert key("1.2") < key("1.10")


def test_header_checks_find_known_problems(src):
    bad = aia_header(CROTA2=0.5, CUNIT1="degrees", RSUN_OBS=900.0, TIMESYS="TAI")
    findings = checks.run(fitsmeta.read(write_fits(src / "bad.fits", bad)))
    codes = {f.code for f in findings}
    assert {"crota_pc_disagree", "cunit_nonstandard", "rsun_distance_disagree", "timesys_not_utc"} <= codes


def test_missing_date_is_still_kept(lib, src):
    h = aia_header()
    del h["DATE-OBS"]
    r = lib.ingest(write_fits(src / "nodate.fits", h))
    assert r.status == "added"
    assert any(f.code == "no_date_obs" for f in r.findings)
    assert "/undated/" in r.relpath


def test_view_projection_and_manifest(lib, src):
    for minute in range(3):
        lib.ingest(write_fits(src / f"aia_{minute}.fits", aia_header(date=f"2025-09-21T16:0{minute}:00")))
    lib.ingest(write_fits(src / "punch.fits", punch_header(), compressed=True))
    aia_key = next(d["dataset_key"] for d in lib.datasets() if "AIA" in d["label"])
    made = views.create(lib, "2025-09-21 CONNECT", dataset_keys=[aia_key], start="2025-09-21T16:01:00")
    assert made["observations"] == 2
    out = views.materialize(lib, "2025-09-21 CONNECT")
    assert out["files"] == 2
    folder = lib.cfg.views_dir / "2025-09-21_CONNECT"
    manifest = (folder / "manifest.csv").read_text().splitlines()
    assert len(manifest) == 3 and manifest[0].startswith("dataset,date_obs")
    links = [p for p in folder.rglob("*.fits")]
    assert len(links) == 2 and all(p.resolve().is_file() for p in links)
    if out["link_modes"].get("symlink"):
        assert all(p.is_symlink() and not os.path.isabs(os.readlink(p)) for p in links)
    # rebuilding replaces the folder; a copy-only fallback also works
    out2 = views.materialize(lib, "2025-09-21 CONNECT", modes=("copy",))
    assert out2["link_modes"] == {"copy": 2}


def test_view_never_overwrites_a_folder_heliad_did_not_make(lib):
    (lib.cfg.views_dir / "mine").mkdir()
    views.create(lib, "mine")
    with pytest.raises(FileExistsError):
        views.materialize(lib, "mine")


def test_one_writer_per_library(lib):
    with pytest.raises(LibraryLocked):
        Library.open(lib.root)


def test_reader_sees_writes_while_writer_open(lib, src):
    reader = db.connect_reader(lib.cfg.db_path)
    assert reader.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    lib.ingest(write_fits(src / "a.fits", aia_header()))
    assert reader.execute("SELECT COUNT(*) FROM current_files").fetchone()[0] == 1
    assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with pytest.raises(Exception):
        reader.execute("DELETE FROM files")


def test_verify_reports_altered_and_missing(lib, src):
    a = lib.ingest(write_fits(src / "a.fits", aia_header()))
    b = lib.ingest(write_fits(src / "b.fits", aia_header(date="2025-09-21T16:05:00")))
    pa, pb = lib.abspath(a.relpath), lib.abspath(b.relpath)
    os.chmod(pa, 0o644)
    with open(pa, "ab") as fh:
        fh.write(b"x")
    os.chmod(pb, 0o644)
    pb.unlink()
    result = lib.verify()
    assert result["altered"] == [a.relpath] and result["missing"] == [b.relpath]


def test_config_file_names_the_library(lib):
    from heliad import config
    data = json.loads(config.config_path().read_text())
    assert data["library_root"] == str(lib.root) and data["schema_version"] == db.SCHEMA_VERSION


def test_query_round_trip_is_canonical():
    q = Query(start="2025-09-21T16:00:00", end="2025-09-21T21:15:00", instrument="AIA",
              wavelength=193.0, provider="JSOC", sample_s=60.0)
    text = q.to_json()
    assert Query.from_json(text) == q
    assert text == Query.from_json(json.loads(text)).to_json()
    assert "null" not in text


def test_rpc(lib, src):
    import io
    out = io.StringIO()
    server = Server(lib, out=out)
    path = write_fits(src / "a.fits", aia_header())
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "heliad.hello"},
        {"jsonrpc": "2.0", "id": 2, "method": "ingest.path", "params": {"path": str(path)}},
        {"jsonrpc": "2.0", "id": 3, "method": "library.stats"},
        {"jsonrpc": "2.0", "id": 4, "method": "nope"},
        {"jsonrpc": "2.0", "id": 5, "method": "library.checks", "params": {"wrong": 1}},
    ]
    server.serve(io.StringIO("\n".join(json.dumps(r) for r in requests) + "\nnot json\n"))
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    by_id = {r.get("id"): r for r in replies}
    assert by_id[1]["result"]["schema_version"] == db.SCHEMA_VERSION
    assert by_id[2]["result"]["status"] == "added"
    assert by_id[3]["result"]["files"] == 1
    assert by_id[4]["error"]["code"] == -32601
    assert by_id[5]["error"]["code"] == -32602
    assert by_id[None]["error"]["code"] == -32700


def test_unparsable_card_is_repaired_and_reported(lib, src):
    # JSOC AIA level 1 files carry OSCNMEAN = nan, which astropy refuses to parse.
    path = write_fits(src / "aia.lev1.193A_2025_09_21T16_01_04.84Z.image_lev1.fits",
                      aia_header(date="2025-09-21T16:01:04.84", OSCNMEAN=0.0))
    raw = path.read_bytes()
    card = b"OSCNMEAN= " + b"0.0".rjust(20)
    assert card in raw
    path.write_bytes(raw.replace(card, b"OSCNMEAN= " + b"nan".rjust(20)))
    r = lib.ingest(path)
    assert r.status == "added"
    assert [f.code for f in r.findings if f.severity == "warning"] == []
    assert "nan_value" in [f.code for f in r.findings if f.severity == "info"]


def test_unparsable_card_that_is_not_nan_is_a_warning(lib, src):
    path = write_fits(src / "aia.lev1.193A_2025_09_21T16_02_04.84Z.image_lev1.fits",
                      aia_header(date="2025-09-21T16:02:04.84", OSCNMEAN=0.0))
    raw = path.read_bytes()
    card = b"OSCNMEAN= " + b"0.0".rjust(20)
    path.write_bytes(raw.replace(card, b"OSCNMEAN= " + b"1.2.3".rjust(20)))
    r = lib.ingest(path)
    assert [f.code for f in r.findings if f.severity == "warning"] == ["unparsable_card"]


def test_detector_repeating_the_mission_does_not_split_a_dataset(src):
    with_det = write_fits(src / "PUNCH_L3_CTM_20250921170000_v0l.fits", punch_header(DETECTOR="PUNCH"))
    without = write_fits(src / "PUNCH_L3_CTM_20250921171600_v0k.fits",
                         punch_header(date="2025-09-21T17:16:00.000", version="0k"))
    assert fitsmeta.read(with_det).dataset_key == fitsmeta.read(without).dataset_key


def test_aspiics_products_are_separate_datasets(src):
    base = {"OBSRVTRY": "Proba-3", "INSTRUME": "ASPIICS", "DETECTOR": "ASPIICS", "LEVEL": "L3",
            "WAVELNTH": 5513, "WAVEUNIT": "Angstrom", "DATE-OBS": "2025-09-21T18:20:34.056"}
    pb = write_fits(src / "aspiics_pb_l3_20250921T182034_163060040001_v03.fits",
                    {**base, "BTYPE": "Polarized brightness"})
    pa = write_fits(src / "aspiics_pa_l3_20250921T182034_163060040001_v03.fits",
                    {**base, "BTYPE": "Polarization angle"})
    a, b = fitsmeta.read(pb), fitsmeta.read(pa)
    assert a.obs_key != b.obs_key
    assert a.label == "Proba-3 ASPIICS 551.3 nm Polarized brightness L3"


def test_release_with_slightly_different_wavelength_is_a_new_version(lib, src):
    base = {"OBSRVTRY": "Proba-3", "INSTRUME": "ASPIICS", "LEVEL": "L3", "WAVEUNIT": "Angstrom",
            "DATE-OBS": "2025-09-21T18:23:11.058"}
    old = write_fits(src / "aspiics_bt_l3_163040050001_20250921T182311.fits", {**base, "WAVELNTH": 5508})
    new = write_fits(src / "aspiics_bt_l3_20250921T182311_163040050001_v03.fits", {**base, "WAVELNTH": 5511})
    fe = write_fits(src / "aspiics_fe_l3_20250921T182311_163040050001_v03.fits", {**base, "WAVELNTH": 5303})
    assert lib.ingest(old).status == "added"
    r = lib.ingest(new)
    assert r.status == "new_version"
    assert "wavelength_snapped" in [f.code for f in r.findings]
    assert lib.ingest(fe).status == "added"  # a different line is a different dataset
    assert sorted(d["wavelength"] for d in lib.datasets()) == [5303, 5508]


def test_wavelength_unit_spelled_out_is_converted(src):
    """PUNCH writes WAVEUNIT = 'nanometer'; the unit used to be unknown and 530 was read as 530 Å."""
    f = write_fits(src / "PUNCH_L3_CAM_20250921203200_v0l.fits",
                   {**punch_header(), "WAVELNTH": 530, "WAVEUNIT": "nanometer"})
    meta = fitsmeta.read(f)
    assert meta.wavelength == 5300.0
    assert "530 nm" in meta.label
    assert fitsmeta.RULES_VERSION >= 4  # keys built at 530 are stale until `heliad reindex`
