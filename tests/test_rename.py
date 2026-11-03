"""HelioFITS Core was called Heliad until 2026-10-25, and was the `heliofits_core` package until
it became `heliofits.core` on 2026-11-02. Everything made under the old name
keeps working: the import name and `python -m heliad`, the HELIAD_ variables, the old config
folder, a library at ~/HeliadLibrary with its heliad.sqlite, heliad:// links, the
heliad.hello method and view folders marked .heliad-view. Nothing new is made with it."""

import io
import json
import subprocess
import sys

import pytest

import heliofits.core
from heliofits.core import config, query, views
from heliofits.core.library import Library
from heliofits.core.rpc import Server
from conftest import aia_header, write_fits


@pytest.mark.parametrize("old", ["heliad", "heliofits_core"])
def test_the_old_import_names_are_the_same_package(old):
    import importlib
    pkg = importlib.import_module(old)
    cli = importlib.import_module(old + ".cli")
    Query = importlib.import_module(old + ".query").Query
    assert pkg is heliofits.core
    assert cli is heliofits.core.cli and Query is query.Query


@pytest.mark.parametrize("old", ["heliad", "heliofits_core", "heliofits.core"])
def test_python_m_old_names_still_run_the_command(old):
    out = subprocess.run([sys.executable, "-m", old, "--version"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.startswith("hfcore ")


def test_the_new_variables_win_and_the_old_ones_still_work(tmp_path, monkeypatch):
    monkeypatch.delenv("HFCORE_HOME", raising=False)
    monkeypatch.setenv("HELIAD_HOME", str(tmp_path / "old"))
    assert config.app_support_dir() == tmp_path / "old"
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "new"))
    assert config.app_support_dir() == tmp_path / "new"
    monkeypatch.setenv("HELIAD_JSOC_EMAIL", "old@example.org")
    assert config.load_settings()["jsoc_email"] == "old@example.org"
    monkeypatch.setenv("HFCORE_JSOC_EMAIL", "new@example.org")
    assert config.load_settings()["jsoc_email"] == "new@example.org"


def _linux_home(tmp_path, monkeypatch):
    for name in ("HFCORE_HOME", "HELIAD_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path / "home"))


def test_a_new_computer_gets_the_new_folders(tmp_path, monkeypatch):
    _linux_home(tmp_path, monkeypatch)
    assert config.app_support_dir() == tmp_path / "xdg" / "hfcore"
    assert config.default_library_root() == tmp_path / "home" / "HelioFITSLibrary"
    assert config.Config(tmp_path / "lib").db_path.name == "hfcore.sqlite"


def test_a_computer_set_up_before_the_rename_keeps_its_folders(tmp_path, monkeypatch):
    _linux_home(tmp_path, monkeypatch)
    old = tmp_path / "xdg" / "heliad"
    old.mkdir(parents=True)
    (old / "config.json").write_text("{}")
    assert config.app_support_dir() == old
    legacy = tmp_path / "home" / "HeliadLibrary"
    legacy.mkdir(parents=True)
    (legacy / "heliad.sqlite").write_bytes(b"")
    assert config.default_library_root() == legacy
    assert config.Config(legacy).db_path == legacy / "heliad.sqlite"
    new = tmp_path / "xdg" / "hfcore"
    new.mkdir()
    (new / "config.json").write_text("{}")
    assert config.app_support_dir() == new  # once the new folder has a config, it is the one


def test_a_library_made_before_the_rename_opens_and_keeps_its_index_name(tmp_path, monkeypatch, src):
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "appsupport"))
    root = tmp_path / "old-library"
    with Library.open(root) as lib:
        lib.ingest(write_fits(src / "a.fits", aia_header()))
    (root / "hfcore.sqlite").rename(root / "heliad.sqlite")
    for suffix in ("-wal", "-shm"):
        if (root / f"hfcore.sqlite{suffix}").exists():
            (root / f"hfcore.sqlite{suffix}").rename(root / f"heliad.sqlite{suffix}")
    with Library.open(root) as lib:
        assert lib.stats()["files"] == 1
    assert not (root / "hfcore.sqlite").exists()


def test_old_links_and_the_old_hello_still_answer(lib):
    server = Server(lib, out=io.StringIO())
    action, q = query.Query.from_url("heliad://search?start=a&end=b&instrument=AIA")
    assert action == "search" and q.instrument == "AIA"
    assert q.to_url().startswith("hfcore://search?")
    assert query.parse_dataset_link("heliad://dataset/abc")["dataset_uid"] == "abc"
    old = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "heliad.hello"}))
    new = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "core.hello"}))
    assert old["result"] == new["result"] and new["result"]["name"] == "heliofits-core"
    assert new["result"]["aliases"]["heliad.hello"] == "core.hello"


def test_a_view_folder_built_before_the_rename_is_still_ours(lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header()))
    views.create(lib, "ev", start="2025-09-21T15:00:00", end="2025-09-21T17:00:00")
    folder = views.materialize(lib, "ev")["path"]
    from pathlib import Path
    folder = Path(folder)
    (folder / views.MARKER).rename(folder / views.LEGACY_MARKER)
    assert [v for v in views.list_views(lib) if v["name"] == "ev"][0]["materialized"]
    views.materialize(lib, "ev")  # replaces it rather than refusing a folder it did not make
    assert (folder / views.MARKER).exists()


def test_a_mac_set_up_before_the_rename_keeps_its_heliad_folder(tmp_path, monkeypatch):
    for name in ("HFCORE_HOME", "HELIAD_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    support = tmp_path / "home" / "Library" / "Application Support"
    old = support / "Heliad"
    old.mkdir(parents=True)
    (old / "config.json").write_text("{}")
    assert config.app_support_dir() == old
    new = support / "HelioFITS Core"
    new.mkdir()
    (new / "config.json").write_text("{}")
    assert config.app_support_dir() == new  # once the new folder has a config, it is the one
