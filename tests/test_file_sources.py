"""file_sources (schema 6) and ingest.path consume: the library as a downloader's one store."""
import io
import json
import shutil
import sqlite3

from heliofits.core import db
from heliofits.core.rpc import Server
from conftest import aia_header, write_fits


def _aia(src, name="aia.lev1.193A_2025_09_21T16_00_04.84Z.image_lev1.fits"):
    return write_fits(src / name, aia_header())


def test_every_url_finds_the_stored_file_duplicates_included(lib, src):
    path = _aia(src)
    first = lib.ingest(path, source_url="https://a.example/x.fits")
    copy = shutil.copy(path, src / "again.fits")
    dup = lib.ingest(copy, source_url="https://mirror.example/x.fits")
    assert dup.status == "duplicate" and dup.file_id == first.file_id
    stored = lib.abspath(first.relpath)
    assert lib.path_for_source("https://a.example/x.fits") == stored
    assert lib.path_for_source("https://mirror.example/x.fits") == stored  # was lost before schema 6
    assert lib.path_for_source("https://nowhere.example/x.fits") is None


def test_consume_moves_the_download_and_a_duplicate_leaves_nothing(lib, src):
    path = _aia(src)
    r = lib.ingest(path, consume=True, source_url="https://a.example/x.fits")
    assert r.status == "added" and not path.exists()
    again = _aia(src)
    d = lib.ingest(again, consume=True, source_url="https://b.example/x.fits")
    assert d.status == "duplicate" and not again.exists()
    assert lib.stats()["files"] == 1


def test_rpc_ingest_path_takes_consume(lib, src):
    path = _aia(src)
    out = io.StringIO()
    Server(lib, out=out).serve(io.StringIO(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "ingest.path",
         "params": {"path": str(path), "source_url": "https://a.example/x.fits", "consume": True}}) + "\n"))
    reply = json.loads(out.getvalue().splitlines()[0])
    assert reply["result"]["status"] == "added"
    assert not path.exists()
    assert lib.path_for_source("https://a.example/x.fits") is not None


def test_delete_forgets_the_urls(lib, src):
    r = lib.ingest(_aia(src), source_url="https://a.example/x.fits")
    lib.delete(file_ids=[r.file_id])
    assert lib.path_for_source("https://a.example/x.fits") is None


def test_schema_5_index_gains_file_sources_from_source_url(tmp_path):
    path = tmp_path / "hfcore.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for v in range(1, 6):
        conn.execute("BEGIN")
        for stmt in db._split(db.MIGRATIONS[v]):
            conn.execute(stmt)
        if v in db.MIGRATION_STEPS:
            db.MIGRATION_STEPS[v](conn)
        db.set_meta(conn, "schema_version", v)
        conn.execute("COMMIT")
    conn.execute("INSERT INTO datasets(dataset_key, label) VALUES ('k', 'k')")
    conn.execute("INSERT INTO observations(obs_key, dataset_id, date_obs) VALUES ('o', 1, '')")
    conn.execute("INSERT INTO files(sha256, relpath, size, observation_id, original_name, source_url, "
                 "ingested_at, header_json) VALUES ('s', 'store/a.fits', 1, 1, 'a.fits', 'https://a/x', 't', '{}')")
    conn.close()
    conn = db.connect_writer(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT file_id FROM file_sources WHERE url = 'https://a/x'").fetchone()[0] == 1
    assert db.meta_value(conn, "reader_compat") == "1"
