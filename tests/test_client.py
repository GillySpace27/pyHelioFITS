import pytest

from heliofits import CommandError, StudioNotRunning
from heliofits.studio import Studio, snake
from heliofits.testing import FakeStudio


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
    with pytest.raises(StudioNotRunning, match="Scripting API"):
        Studio().seek_frame(frame=0)


def test_cache_stats(tmp_path, monkeypatch):
    from heliofits import cache
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HFCORE_LIBRARY", str(tmp_path / "lib"))
    from heliofits_core.library import Library
    Library.open(tmp_path / "lib").close()
    assert cache.library_stats()["files"] == 0
    with pytest.raises(CommandError):
        cache.call("nope.nope")
