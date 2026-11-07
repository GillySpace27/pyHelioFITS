"""Download outcomes found by the first real archive runs: a fetch that stores
nothing is a failure, one that stores some is partial, and a retry refetches
only what is missing. Also schema 3 and the VSO mirror fallback."""

import sqlite3

import pytest

from pyheliofits.core import db, jobs
from pyheliofits.core.sources import fido
from conftest import aia_header, write_fits
from test_blockers import QUERY, FakeSource


class ErrorSource(FakeSource):
    """Finds `found` records and delivers only the files it was given."""

    def __init__(self, src, files=(), found=0, errors=()):
        super().__init__(src, files)
        self.found, self.errors = found, list(errors)

    def fetch(self, query, staging, *, settings, phase, skip=None):
        out = super().fetch(query, staging, settings=settings, phase=phase, skip=skip)
        self.seen = sorted(p.name for p in staging.iterdir())
        return {**out, "found": self.found, "errors": list(self.errors)}


def test_fetch_that_stores_nothing_is_a_failure(lib, src):
    fake = ErrorSource(src, found=5, errors=["https://sdo7.example/1,\nTimeout on reading data from socket"])
    runner = jobs.JobRunner(lib, source_modules={"fake": fake})
    job = runner.enqueue(QUERY, "fake")
    (failed,) = runner.run_pending()
    assert failed["status"] == "failed"
    assert failed["files_total"] == 5 and failed["files_done"] == 0
    assert failed["error"].startswith("stored 0 of 5 files; https://sdo7.example/1, Timeout")
    assert failed["result"]["found"] == 5 and failed["result"]["stored"] == 0
    assert runner.retry(job["job_id"])["status"] == "queued"


def test_fetch_that_stores_some_is_partial_and_retry_refetches_only_the_rest(lib, src):
    one = [("a.fits", aia_header(), False)]
    fake = ErrorSource(src, files=one, found=2, errors=["https://sdo7.example/2, server error"])
    runner = jobs.JobRunner(lib, source_modules={"fake": fake})
    job = runner.enqueue(QUERY, "fake")
    (partial,) = runner.run_pending()
    assert partial["status"] == "partial"
    assert (partial["files_total"], partial["files_done"]) == (2, 1)
    assert "(and" not in partial["error"] and partial["error"].startswith("stored 1 of 2 files")
    assert lib.stats()["files"] == 1

    # The retry finds the stored file back in staging, so it is not fetched again.
    fake.files = one + [("b.fits", aia_header(date="2025-09-21T16:01:04.84"), False)]
    fake.errors = []
    runner.retry(job["job_id"])
    (done,) = runner.run_pending()
    assert fake.seen == ["a.fits", "b.fits"]
    assert done["status"] == "done" and done["error"] is None
    assert done["result"]["ingest"] == {"duplicate": 1, "added": 1}
    assert lib.stats()["files"] == 2
    stored = lib.abspath(lib.current_files()[0]["relpath"])
    assert stored.exists()  # the retry consumed its link in staging, not the stored file


def test_done_jobs_cannot_be_retried(lib, src):
    runner = jobs.JobRunner(lib, source_modules={"fake": ErrorSource(src, found=0)})
    job = runner.enqueue(QUERY, "fake")
    (done,) = runner.run_pending()
    assert done["status"] == "done"
    with pytest.raises(ValueError):
        runner.retry(job["job_id"])


@pytest.mark.parametrize("found, stored, errors, counts, status", [
    (5, 5, [], {"added": 5}, "done"),
    (5, 5, ["a slow mirror, then a good one"], {"added": 5}, "done"),
    (5, 0, ["e"] * 5, {}, "failed"),
    (5, 3, ["e", "e"], {"added": 3}, "partial"),
    (2, 1, [], {"added": 1, "incomplete": 1}, "partial"),
    (None, 0, ["e"], {}, "failed"),
    (None, 2, [], {"added": 2}, "done"),
    (0, 0, [], {}, "done"),
])
def test_outcome(found, stored, errors, counts, status):
    got, error = jobs.outcome(found, stored, errors, counts)
    assert got == status
    assert (error is None) == (status == "done")


def test_schema_2_library_with_jobs_migrates_to_3(tmp_path):
    path = tmp_path / "hfcore.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    for statement in db._split(db.SCHEMA_V1) + db._split(db.SCHEMA_V2):
        conn.execute(statement)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '2')")
    conn.execute("INSERT INTO queries(query_json, source, created_at) VALUES ('{}', 'fido', 't')")
    conn.execute("INSERT INTO jobs(source, query_id, status, created_at, updated_at) "
                 "VALUES ('fido', 1, 'done', 't', 't')")
    conn.execute("INSERT INTO downloads(query_id, record_json, status, updated_at, job_id) "
                 "VALUES (1, '{}', 'failed', 't', 1)")
    conn.close()
    conn = db.connect_writer(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "done"
    conn.execute("UPDATE jobs SET status = 'partial'")
    assert conn.execute("SELECT job_id FROM downloads").fetchone()[0] == 1
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE jobs SET status = 'unknown'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO downloads(query_id, record_json, status, updated_at, job_id) "
                     "VALUES (1, '{}', 'failed', 't', 99)")
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'jobs_status'").fetchone()


# ---- the VSO mirror fallback ---------------------------------------------------

class FakeResults(list):
    def __init__(self, paths=(), errors=()):
        super().__init__(paths)
        self.errors = list(errors)


class FakeFido:
    """Fido with one VSO block: each fetch delivers the names its site serves."""

    def __init__(self, staging, serves):
        from sunpy.net.vso import VSOClient

        self.staging, self.serves, self.sites = staging, serves, []
        self.block = type("Block", (), {"client": VSOClient.__new__(VSOClient)})()

    def search(self, *attrs):
        result = FakeResults([self.block])
        result.file_num = 3
        return result

    def fetch(self, *blocks, path, overwrite, max_conn, progress, site="default"):
        self.sites.append(site)
        names = self.serves.get(site, [])
        for name in names:
            write_fits(self.staging / name, aia_header())
        missing = 3 - len(list(self.staging.iterdir()))
        return FakeResults([self.staging / n for n in names],
                           [f"{site} failed"] * max(missing, 0))


@pytest.fixture
def staging(tmp_path):
    out = tmp_path / "job-1"
    out.mkdir()
    return out


def _fetch(monkeypatch, staging, serves, settings=None):
    import sunpy.net

    fake = FakeFido(staging, serves)
    monkeypatch.setattr(sunpy.net, "Fido", fake)
    phases = []
    out = fido.fetch(QUERY, staging, settings=settings or {},
                     phase=lambda name, done=None, total=None: phases.append(name))
    return fake, out, phases


def test_vso_records_missing_from_the_first_mirror_come_from_the_next(monkeypatch, staging):
    fake, out, phases = _fetch(monkeypatch, staging,
                               {"default": ["a.fits"], "NSO": [], "SAO": ["b.fits", "c.fits"]})
    assert fake.sites == ["default", "NSO", "SAO"]
    assert [p.name for p in out["paths"]] == ["a.fits", "b.fits", "c.fits"]
    assert out["errors"] == [] and out["found"] == 3
    assert [a["site"] for a in out["attempts"]] == ["default", "NSO", "SAO"]
    assert "fetching from mirror NSO" in phases


def test_mirrors_are_not_tried_when_nothing_is_missing(monkeypatch, staging):
    fake, out, _ = _fetch(monkeypatch, staging, {"default": ["a.fits", "b.fits", "c.fits"]})
    assert fake.sites == ["default"] and out["errors"] == []


def test_mirror_list_comes_from_settings(monkeypatch, staging):
    fake, out, _ = _fetch(monkeypatch, staging, {}, settings={"vso_mirrors": "ROB, MPS"})
    assert fake.sites == ["default", "ROB", "MPS"]
    assert out["errors"] == ["MPS failed"] * 3
    fake, _, _ = _fetch(monkeypatch, staging, {}, settings={"vso_mirrors": "none"})
    assert fake.sites == ["default"]
    assert fido.mirrors({}) == list(fido.VSO_MIRRORS)
