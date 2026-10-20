"""Read-only opening, reindex, delete, truncation, download jobs and the schema 2 migration."""

import gzip
import io
import json
import sqlite3
import time

import pytest

from heliad import config, db, fitsmeta, jobs, library, rpc, views
from heliad.library import Library, ReadOnlyLibrary
from heliad.query import Query
from heliad.rpc import Server
from heliad.sources import jsoc
from conftest import aia_header, punch_header, write_fits

ASPIICS = {"OBSRVTRY": "Proba-3", "INSTRUME": "ASPIICS", "LEVEL": "L3", "WAVEUNIT": "Angstrom",
           "DATE-OBS": "2025-09-21T18:23:11.058"}


# ---- read-only open and the config file -------------------------------------

def test_read_only_open_works_while_the_writer_holds_the_library(lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header()))
    with Library.open(lib.root, readonly=True) as ro:
        assert ro.stats()["files"] == 1
        assert len(ro.current_files()) == 1
        with pytest.raises(ReadOnlyLibrary):
            ro.ingest(src / "a.fits")
        with pytest.raises(ReadOnlyLibrary):
            ro.delete(file_ids=[1])


def test_read_only_open_of_a_missing_library_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIAD_HOME", str(tmp_path / "home"))
    with pytest.raises(FileNotFoundError):
        Library.open(tmp_path / "nothing", readonly=True)
    assert not (tmp_path / "nothing").exists()
    assert not config.exists()


def test_library_flag_does_not_redirect_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIAD_HOME", str(tmp_path / "home"))
    with Library.open(tmp_path / "main"):
        pass
    with Library.open(tmp_path / "scratch"):
        pass
    assert json.loads(config.config_path().read_text())["library_root"] == str(tmp_path / "main")
    with Library.open(tmp_path / "scratch") as other:
        other.make_default()
    assert json.loads(config.config_path().read_text())["library_root"] == str(tmp_path / "scratch")


def test_cli_read_commands_run_beside_a_writer(lib, src, capsys):
    from heliad import cli
    lib.ingest(write_fits(src / "a.fits", aia_header()))
    assert cli.main(["--library", str(lib.root), "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == 1


# ---- schema 2 ----------------------------------------------------------------

def test_schema_1_library_migrates_to_current(tmp_path):
    path = tmp_path / "heliad.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    for statement in db._split(db.SCHEMA_V1):
        conn.execute(statement)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.close()
    conn = db.connect_writer(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    assert db.meta_value(conn, "reader_compat") == str(db.READER_COMPAT)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(downloads)")}
    assert "job_id" in cols
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_reader_refuses_a_library_it_cannot_read(lib):
    db.set_meta(lib.conn, "reader_compat", db.SCHEMA_VERSION + 1)
    with pytest.raises(RuntimeError, match="newer Heliad"):
        Library.open(lib.root, readonly=True)


# ---- truncation --------------------------------------------------------------

@pytest.mark.parametrize("compressed", [False, True])
def test_cut_off_file_is_refused(lib, src, compressed):
    path = write_fits(src / "a.fits", aia_header(), compressed=compressed)
    assert fitsmeta.expected_size(path) == path.stat().st_size
    with open(path, "r+b") as fh:
        fh.truncate(path.stat().st_size - 2880)
    with pytest.raises(fitsmeta.IncompleteFile):
        lib.ingest(path)
    assert lib.stats()["files"] == 0


def test_file_cut_inside_its_header_is_refused(lib, src):
    path = write_fits(src / "a.fits", aia_header())
    with open(path, "r+b") as fh:
        fh.truncate(1000)
    with pytest.raises(fitsmeta.IncompleteFile):
        lib.ingest(path)


def test_ingest_path_says_which_refusals_are_final(lib, src):
    """Studio stops offering a file the core calls unstorable, and offers again one that
    failed for any other reason."""
    server = Server(lib, out=io.StringIO())
    call = lambda path: server.handle_line(json.dumps(  # noqa: E731
        {"jsonrpc": "2.0", "id": 1, "method": "ingest.path", "params": {"path": str(path)}}))
    cut = write_fits(src / "cut.fits", aia_header())
    with open(cut, "r+b") as fh:
        fh.truncate(1000)
    junk = src / "junk.fits"
    junk.write_bytes(b"NOTFITS = 1".ljust(80) + b"END".ljust(2800))  # whole, but astropy cannot read it
    for path in (cut, junk):
        assert call(path)["error"]["code"] == rpc.UNSTORABLE, path.name
    assert call(src / "gone.fits")["error"]["code"] == rpc.INTERNAL_ERROR  # not the file's fault

    # gzip-wrapped FITS, as Studio caches it: named .fits.gz, or by a bare digest before 2026-09-28
    for name, wave in (("a.fits.gz", 193), ("0123abcd", 171)):
        plain = write_fits(src / f"{wave}.fits", aia_header(wave=wave))
        (src / name).write_bytes(gzip.compress(plain.read_bytes()))
        assert call(src / name).get("result", {}).get("status") == "added", name
    assert lib.stats()["files"] == 2


# ---- reindex -----------------------------------------------------------------

def test_reindex_merges_releases_split_by_older_rules(lib, src, monkeypatch):
    old = write_fits(src / "aspiics_bt_l3_163040050001_20250921T182311.fits", {**ASPIICS, "WAVELNTH": 5508})
    new = write_fits(src / "aspiics_bt_l3_20250921T182311_163040050001_v03.fits",
                     {**ASPIICS, "WAVELNTH": 5511, "FILEVRSN": "03"})
    monkeypatch.setattr(library, "WAVELENGTH_BAND_TOLERANCE", 0.0)  # the rules before snapping
    a = lib.ingest(old)
    b = lib.ingest(new)
    monkeypatch.undo()
    assert lib.stats()["datasets"] == 2 and lib.stats()["observations"] == 2
    views.create(lib, "event", start="2025-09-21T00:00", end="2025-09-22T00:00")
    db.set_meta(lib.conn, "rules_version", 0)
    assert lib.stats()["reindex_due"]

    preview = lib.reindex(dry_run=True)
    assert preview["datasets_after"] == 1 and preview["observations_after"] == 1
    assert lib.stats()["datasets"] == 2  # a dry run changes nothing

    lib.reindex()
    stats = lib.stats()
    assert stats["datasets"] == 1 and stats["observations"] == 1 and stats["files"] == 2
    assert not stats["reindex_due"]
    (cur,) = lib.current_files()
    assert cur["file_id"] == b.file_id
    (member,) = views.members(lib, "event")
    assert member["file_id"] == b.file_id
    assert {v["file_id"] for v in lib.versions(cur["observation_id"])} == {a.file_id, b.file_id}


def test_reindex_of_a_current_library_changes_nothing(lib, src):
    for minute in range(3):
        lib.ingest(write_fits(src / f"a{minute}.fits", aia_header(date=f"2025-09-21T16:0{minute}:00")))
    before = lib.current_files()
    result = lib.reindex()
    assert result["files_moved"] == 0 and result["current_changed"] == 0
    assert lib.current_files() == before


# ---- delete ------------------------------------------------------------------

def _punch_versions(lib, src):
    out = {}
    for v in ("0k", "0l"):
        p = write_fits(src / f"PUNCH_L3_CTM_20250921170000_v{v}.fits", punch_header(version=v), compressed=True)
        out[v] = lib.ingest(p)
    return out


def test_delete_current_version_makes_the_older_one_current(lib, src):
    r = _punch_versions(lib, src)
    views.create(lib, "event", start="2025-09-21T00:00")
    views.materialize(lib, "event")
    preview = lib.delete(file_ids=[r["0l"].file_id], dry_run=True)
    assert preview["files"] == 1 and preview["repointed"] == 1
    assert lib.stats()["files"] == 2
    assert lib.abspath(r["0l"].relpath).exists()

    result = lib.delete(file_ids=[r["0l"].file_id])
    assert result["repointed"] == 1 and result["views_rebuilt"] == ["event"]
    assert not lib.abspath(r["0l"].relpath).exists()
    (cur,) = lib.current_files()
    assert cur["version"] == "0k"
    (member,) = views.members(lib, "event")
    assert member["file_id"] == r["0k"].file_id


def test_delete_last_file_removes_observation_and_dataset(lib, src):
    r = _punch_versions(lib, src)
    lib.ingest(write_fits(src / "aia.fits", aia_header()))
    views.create(lib, "event", start="2025-09-21T00:00")
    key = next(d["dataset_key"] for d in lib.datasets() if d["observatory"] == "PUNCH")
    result = lib.delete(dataset_keys=[key])
    assert result["files"] == 2 and result["observations"] == 1 and result["datasets"] == 1
    assert result["bytes_released"] > 0
    assert [d["observatory"] for d in lib.datasets()] == ["SDO"]
    assert len(views.members(lib, "event")) == 1
    assert not lib.abspath(r["0k"].relpath).parent.exists()  # empty store folders pruned
    assert lib.verify()["missing"] == []


# ---- download jobs -----------------------------------------------------------

class FakeSource:
    """Stands in for an archive: writes what it is told into staging."""

    def __init__(self, src, files=(), fail=None, during=None):
        self.src, self.files, self.fail, self.during = src, files, fail, during
        self.calls = 0

    def fetch(self, query, staging, *, settings, phase):
        self.calls += 1
        phase("searching")
        if self.during:
            self.during()
        phase("fetching", 0, len(self.files))
        paths = []
        for name, header, cut in self.files:
            p = staging / name
            if not p.exists():
                write_fits(p, header)
                if cut:
                    with open(p, "r+b") as fh:
                        fh.truncate(p.stat().st_size - 2880)
            paths.append(p)
        if self.fail:
            raise RuntimeError(self.fail)
        return {"found": len(paths), "paths": paths, "errors": [],
                "urls": {p.name: f"https://archive.example/{p.name}" for p in paths}}


QUERY = Query(start="2025-09-21T16:00:00", end="2025-09-21T17:00:00", instrument="AIA", wavelength=193.0)


def test_job_ingests_good_files_and_rejects_cut_off_ones(lib, src):
    fake = FakeSource(src, files=[("good.fits", aia_header(), False),
                                  ("cut.fits", aia_header(date="2025-09-21T16:01:00"), True)])
    seen = []
    runner = jobs.JobRunner(lib, notify=lambda m, p: seen.append((m, p)), source_modules={"fake": fake})
    job = runner.enqueue(QUERY, "fake")
    assert job["status"] == "queued" and job["query"]["instrument"] == "AIA"
    (done,) = runner.run_pending()
    assert done["status"] == "partial" and done["error"].startswith("stored 1 of 2 files")
    assert done["result"]["ingest"] == {"added": 1, "incomplete": 1}
    assert lib.stats()["files"] == 1
    rows = lib.reader.execute("SELECT status, job_id, url FROM downloads ORDER BY id").fetchall()
    assert [(r["status"], r["job_id"]) for r in rows] == [("done", job["job_id"]), ("failed", job["job_id"])]
    (cur,) = lib.current_files()
    assert lib.reader.execute("SELECT source_url FROM files").fetchone()[0].endswith("good.fits")
    assert not (lib.cfg.staging_dir / f"job-{job['job_id']}").exists()
    assert seen[-1][0] == "download.finished"
    assert any(m == "download.progress" and p["phase"] == "ingesting" for m, p in seen)


def test_failed_job_keeps_staged_files_and_resumes(lib, src):
    fake = FakeSource(src, files=[("good.fits", aia_header(), False)], fail="archive went away")
    runner = jobs.JobRunner(lib, source_modules={"fake": fake})
    job = runner.enqueue(QUERY, "fake")
    (failed,) = runner.run_pending()
    assert failed["status"] == "failed" and "archive went away" in failed["error"]
    staged = lib.cfg.staging_dir / f"job-{job['job_id']}" / "good.fits"
    assert staged.exists()
    fake.fail = None
    runner.retry(job["job_id"])
    (done,) = runner.run_pending()
    assert done["status"] == "done" and lib.stats()["files"] == 1


def test_cancel_queued_and_active_jobs(lib, src):
    holder = {}
    fake = FakeSource(src, files=[("good.fits", aia_header(), False)],
                      during=lambda: holder["runner"].cancel(holder["job"]))
    runner = holder["runner"] = jobs.JobRunner(lib, source_modules={"fake": fake})
    queued = runner.enqueue(QUERY, "fake")
    assert runner.cancel(queued["job_id"])["status"] == "cancelled"
    assert runner.run_pending() == []
    active = runner.enqueue(QUERY, "fake")
    holder["job"] = active["job_id"]
    (job,) = runner.run_pending()
    assert job["status"] == "cancelled"
    assert lib.stats()["files"] == 0


def test_job_left_active_is_queued_again_on_start(lib):
    runner = jobs.JobRunner(lib, source_modules={"fake": FakeSource(None)})
    job = runner.enqueue(QUERY, "fake")
    lib.conn.execute("UPDATE jobs SET status = 'active' WHERE id = ?", (job["job_id"],))
    jobs.JobRunner(lib)
    assert jobs.list_jobs(lib, job_id=job["job_id"])[0]["status"] == "queued"


def test_rpc_enqueue_returns_before_the_download_runs(lib, src):
    out = io.StringIO()
    server = Server(lib, out=out)
    server.jobs._modules["fake"] = FakeSource(src, files=[("good.fits", aia_header(), False)])
    reply = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "download.enqueue",
                                           "params": {"query": json.loads(QUERY.to_json()), "source": "fake"}}))
    assert reply["result"]["status"] == "queued"
    server.jobs.start()
    try:
        for _ in range(100):
            if jobs.list_jobs(lib)[0]["status"] == "done":
                break
            time.sleep(0.05)
    finally:
        server.jobs.stop(timeout=5)
    assert jobs.list_jobs(lib)[0]["status"] == "done"
    notes = [json.loads(line) for line in out.getvalue().splitlines()]
    assert notes[-1]["method"] == "download.finished"
    listed = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "download.list"}))
    assert listed["result"][0]["result"]["ingest"] == {"added": 1}
    hello = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "heliad.hello"}))
    assert hello["result"]["reader_compat"] == db.READER_COMPAT


def test_rpc_delete_is_a_dry_run_unless_told(lib, src):
    r = lib.ingest(write_fits(src / "a.fits", aia_header()))
    server = Server(lib, out=io.StringIO())
    call = lambda **p: server.handle_line(json.dumps(  # noqa: E731
        {"jsonrpc": "2.0", "id": 1, "method": "library.delete", "params": p}))["result"]
    assert call(file_ids=[r.file_id])["dry_run"] is True
    assert lib.stats()["files"] == 1
    assert call(file_ids=[r.file_id], dry_run=False)["files"] == 1
    assert lib.stats()["files"] == 0


# ---- JSOC and settings -------------------------------------------------------

def test_jsoc_query_and_record_set():
    q = Query(start="2025-09-21T16:00:00", end="2025-09-21T17:00:00", wavelength=193.0,
              sample_s=60, jsoc_series="aia.lev1_euv_12s")
    assert Query.from_json(q.to_json()) == q
    assert jsoc.recordset(q) == "aia.lev1_euv_12s[2025-09-21T16:00:00Z/3600s@60s][193]"
    hmi = Query(start="2025-09-21T16:00:00Z", end="2025-09-21T16:12:00Z", jsoc_series="hmi.m_720s",
                wavelength=6173.0)
    assert jsoc.recordset(hmi) == "hmi.m_720s[2025-09-21T16:00:00Z/720s]"
    with pytest.raises(ValueError):
        jsoc.recordset(Query(start="2025-09-21T16:00:00", end="2025-09-21T17:00:00"))


def test_settings_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIAD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HELIAD_JSOC_EMAIL", raising=False)
    assert config.load_settings() == {}
    config.save_setting("jsoc_email", "someone@example.org")
    assert config.load_settings()["jsoc_email"] == "someone@example.org"
    monkeypatch.setenv("HELIAD_JSOC_EMAIL", "other@example.org")
    assert config.load_settings()["jsoc_email"] == "other@example.org"
    assert config.save_setting("jsoc_email", None) == {}


def test_location_warnings(tmp_path):
    assert config.location_warnings(tmp_path / "HeliadLibrary") == []
    (synced,) = config.location_warnings(tmp_path / "Dropbox" / "HeliadLibrary")
    assert "sync service" in synced


# ---- ASPIICS sequences -------------------------------------------------------

def test_aspiics_releases_pair_by_sequence_id_not_date(lib, src, monkeypatch):
    # Release 1.0 dates a pB sequence by its last frame, release 1.1 by its first.
    base = {**ASPIICS, "BTYPE": "Polarized brightness"}
    old = write_fits(src / "aspiics_pb_l3_163060040001_20250921T182136.fits",
                     {**base, "WAVELNTH": 5508, "DATE-OBS": "2025-09-21T18:21:36.057"})
    new = write_fits(src / "aspiics_pb_l3_20250921T182034_163060040001_v03.fits",
                     {**base, "WAVELNTH": 5513, "DATE-OBS": "2025-09-21T18:20:34.056"})
    other = write_fits(src / "aspiics_pb_l3_20250921T182738_163040060001_v03.fits",
                       {**base, "WAVELNTH": 5513, "DATE-OBS": "2025-09-21T18:27:38.062"})
    assert fitsmeta.read(new).sequence == "163060040001"
    hexid = write_fits(src / "aspiics_pb_l3_1630400A0001_20250921T183756.fits", base)
    assert fitsmeta.read(hexid).sequence == "1630400A0001"
    assert lib.ingest(old).status == "added"
    assert lib.ingest(new).status == "new_version"
    assert lib.ingest(other).status == "added"
    cur = lib.current_files()
    assert [c["date_obs"] for c in cur] == ["2025-09-21T18:20:34.056", "2025-09-21T18:27:38.062"]
    assert [c["version"] for c in cur] == ["03", "03"]

    # A library keyed by date before this rule is merged the same way by reindex.
    before = lib.current_files()
    monkeypatch.setattr(fitsmeta, "_sequence", lambda header, name: None)
    lib.reindex()
    assert len(lib.current_files()) == 3
    monkeypatch.undo()
    lib.reindex()
    assert lib.current_files() == [{**b, "observation_id": a["observation_id"]}
                                   for a, b in zip(lib.current_files(), before)]


def test_sequence_id_is_aspiics_only(src):
    name = "PUNCH_L3_CTM_20250921170000_v0l.fits"
    assert fitsmeta.read(write_fits(src / name, punch_header())).sequence is None
