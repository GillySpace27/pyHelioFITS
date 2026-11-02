"""template.latest and its command line mirrors (`hfcore latest`, `hfcore template refresh`):
ask the archive for the newest frame of each dataset and queue one job for the span
before it when the library is behind. Archives are faked; nothing here reaches one."""

import io
import json
from pathlib import Path

import pytest

from heliofits.core import cli, jobs, latest, rpc, sources, upgrade
from heliofits.core.library import Library
from heliofits.core.rpc import Server
from tests.conftest import aia_header, write_fits

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = "2025-09-21T18:00:00.000"
LIB_TIMES = ["2025-09-21T16:00:00.840", "2025-09-21T16:00:24.840",
             "2025-09-21T16:00:48.840", "2025-09-21T16:01:12.840"]  # every 24 s
# The archive goes on every 24 s until 17:00:00.840, an hour past the library's newest frame.
ARCHIVE_TIMES = [upgrade._iso(upgrade.seconds(LIB_TIMES[0]) + 24 * i) for i in range(151)]
NEWEST = "2025-09-21T17:00:00.840"
AIA = "sdo/aia/-/-/1/193"
AIA171 = "sdo/aia/-/-/1/171"
UNKNOWN = "-/-/-/-/-/-"


class Archive:
    """One frame at each of `frames`; search and fetch keep to the query's range.
    A query for a wavelength in `broken` fails the way an archive that is down does."""

    def __init__(self, frames, broken=()):
        self.frames, self.broken, self.searches, self.fetches = list(frames), set(broken), [], []

    def _in(self, query):
        lo, hi = upgrade.seconds(query.start), upgrade.seconds(query.end)
        return [t for t in self.frames if lo <= upgrade.seconds(t) <= hi]

    def search(self, query, *, settings=None):
        self.searches.append(query)
        if query.wavelength in self.broken:
            raise ConnectionError("the archive did not answer")
        times = self._in(query)
        return {"query": query.to_json(), "total": len(times),
                "providers": [{"client": "FakeClient", "count": len(times), "times": times}]}

    def fetch(self, query, staging, *, settings, phase, skip=None):
        self.fetches.append(query)
        phase("searching")
        todo = self._in(query)
        kept = [t for t in todo if not (skip and skip(t))]
        phase("fetching", 0, len(kept))
        paths = []
        for t in kept:
            p = staging / f"frame_{t.replace(':', '').replace('.', '_')}.fits"
            if not p.exists():
                write_fits(p, aia_header(date=t, wave=query.wavelength or 193))
            paths.append(p)
        return {"found": len(kept), "paths": paths, "errors": [], "urls": {},
                "skipped": len(todo) - len(kept)}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(latest, "now_seconds", lambda: upgrade.seconds(NOW))


def _fill(lib, src, times=LIB_TIMES, wave=193, prefix="lib"):
    for i, t in enumerate(times):
        lib.ingest(write_fits(src / f"{prefix}{i}.fits", aia_header(date=t, wave=wave)))


@pytest.fixture
def server(lib):
    return Server(lib, out=io.StringIO())


def _call(server, archive, **params):
    server.jobs._modules["fido"] = archive
    return server.methods["template.latest"](**params)


# ---- the method ------------------------------------------------------------------

def test_archive_newer_than_the_library_queues_one_job_for_the_span(server, lib, src):
    _fill(lib, src)
    archive = Archive(ARCHIVE_TIMES)
    (entry,) = _call(server, archive, datasets=[AIA])
    assert entry["status"] == "queued" and entry["dataset_key"] == AIA
    assert entry["dataset_uid"] == lib.dataset(dataset_key=AIA)["dataset_uid"]
    assert entry["newest_local"] == LIB_TIMES[-1] and entry["newest_remote"] == NEWEST
    assert (entry["start"], entry["end"]) == ("2025-09-21T15:00:00.840", NEWEST)
    # The archive was searched over the lookback window ending now.
    (searched,) = archive.searches
    assert (searched.start, searched.end) == ("2025-09-18T18:00:00.000", NOW)
    (job,) = jobs.list_jobs(lib)
    assert job["job_id"] == entry["job_id"] and job["status"] == "queued"
    assert (job["query"]["start"], job["query"]["end"]) == (entry["start"], NEWEST)
    assert job["options"] == {"dataset_key": AIA, "missing_only": True, "tolerance_s": 12.0}
    (done,) = server.jobs.run_pending()
    assert done["status"] == "done" and done["result"]["skipped"] == 4  # the library's own frames
    assert lib.current_files(dataset_key=AIA)[-1]["date_obs"] == NEWEST


def test_same_newest_frame_is_up_to_date_and_queues_nothing(server, lib, src):
    _fill(lib, src)
    (entry,) = _call(server, Archive(LIB_TIMES), datasets=[AIA])
    assert entry["status"] == "up_to_date" and entry["job_id"] is None
    assert entry["newest_local"] == entry["newest_remote"] == LIB_TIMES[-1]
    assert jobs.list_jobs(lib) == []


def test_a_dataset_without_a_query_is_no_query_not_an_error(server, lib, src):
    _fill(lib, src)
    lib.ingest(write_fits(src / "bare.fits", {"DATE-OBS": "2025-09-21T16:00:00", "TIMESYS": "UTC"}))
    archive = Archive(ARCHIVE_TIMES)
    bare, aia = _call(server, archive, datasets=[UNKNOWN, AIA])
    assert bare["status"] == "no_query" and bare["dataset_key"] == UNKNOWN
    assert bare["newest_remote"] is None and bare["job_id"] is None
    assert bare["newest_local"] == "2025-09-21T16:00:00.000"
    assert aia["status"] == "queued"
    assert len(archive.searches) == 1 and len(jobs.list_jobs(lib)) == 1


def test_an_archive_error_is_reported_and_the_other_datasets_go_on(server, lib, src):
    _fill(lib, src)
    _fill(lib, src, wave=171, prefix="b")
    broken, aia = _call(server, Archive(ARCHIVE_TIMES, broken={171.0}), datasets=[AIA171, AIA])
    assert broken["status"] == "archive_error" and "did not answer" in broken["error"]
    assert broken["newest_local"] == LIB_TIMES[-1] and broken["job_id"] is None
    assert aia["status"] == "queued" and "error" not in aia
    assert [j["options"]["dataset_key"] for j in jobs.list_jobs(lib)] == [AIA]


def test_an_unknown_dataset_is_not_found_and_the_call_goes_on(server, lib, src):
    _fill(lib, src)
    uid = lib.dataset(dataset_key=AIA)["dataset_uid"]
    missing, by_uid = _call(server, Archive(ARCHIVE_TIMES), datasets=["no/such/dataset", uid])
    assert missing == {"dataset_uid": None, "dataset_key": None, "requested": "no/such/dataset",
                       "newest_local": None, "newest_remote": None, "job_id": None,
                       "start": None, "end": None, "status": "not_found"}
    assert by_uid["dataset_key"] == AIA and by_uid["status"] == "queued"


def test_dry_run_reports_the_window_and_queues_nothing(server, lib, src):
    _fill(lib, src)
    (entry,) = _call(server, Archive(ARCHIVE_TIMES), datasets=[AIA], span_s=600, dry_run=True)
    assert entry["status"] == "would_queue" and entry["job_id"] is None
    assert (entry["start"], entry["end"]) == ("2025-09-21T16:50:00.840", NEWEST)
    assert jobs.list_jobs(lib) == []


def test_a_short_lookback_that_finds_nothing_is_up_to_date(server, lib, src):
    _fill(lib, src)
    (entry,) = _call(server, Archive(ARCHIVE_TIMES), datasets=[AIA], lookback_s=600)
    assert entry["status"] == "up_to_date" and entry["newest_remote"] is None


def test_it_answers_on_the_background_lane(server, lib, src):
    assert "template.latest" in rpc.BACKGROUND
    _fill(lib, src)
    server.jobs._modules["fido"] = Archive(LIB_TIMES)
    pending, replies = [], []
    line = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "template.latest",
                       "params": {"datasets": [AIA]}})
    assert server.handle_line(line, reply=replies.append, pending=pending) is None
    for f in pending:
        f.result(timeout=10)
    assert replies[0]["id"] == 7 and replies[0]["result"][0]["status"] == "up_to_date"


# ---- the command line --------------------------------------------------------------

def run(capsys, *args):
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def released(lib, src, monkeypatch):
    """A library holding the AIA frames, its lock released so a command can open it,
    with the fido source faked."""
    _fill(lib, src)
    root = lib.root
    with lib:
        pass
    archive = Archive(ARCHIVE_TIMES)
    real = sources.get
    monkeypatch.setattr(sources, "get", lambda name: archive if name == "fido" else real(name))
    return root, archive


def test_cli_latest_dry_run_prints_a_table_and_json(released, capsys):
    root, _ = released
    code, out, _ = run(capsys, "--library", str(root), "latest", AIA, "no/such", "--span", "10m", "--dry-run")
    assert code == 1  # a dataset was not found: the run reported a problem
    assert out.splitlines()[0].split()[:2] == ["Status", "Dataset"]
    assert "would_queue" in out and "not_found" in out and NEWEST in out
    code, out, _ = run(capsys, "--library", str(root), "--json", "latest", AIA, "--lookback", "1d", "--dry-run")
    assert code == 0
    (entry,) = json.loads(out)
    assert entry["start"] == "2025-09-21T15:00:00.840"  # the default span, 2 h
    assert entry["status"] == "would_queue"
    with Library.open(root, readonly=True) as again:
        assert jobs.list_jobs(again) == []


def test_cli_latest_runs_the_job(released, capsys):
    root, archive = released
    code, out, err = run(capsys, "--library", str(root), "--json", "latest", AIA)
    assert code == 0
    (entry,) = json.loads(out)
    assert entry["status"] == "queued" and entry["job_id"] == 1
    assert "job 1" in err
    with Library.open(root, readonly=True) as again:
        assert again.current_files(dataset_key=AIA)[-1]["date_obs"] == NEWEST
        assert jobs.list_jobs(again)[0]["status"] == "done"


def test_cli_rejects_a_duration_it_cannot_read(released, capsys):
    root, _ = released
    with pytest.raises(SystemExit) as exit_:
        cli.main(["--library", str(root), "latest", AIA, "--span", "soon"])
    assert exit_.value.code == 2


def _session(tmp_path, root) -> Path:
    text = (FIXTURES / "studio-session-heliad-and-helioviewer.jhv").read_text(encoding="utf-8")
    path = tmp_path / "session.jhv"
    path.write_text(text.replace("@LIBRARY@", Path(root).resolve().as_posix()), encoding="utf-8")
    return path


def test_template_refresh_reads_a_studio_session(released, tmp_path, capsys):
    root, archive = released
    session = _session(tmp_path, root)
    code, out, _ = run(capsys, "--library", str(root), "--json", "template", "refresh", str(session), "--dry-run")
    assert code == 0
    heliad_layer, helioviewer_layer = json.loads(out)
    assert heliad_layer["layer"] == "SDO AIA 193" and heliad_layer["dataset_key"] == AIA
    assert heliad_layer["status"] == "would_queue" and heliad_layer["newest_remote"] == NEWEST
    assert helioviewer_layer == {"layer": "AIA 171 (Helioviewer)", "status": "skipped",
                                 "reason": "not a library dataset"}
    code, out, _ = run(capsys, "--library", str(root), "template", "refresh", str(session), "--dry-run")
    assert code == 0 and out.splitlines()[0].split()[:2] == ["Layer", "Status"]
    assert "skipped (not a library dataset)" in out and "would_queue" in out
    with Library.open(root, readonly=True) as again:
        assert jobs.list_jobs(again) == []

    code, out, _ = run(capsys, "--library", str(root), "--json", "template", "refresh", str(session))
    assert code == 0 and json.loads(out)[0]["status"] == "queued"
    with Library.open(root, readonly=True) as again:
        assert again.current_files(dataset_key=AIA)[-1]["date_obs"] == NEWEST


def test_template_refresh_of_a_file_that_is_not_a_session_is_a_usage_error(released, tmp_path, capsys):
    root, _ = released
    bad = tmp_path / "notes.jhv"
    bad.write_text('{"something": "else"}', encoding="utf-8")
    code, _, err = run(capsys, "--library", str(root), "template", "refresh", str(bad))
    assert code == 2 and "org.helioviewer.jhv.state" in err
    code, _, err = run(capsys, "--library", str(root), "template", "refresh", str(tmp_path / "gone.jhv"))
    assert code == 2


# ---- template.refresh: the same, for a session Studio sends ------------------------

def test_template_refresh_method_takes_the_session_document(server, lib, src, tmp_path):
    assert "template.refresh" in rpc.BACKGROUND
    _fill(lib, src)
    server.jobs._modules["fido"] = Archive(ARCHIVE_TIMES)
    document = json.loads(_session(tmp_path, lib.root).read_text(encoding="utf-8"))
    heliad_layer, helioviewer_layer = server.methods["template.refresh"](session=document, dry_run=True)
    assert heliad_layer["layer"] == "SDO AIA 193" and heliad_layer["status"] == "would_queue"
    assert helioviewer_layer["status"] == "skipped"
    assert "template.refresh" in rpc.describe()


def test_template_refresh_method_rejects_a_document_that_is_not_a_session(server):
    with pytest.raises(rpc.RpcError) as err:
        server.methods["template.refresh"](session={"something": "else"})
    assert err.value.code == rpc.INVALID_PARAMS
