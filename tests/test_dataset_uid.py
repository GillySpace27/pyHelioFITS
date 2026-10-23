"""Stable dataset ids (schema 5): the migration mints them, a key-changing reindex
carries them, and old keys resolve through dataset_aliases."""

import io
import os
import sqlite3
import stat
import uuid

import pytest
from astropy.io import fits
from conftest import punch_header, write_fits

from heliad import db, fitsmeta
from heliad.query import dataset_link, parse_dataset_link
from heliad.rpc import Server


def _schema_4(path):
    conn = sqlite3.connect(path, isolation_level=None)
    for v in (1, 2, 3, 4):
        for statement in db._split(db.MIGRATIONS[v]):
            conn.execute(statement)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '4')")
    conn.execute("INSERT INTO datasets(id, dataset_key, label) VALUES (1, 'a', 'A'), (2, 'b', 'B')")
    conn.execute("INSERT INTO observations(id, obs_key, dataset_id, date_obs) VALUES (1, 'o', 1, 't')")
    conn.execute("INSERT INTO files(id, sha256, relpath, size, observation_id, original_name, "
                 "ingested_at, header_json) VALUES (1, 's', 'r', 1, 1, 'n', 't', '{}')")
    conn.execute("UPDATE observations SET current_file_id = 1")
    conn.execute("INSERT INTO views(id, name, created_at) VALUES (1, 'event', 't')")
    conn.close()


def test_schema_4_migrates_to_5_minting_a_uid_for_every_row(tmp_path):
    path = tmp_path / "heliad.sqlite"
    _schema_4(path)
    conn = db.connect_writer(path)
    assert db.schema_version(conn) == 5 and db.meta_value(conn, "reader_compat") == "1"
    uids = [r[0] for r in conn.execute("SELECT dataset_uid FROM datasets ORDER BY id")]
    assert len(set(uids)) == 2 and all(uuid.UUID(u).version == 4 for u in uids)
    (view_uid,) = conn.execute("SELECT view_uid FROM views").fetchone()
    assert uuid.UUID(view_uid).version == 4
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE datasets SET dataset_uid = ? WHERE id = 2", (uids[0],))
    (row,) = conn.execute("SELECT dataset_uid FROM current_files").fetchall()
    assert row[0] == uids[0]
    conn.execute("INSERT INTO dataset_aliases(old_key, dataset_uid) VALUES ('old', ?)", (uids[0],))
    conn.execute("INSERT INTO file_aliases(old_relpath, file_id, sha256) VALUES ('r0', 1, 's')")
    with pytest.raises(sqlite3.IntegrityError):  # a file alias must name a real file
        conn.execute("INSERT INTO file_aliases(old_relpath, file_id, sha256) VALUES ('r1', 9, 'x')")
    conn.close()
    # The index as it was is kept beside it, and opening again changes nothing.
    backup = tmp_path / "heliad.sqlite.schema4.bak"
    assert backup.exists()
    old = sqlite3.connect(backup)
    assert old.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "4"
    old.close()
    conn = db.connect_writer(path)
    assert [r[0] for r in conn.execute("SELECT dataset_uid FROM datasets ORDER BY id")] == uids
    conn.close()


def test_new_datasets_and_views_get_uids(lib, src):
    from heliad import views
    lib.ingest(write_fits(src / "PUNCH_L3_CTM_20250921170000_v0l.fits", punch_header()))
    (d,) = lib.datasets()
    assert uuid.UUID(d["dataset_uid"]).version == 4
    views.create(lib, "event", dataset_keys=[d["dataset_key"]])
    (v,) = views.list_views(lib)
    assert uuid.UUID(v["view_uid"]).version == 4


def _punch_530(src, n):
    return [write_fits(src / f"PUNCH_L3_CAM_2025092120{i:02d}00_v0l.fits",
                       {**punch_header(date=f"2025-09-21T20:{i:02d}:00.000"), "TYPECODE": "CA",
                        "WAVELNTH": 530, "WAVEUNIT": "nanometer"})
            for i in range(n)]


def test_reindex_carries_the_uid_across_a_key_change(lib, src, monkeypatch):
    # Ingest as Heliad did before it read WAVEUNIT 'nanometer' (530 taken as 530 Å) ...
    monkeypatch.delitem(fitsmeta._WAVE_TO_ANGSTROM, "nanometer", raising=False)
    for f in _punch_530(src, 3):
        lib.ingest(f)
    (before,) = lib.datasets()
    assert before["dataset_key"].endswith("/530")
    # ... then reindex under rules that read it (5300 Å): the key changes, the uid stays.
    monkeypatch.setitem(fitsmeta._WAVE_TO_ANGSTROM, "nanometer", 10.0)
    out = lib.reindex()
    (after,) = lib.datasets()
    assert after["dataset_key"].endswith("/5300")
    assert after["dataset_uid"] == before["dataset_uid"] and after["id"] == before["id"]
    assert out["datasets_renamed"] == [{"old_key": before["dataset_key"], "new_key": after["dataset_key"],
                                        "dataset_uid": before["dataset_uid"]}]
    # The old key still finds the dataset, through its alias.
    assert lib.resolve_dataset(before["dataset_key"]) == {
        "dataset_uid": before["dataset_uid"], "dataset_key": after["dataset_key"], "resolved_by": "alias"}
    assert lib.resolve_dataset(after["dataset_key"])["resolved_by"] == "key"
    assert lib.resolve_dataset(before["dataset_uid"])["resolved_by"] == "uid"
    assert lib.resolve_dataset("nothing/here") is None
    # A second reindex renames nothing and keeps the uid.
    assert lib.reindex()["datasets_renamed"] == []
    assert lib.datasets()[0]["dataset_uid"] == before["dataset_uid"]


def test_reindex_gives_a_split_dataset_one_uid_by_majority(lib, src, monkeypatch):
    monkeypatch.delitem(fitsmeta._WAVE_TO_ANGSTROM, "nanometer", raising=False)
    files = _punch_530(src, 3)
    for f in files:
        lib.ingest(f)
    (before,) = lib.datasets()
    # Under the new rules one file of three reads 'nm' as written and two read 'nanometer'
    # as before (530 Å): the /530 key survives for two files, so the uid stays with it.
    monkeypatch.setitem(fitsmeta._WAVE_TO_ANGSTROM, "nanometer", 1.0)
    stored = lib.abspath(lib.current_files()[0]["relpath"])
    os.chmod(stored, stat.S_IWUSR | stat.S_IRUSR)
    with fits.open(stored, mode="update") as hdul:
        hdul[0].header["WAVEUNIT"] = "nm"
    out = lib.reindex()
    keys = {d["dataset_key"]: d["dataset_uid"] for d in lib.datasets()}
    assert keys[before["dataset_key"]] == before["dataset_uid"]
    (other,) = [u for k, u in keys.items() if k != before["dataset_key"]]
    assert other != before["dataset_uid"]
    assert out["datasets_renamed"] == []


def test_rpc_dataset_get_resolve_and_link(lib, src, monkeypatch):
    monkeypatch.delitem(fitsmeta._WAVE_TO_ANGSTROM, "nanometer", raising=False)
    for f in _punch_530(src, 2):
        lib.ingest(f)
    (old,) = lib.datasets()
    monkeypatch.setitem(fitsmeta._WAVE_TO_ANGSTROM, "nanometer", 10.0)
    lib.reindex()
    m = Server(lib, out=io.StringIO()).methods
    by_uid = m["dataset.get"](dataset_uid=old["dataset_uid"])
    assert by_uid["resolved_by"] == "uid" and by_uid["observations"] == 2
    by_old_key = m["dataset.get"](dataset_key=old["dataset_key"])
    assert by_old_key["resolved_by"] == "alias" and by_old_key["dataset_uid"] == old["dataset_uid"]
    assert m["dataset.get"](dataset_uid="no-such-uid") is None
    assert m["dataset.resolve"](key=old["dataset_key"])["dataset_uid"] == old["dataset_uid"]

    url = dataset_link(old["dataset_uid"], "2025-09-21T20:00:00", "2025-09-21T21:00:00")
    assert url == (f"heliad://dataset/{old['dataset_uid']}"
                   "?start=2025-09-21T20%3A00%3A00&end=2025-09-21T21%3A00%3A00")
    link = m["dataset.from_url"](url=url)
    assert link["start"] == "2025-09-21T20:00:00" and link["end"] == "2025-09-21T21:00:00"
    assert link["dataset"]["dataset_uid"] == old["dataset_uid"]
    assert parse_dataset_link(f"heliad://dataset/{old['dataset_uid']}")["start"] is None
    for bad in ("heliad://search?start=a&end=b", "heliad://dataset/", "heliad://dataset/x?colour=red"):
        with pytest.raises(ValueError):
            parse_dataset_link(bad)
