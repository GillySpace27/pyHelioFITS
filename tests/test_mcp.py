import pytest

pytest.importorskip("mcp")
import anyio
import pytest
from mcp.client import Client

from heliofits import mcp as hmcp
from heliofits.studio import Studio
from heliofits.testing import FakeStudio


@pytest.fixture
def fake(tmp_path, monkeypatch):
    with FakeStudio() as f:
        monkeypatch.setenv("HFSTUDIO_API", str(f.endpoint_file(tmp_path / "api.json")))
        monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("HFCORE_LIBRARY", str(tmp_path / "lib"))
        from heliofits_core.library import Library
        Library.open(tmp_path / "lib").close()
        yield f


def test_tools_listed_and_flagged(fake):
    async def go():
        async with Client(hmcp.build(Studio())) as c:
            return {t.name: t for t in (await c.list_tools()).tools}
    tools = anyio.run(go)
    assert "studio_seekTime" in tools and "cache_library_stats" in tools
    assert "studio_newJp2Layer" not in tools  # ui commands hidden by default
    assert tools["studio_newSession"].annotations.destructive_hint
    assert tools["cache_library_delete"].annotations.destructive_hint
    assert tools["cache_library_stats"].annotations.read_only_hint
    assert "cache_heliad_hello" not in tools


def test_call_studio_and_cache(fake):
    async def go():
        async with Client(hmcp.build(Studio())) as c:
            a = await c.call_tool("studio_seekTime", {"time": "2024-05-10T12:00"})
            b = await c.call_tool("cache_library_stats", {})
            bad = await c.call_tool("studio_seekFrame", {"frame": -1})
            return a, b, bad
    a, b, bad = anyio.run(go)
    assert not a.is_error and fake.calls == [("seekTime", {"time": "2024-05-10T12:00"})]
    assert not b.is_error and b.structured_content["files"] == 0
    assert bad.is_error and "invalid-arguments" in bad.content[0].text


def test_read_only_hides_writers(fake):
    async def go():
        async with Client(hmcp.build(Studio(), read_only=True)) as c:
            return {t.name for t in (await c.list_tools()).tools}
    names = anyio.run(go)
    assert "cache_library_stats" in names and "studio_seekTime" not in names and "cache_library_delete" not in names


def test_studio_not_running_is_a_tool_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HFSTUDIO_API", str(tmp_path / "none.json"))
    async def go():
        async with Client(hmcp.build(Studio())) as c:
            return await c.call_tool("studio_seekFrame", {"frame": 0})
    r = anyio.run(go)
    assert r.is_error and "Scripting API" in r.content[0].text
