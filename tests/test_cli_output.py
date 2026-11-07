"""The command line prints text for a person, the JSON-RPC result with --json, and exits by the
documented codes: 0 done, 1 a problem the operation reported, 2 usage, 3 locked."""

import json

import pytest

from pyheliofits.core import cli, config
from pyheliofits.core.library import Library
from tests.conftest import aia_header, write_fits


@pytest.fixture
def filled(lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header()))
    return lib


def run(capsys, *args):
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_json_is_the_rpc_result_and_text_is_for_a_person(filled, capsys):
    code, out, _ = run(capsys, "--library", str(filled.root), "--json", "stats")
    assert code == 0
    assert json.loads(out)["files"] == 1
    code, out, _ = run(capsys, "--library", str(filled.root), "stats")
    assert code == 0 and "Files: 1" in out and not out.startswith("{")


def test_tables_name_their_columns(filled, capsys):
    _, out, _ = run(capsys, "--library", str(filled.root), "datasets")
    assert out.splitlines()[0].split()[:2] == ["Obs", "First"]
    assert "SDO AIA 193" in out
    _, out, _ = run(capsys, "--library", str(filled.root), "files")
    assert "Observed (UTC)" in out and "store/" in out
    _, out, _ = run(capsys, "--library", str(filled.root), "versions", "1")
    assert "current" in out
    _, out, _ = run(capsys, "--library", str(filled.root), "collisions")
    assert out.strip() == "No collisions."
    _, out, _ = run(capsys, "--library", str(filled.root), "jobs")
    assert out.strip() == "No jobs."


def test_verify_reports_a_problem_with_exit_1(filled, capsys):
    code, out, _ = run(capsys, "--library", str(filled.root), "verify")
    assert code == 0 and out.startswith("Verified 1 files; 0 missing, 0 altered.")
    relpath = filled.current_files()[0]["relpath"]
    gone = filled.abspath(relpath)
    gone.chmod(0o600)  # stored files are read-only, and Windows will not unlink those
    gone.unlink()
    code, out, _ = run(capsys, "--library", str(filled.root), "verify")
    assert code == 1 and "1 missing" in out and relpath in out
    code, out, _ = run(capsys, "--library", str(filled.root), "--json", "verify")
    assert code == 1 and json.loads(out)["missing"] == [relpath]


def test_no_library_is_a_usage_error(tmp_path, capsys):
    code, out, err = run(capsys, "--library", str(tmp_path / "nowhere"), "stats")
    assert code == 2 and out == "" and "no HelioFITS Core library" in err


def test_a_locked_library_is_exit_3(filled, capsys):
    # The fixture's Library holds the root lock; a writing command cannot take it.
    code, out, err = run(capsys, "--library", str(filled.root), "reindex", "--dry-run")
    assert code == 3 and "another HelioFITS Core process" in err


def test_delete_dry_run_says_what_would_go(filled, capsys):
    with filled:
        pass  # release the lock so the delete can open the library for writing
    code, out, err = run(capsys, "--library", str(filled.root), "delete", "--file", "1")
    assert code == 0 and out.startswith("Would remove 1 files") and "--yes" in err
    with Library.open(filled.root, readonly=True) as again:
        assert again.stats()["files"] == 1


def test_settings_print_as_lines_and_as_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "home"))
    config.save_setting("vso_mirrors", "NSO")
    _, out, _ = run(capsys, "settings")
    assert out.strip() == "vso_mirrors = NSO"
    _, out, _ = run(capsys, "settings", "vso_mirrors")
    assert out.strip() == "NSO"
    _, out, _ = run(capsys, "--json", "settings")
    assert json.loads(out) == {"vso_mirrors": "NSO"}


def test_version_is_the_installed_one(capsys):
    from pyheliofits.core import __version__
    with pytest.raises(SystemExit) as exit_:
        cli.main(["--version"])
    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"hfcore {__version__}"
