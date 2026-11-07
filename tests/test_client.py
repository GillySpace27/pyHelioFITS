import pytest

from pyheliofits import CommandError, StudioNotRunning
from pyheliofits.studio import Studio, snake
from pyheliofits.testing import FakeStudio


@pytest.fixture
def fake(tmp_path, monkeypatch):
    with FakeStudio(handlers={"listCommands": lambda a: {"commands": []}}) as f:
        monkeypatch.setenv("HFSTUDIO_API", str(f.endpoint_file(tmp_path / "api.json")))
        yield f


def test_snake():
    assert snake("seekTime") == "seek_time"


def test_call_both_spellings(fake):
    s = Studio()
    s.seek_time(time="2024-05-10T12:00")
    s.seekTime(time="2024-05-10T12:00")
    assert fake.calls == [("seekTime", {"time": "2024-05-10T12:00"})] * 2


def test_bad_args_checked_before_sending(fake):
    with pytest.raises(CommandError) as e:
        Studio().seek_frame(frame=-1)
    assert e.value.kind == "invalid-arguments" and fake.calls == []


def test_unknown_command(fake):
    with pytest.raises(CommandError) as e:
        Studio().nope()
    assert e.value.kind == "unknown-command"


def test_destructive_declined(tmp_path, monkeypatch):
    with FakeStudio(confirm=False) as f:
        monkeypatch.setenv("HFSTUDIO_API", str(f.endpoint_file(tmp_path / "api.json")))
        with pytest.raises(CommandError) as e:
            Studio().new_session()
        assert e.value.kind == "declined"


def test_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("HFSTUDIO_API", str(tmp_path / "missing.json"))
    with pytest.raises(StudioNotRunning, match="Local API"):
        Studio().seek_frame(frame=0)


def test_cache_stats(tmp_path, monkeypatch):
    from pyheliofits import cache
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HFCORE_LIBRARY", str(tmp_path / "lib"))
    from pyheliofits.core.library import Library
    Library.open(tmp_path / "lib").close()
    assert cache.library_stats()["files"] == 0
    with pytest.raises(CommandError):
        cache.call("nope.nope")


def test_cache_runs_a_queued_job_to_the_end(tmp_path, monkeypatch):
    from pyheliofits import cache
    from pyheliofits.core import jobs
    from pyheliofits.core.library import Library
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HFCORE_LIBRARY", str(tmp_path / "lib"))
    Library.open(tmp_path / "lib").close()
    ran = []
    monkeypatch.setattr(jobs.JobRunner, "enqueue", lambda self, q, source="fido", options=None:
                        {"job_id": 7, "status": "queued"})
    monkeypatch.setattr(jobs.JobRunner, "run_pending", lambda self: ran.append(1) or [])
    monkeypatch.setattr(jobs.JobRunner, "get", lambda self, i: {"job_id": i, "status": "done"})
    query = {"start": "2025-09-21T16:00:00", "end": "2025-09-21T17:00:00", "instrument": "AIA", "wavelength": 193.0}
    assert cache.download_enqueue(query=query) == {"job_id": 7, "status": "done"} and ran == [1]
    assert cache.call("download.enqueue", _wait=False, query=query)["status"] == "queued"
