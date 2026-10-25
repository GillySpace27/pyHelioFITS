"""The cache editor's Upgrade panel: rejecting a frame, refetching one, densifying a
dataset by fetching only the frames it lacks, and comparing its cadence with the
archive's. Archives are faked; nothing here reaches one."""

import io
import shutil
import sqlite3

import pytest

from heliofits_core import coverage, db, jobs, upgrade
from heliofits_core.query import Query
from heliofits_core.rpc import Server
from conftest import aia_header, punch_header, write_fits

LIB_TIMES = ["2025-09-21T16:00:00.840", "2025-09-21T16:00:24.840",
             "2025-09-21T16:00:48.840", "2025-09-21T16:01:12.840"]  # every 24 s
ARCHIVE_TIMES = [f"2025-09-21T16:{s // 60:02d}:{s % 60:02d}.000" for s in range(0, 84, 12)]  # every 12 s
AIA = "sdo/aia/-/-/1/193"


class Archive:
    """An archive holding one frame at each of `frames`. search reports their times
    the way the fido source does (per provider); fetch writes those in the query's
    range that `skip` does not exclude, and counts the rest as skipped."""

    def __init__(self, frames, header=None):
        self.frames, self.queries = list(frames), []
        self.header = header or (lambda t: aia_header(date=t))

    def _in(self, query):
        lo, hi = upgrade.seconds(query.start), upgrade.seconds(query.end)
        return [t for t in self.frames if lo <= upgrade.seconds(t) <= hi]

    def search(self, query, *, settings=None):
        self.queries.append(query)
        times = self._in(query)
        return {"query": query.to_json(), "total": len(times),
                "providers": [{"client": "FakeClient", "count": len(times), "times": times}]}

    def fetch(self, query, staging, *, settings, phase, skip=None):
        self.queries.append(query)
        phase("searching")
        todo = self._in(query)
        kept = [t for t in todo if not (skip and skip(t))]
        phase("fetching", 0, len(kept))
        paths = []
        for t in kept:
            p = staging / f"frame_{t.replace(':', '').replace('.', '_')}.fits"
            if not p.exists():
                write_fits(p, self.header(t))
            paths.append(p)
        return {"found": len(kept), "paths": paths, "errors": [], "urls": {},
                "skipped": len(todo) - len(kept)}


def _fill(lib, src, times=LIB_TIMES):
    for i, t in enumerate(times):
        lib.ingest(write_fits(src / f"lib{i}.fits", aia_header(date=t)))
    return {r["date_obs"]: r["observation_id"] for r in lib.current_files(dataset_key=AIA)}


@pytest.fixture
def server(lib):
    return Server(lib, out=io.StringIO())


# ---- rejecting -------------------------------------------------------------------

def test_reject_hides_a_frame_from_current_files_and_coverage(lib, src):
    obs = _fill(lib, src)
    bad = obs[LIB_TIMES[1]]
    out = lib.reject([bad], "smeared by a flare")
    assert out["rejected"] == 1
    assert [r["date_obs"] for r in lib.current_files()] == [LIB_TIMES[0], *LIB_TIMES[2:]]
    raw = lib.reader.execute("SELECT COUNT(*) FROM current_files").fetchone()[0]
    assert raw == 3  # what Studio reads
    (row,) = coverage.library(lib, bins=1)["datasets"]
    assert row["frames"] == 3
    every = lib.current_files(include_rejected=True)
    assert len(every) == 4
    assert [r["rejected"] for r in every] == [None, "smeared by a flare", None, None]
    (listed,) = lib.rejected()
    assert listed["observation_id"] == bad and listed["date_obs"] == LIB_TIMES[1]
    assert listed["reason"] == "smeared by a flare" and listed["rejected_at"]
    assert listed["relpath"] == every[1]["relpath"] and listed["file_id"] == every[1]["file_id"]
    assert lib.rejected(dataset_key="nothing/here") == []

    assert lib.unreject([bad])["unrejected"] == 1
    assert len(lib.current_files()) == 4 and lib.rejected() == []


def test_reject_needs_a_reason_and_a_real_observation(lib, src):
    obs = _fill(lib, src, LIB_TIMES[:1])
    with pytest.raises(ValueError):
        lib.reject(list(obs.values()), "  ")
    with pytest.raises(KeyError):
        lib.reject([999], "no such frame")
    assert lib.rejected() == []


def test_deleting_a_rejected_observation_drops_its_rejection(lib, src):
    obs = _fill(lib, src, LIB_TIMES[:2])
    lib.reject([obs[LIB_TIMES[0]]], "bad")
    assert lib.delete(observation_ids=[obs[LIB_TIMES[0]]], dry_run=True)["observations"] == 1
    assert len(lib.rejected()) == 1  # a dry run changes nothing
    lib.delete(observation_ids=[obs[LIB_TIMES[0]]], dry_run=False)
    assert lib.rejected() == []
    assert not lib.conn.execute("PRAGMA foreign_key_check").fetchall()


def test_a_rejection_survives_reindex(lib, src):
    obs = _fill(lib, src, LIB_TIMES[:2])
    lib.reject([obs[LIB_TIMES[1]]], "bad")
    lib.reindex()
    assert [r["date_obs"] for r in lib.rejected()] == [LIB_TIMES[1]]


# ---- the dataset's query -----------------------------------------------------------

def test_dataset_query_is_inferred_from_the_dataset_when_nothing_was_downloaded(lib, src):
    _fill(lib, src)
    out = upgrade.dataset_query(lib, AIA)
    assert out["inferred"] is True and out["source"] == "fido"
    q = out["query"]
    assert (q["start"], q["end"]) == (LIB_TIMES[0], LIB_TIMES[-1])
    assert q["instrument"] == "AIA" and q["wavelength"] == 193.0 and q["level"] == "1"
    assert q["sample_s"] == 24.0
    assert q["detector"] is None and q["jsoc_series"] is None  # nothing to read, nothing made up
    try:
        import sunpy.net  # noqa: F401
        assert q["source"] == "SDO"
    except ImportError:
        assert q["source"] is None
    assert "dataset" in out["basis"]


def test_dataset_query_prefers_the_stored_archive_query_widened_to_the_span(lib, src):
    _fill(lib, src, LIB_TIMES[:1])  # imported first, so it has no query
    stored = Query(start="2025-09-21T16:00:20", end="2025-09-21T16:00:30", instrument="AIA",
                   wavelength=193.0, provider="JSOC")
    runner = jobs.JobRunner(lib, source_modules={"fido": Archive(ARCHIVE_TIMES)})
    runner.enqueue(stored, "fido")
    runner.run_pending()
    out = upgrade.dataset_query(lib, AIA)
    assert out["inferred"] is False and out["source"] == "fido"
    assert out["query"]["provider"] == "JSOC"
    assert out["query"]["start"] == LIB_TIMES[0]           # widened to the dataset's first frame
    assert out["query"]["end"] == "2025-09-21T16:00:30.000"
    assert "stored query" in out["basis"]


def test_tolerance_is_half_the_spacing_clamped():
    assert upgrade.tolerance(24.0) == 12.0
    assert upgrade.tolerance(24.0, 12.0) == 6.0
    assert upgrade.tolerance(0.5) == 1.0
    assert upgrade.tolerance(3600.0) == 60.0
    assert upgrade.tolerance(None) == 60.0


# ---- upgrading ---------------------------------------------------------------------

def test_upgrade_dry_run_queues_nothing(server, lib, src):
    _fill(lib, src)
    out = server.methods["dataset.upgrade"](dataset_key=AIA, sample_s=12)
    assert out["dry_run"] is True and out["query"]["sample_s"] == 12
    assert out["options"] == {"dataset_key": AIA, "missing_only": True, "tolerance_s": 6.0}
    assert jobs.list_jobs(lib) == []


def test_upgrade_fetches_only_missing_frames_and_never_a_rejected_one(server, lib, src):
    obs = _fill(lib, src)
    lib.reject([obs[LIB_TIMES[1]]], "bad frame")
    archive = Archive(ARCHIVE_TIMES)
    server.jobs._modules["fido"] = archive
    job = server.methods["dataset.upgrade"](dataset_key=AIA, sample_s=12, dry_run=False)
    assert job["status"] == "queued" and job["options"]["missing_only"] is True
    (done,) = server.jobs.run_pending()
    assert done["status"] == "done"
    # The query spans the library's first to last frame, so the archive's 16:00:00.000 is outside it.
    assert done["result"]["skipped"] == 3 and done["result"]["found"] == 3
    assert done["result"]["ingest"] == {"added": 3}
    have = [r["date_obs"] for r in lib.current_files(dataset_key=AIA)]
    assert have == [LIB_TIMES[0], ARCHIVE_TIMES[1], ARCHIVE_TIMES[3], LIB_TIMES[2],
                    ARCHIVE_TIMES[5], LIB_TIMES[3]]
    assert [r["date_obs"] for r in lib.rejected()] == [LIB_TIMES[1]]  # still rejected, not refetched
    assert done["options"] == {"dataset_key": AIA, "missing_only": True, "tolerance_s": 6.0}


def test_cadences_lists_the_archive_frames_the_library_lacks(server, lib, src):
    _fill(lib, src)
    server.jobs._modules["fido"] = archive = Archive(ARCHIVE_TIMES)
    out = server.methods["dataset.cadences"](dataset_key=AIA)
    assert archive.queries[-1].sample_s is None
    assert out["library"] == {"count": 4, "median_spacing_s": 24.0, "rejected": 0}
    assert out["archive"] == {"count": 6, "median_spacing_s": 12.0}
    assert out["tolerance_s"] == 6.0
    assert out["missing"] == [ARCHIVE_TIMES[1], ARCHIVE_TIMES[3], ARCHIVE_TIMES[5]]
    assert out["query"]["sample_s"] is None


def test_refetch_asks_for_one_frame_and_an_identical_file_is_a_duplicate(server, lib, src):
    obs = _fill(lib, src)
    original = lib.abspath(lib.current_files()[1]["relpath"])

    class Same(Archive):
        def fetch(self, query, staging, *, settings, phase, skip=None):
            self.queries.append(query)
            shutil.copyfile(original, staging / "again.fits")
            return {"found": 1, "paths": [staging / "again.fits"], "errors": [], "urls": {}}

    server.jobs._modules["fido"] = archive = Same([])
    job = server.methods["library.refetch"](observation_id=obs[LIB_TIMES[1]])
    assert job["options"] == {"missing_only": False, "observation_id": obs[LIB_TIMES[1]]}
    assert (job["query"]["start"], job["query"]["end"]) == ("2025-09-21T16:00:12.840",
                                                           "2025-09-21T16:00:36.840")
    assert "sample_s" not in job["query"]
    (done,) = server.jobs.run_pending()
    assert done["result"]["ingest"] == {"duplicate": 1}
    assert archive.queries[0].sample_s is None


def test_refetch_brings_a_newer_version_in_as_current(server, lib, src):
    lib.ingest(write_fits(src / "p0k.fits", punch_header(version="0k")))
    (cur,) = lib.current_files()
    server.jobs._modules["fido"] = Archive([cur["date_obs"]],
                                           header=lambda t: punch_header(date=t, version="0l"))
    server.methods["library.refetch"](observation_id=cur["observation_id"])
    (done,) = server.jobs.run_pending()
    assert done["result"]["ingest"] == {"new_version": 1}
    (now_cur,) = lib.current_files()
    assert now_cur["version"] == "0l" and now_cur["observation_id"] == cur["observation_id"]


# ---- schema 4 ----------------------------------------------------------------------

def test_schema_3_library_migrates_to_4(tmp_path):
    path = tmp_path / "hfcore.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    for v in (1, 2, 3):
        for statement in db._split(db.MIGRATIONS[v]):
            conn.execute(statement)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '3')")
    conn.execute("INSERT INTO datasets(id, dataset_key, label) VALUES (1, 'd', 'D')")
    conn.execute("INSERT INTO observations(id, obs_key, dataset_id, date_obs) VALUES (1, 'o', 1, 't')")
    conn.execute("INSERT INTO files(id, sha256, relpath, size, observation_id, original_name, "
                 "ingested_at, header_json) VALUES (1, 's', 'r', 1, 1, 'n', 't', '{}')")
    conn.execute("UPDATE observations SET current_file_id = 1")
    conn.execute("INSERT INTO queries(query_json, source, created_at) VALUES ('{}', 'fido', 't')")
    conn.execute("INSERT INTO jobs(source, query_id, status, created_at, updated_at) "
                 "VALUES ('fido', 1, 'done', 't', 't')")
    before = [r[1] for r in conn.execute("PRAGMA table_info(current_files)")]
    conn.close()

    conn = db.connect_writer(path)
    # Migrates on through every later schema; 5 only appends dataset_uid to current_files.
    assert db.schema_version(conn) == db.SCHEMA_VERSION and db.meta_value(conn, "reader_compat") == "1"
    assert [r[1] for r in conn.execute("PRAGMA table_info(current_files)")] == [*before, "dataset_uid"]
    assert conn.execute("SELECT COUNT(*) FROM current_files").fetchone()[0] == 1
    assert conn.execute("SELECT options_json FROM jobs").fetchone()[0] is None
    conn.execute("INSERT INTO rejections(observation_id, reason, rejected_at) VALUES (1, 'bad', 't')")
    assert conn.execute("SELECT COUNT(*) FROM current_files").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO rejections(observation_id, reason, rejected_at) VALUES (9, 'x', 't')")
    conn.execute("DELETE FROM rejections")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO rejections(observation_id, reason, rejected_at) VALUES (1, NULL, 't')")


# ---- skip in the real sources --------------------------------------------------------

def test_fido_leaves_out_the_records_skip_claims(monkeypatch, tmp_path):
    import sunpy.net
    from astropy.time import Time
    from sunpy.net.base_client import QueryResponseTable
    from sunpy.net.vso import VSOClient

    from heliofits_core.sources import fido

    block = QueryResponseTable({"Start Time": Time(ARCHIVE_TIMES[:3])}, client=VSOClient.__new__(VSOClient))

    class Results(list):
        file_num = 3
        errors = []

    class FakeFido:
        def search(self, *attrs):
            return Results([block])

        def fetch(self, *blocks, path, overwrite, max_conn, progress, site="default"):
            self.got = [list(b["Start Time"].isot) for b in blocks]
            return Results()

    fake = FakeFido()
    monkeypatch.setattr(sunpy.net, "Fido", fake)
    skip = lambda t: t.startswith(ARCHIVE_TIMES[1][:19])  # noqa: E731
    out = fido.fetch(Query(start=ARCHIVE_TIMES[0], end=ARCHIVE_TIMES[2]), tmp_path, settings={},
                     phase=lambda *a, **k: None, skip=skip)
    assert fake.got == [[ARCHIVE_TIMES[0], ARCHIVE_TIMES[2]]]
    assert out["found"] == 2 and out["skipped"] == 1


def test_jsoc_reads_t_rec_from_a_record_name():
    from heliofits_core.sources import jsoc

    assert jsoc.record_time("aia.lev1_euv_12s[2025-09-21T16:00:00Z][193]") == "2025-09-21T16:00:00Z"
    assert jsoc.record_time("hmi.M_45s[2025.09.21_16:00:00_TAI][2]") == "2025.09.21_16:00:00_TAI"
    assert jsoc.record_time("no brackets") is None
