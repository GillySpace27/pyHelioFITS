"""The cache editor's verbs on the command line: reject, unreject, rejected, refetch,
cadences, upgrade, coverage and checks print text for a person, the JSON-RPC result with
--json, and exit by the documented codes. Archives are faked; nothing here reaches one."""

import json

import pytest

from heliofits_core import cli, sources
from tests.test_upgrade import AIA, ARCHIVE_TIMES, LIB_TIMES, Archive, _fill


@pytest.fixture
def filled(lib, src):
    obs = _fill(lib, src)
    with lib:
        pass  # release the root lock so writing commands can take it
    return lib, obs


def run(capsys, lib, *args):
    code = cli.main(["--library", str(lib.root), *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_reject_and_unreject_print_what_changed(filled, capsys):
    lib, obs = filled
    bad = obs[LIB_TIMES[1]]
    code, out, _ = run(capsys, lib, "reject", str(bad), "--reason", "smeared by a flare")
    assert code == 0 and out.strip() == f"Rejected 1 observations: {bad}"
    code, out, _ = run(capsys, lib, "rejected")
    assert code == 0
    assert out.splitlines()[0].split()[:2] == ["Observation", "Observed"]
    assert "smeared by a flare" in out and LIB_TIMES[1] in out
    code, out, _ = run(capsys, lib, "--json", "rejected", "--dataset", AIA)
    assert code == 0 and [r["observation_id"] for r in json.loads(out)] == [bad]
    code, out, _ = run(capsys, lib, "files")
    assert LIB_TIMES[1] not in out
    code, out, _ = run(capsys, lib, "unreject", str(bad))
    assert code == 0 and out.strip() == f"Unrejected 1 observations: {bad}"
    code, out, _ = run(capsys, lib, "rejected")
    assert code == 0 and out.strip() == "Nothing rejected."


def test_reject_needs_a_reason_and_known_observations(filled, capsys):
    lib, obs = filled
    with pytest.raises(SystemExit) as exit_:
        cli.main(["--library", str(lib.root), "reject", "1"])
    assert exit_.value.code == 2  # argparse: --reason is required
    capsys.readouterr()
    code, out, err = run(capsys, lib, "reject", "999", "--reason", "x")
    assert code == 2 and out == "" and "no observation [999]" in err


def test_checks_and_coverage_read_the_library(filled, capsys):
    lib, obs = filled
    code, out, _ = run(capsys, lib, "checks", "1")
    assert code == 0 and (out.strip() == "No checks recorded for this file."
                          or out.splitlines()[0].startswith("Severity"))
    code, out, _ = run(capsys, lib, "coverage", "--start", "2025-09-21T16:00:00", "--end", "2025-09-21T16:02:00",
                       "--bins", "4")
    assert code == 0
    assert out.splitlines()[0] == "2025-09-21T16:00:00.000 to 2025-09-21T16:02:00.000 UTC in 4 bins of 30 s"
    assert "Frames" in out.splitlines()[1] and "SDO AIA 193" in out
    assert "█" in out  # the per-bin sparkline
    code, out, _ = run(capsys, lib, "--json", "coverage", "--bins", "2")
    r = json.loads(out)
    assert code == 0 and r["bins"] == 2 and r["datasets"][0]["frames"] == 4
    code, out, err = run(capsys, lib, "coverage", "--bins", "0")
    assert code == 2 and "bins must be" in err


def test_cadences_compares_with_the_archive(filled, capsys, monkeypatch):
    lib, obs = filled
    monkeypatch.setattr(sources, "get", lambda name: Archive(ARCHIVE_TIMES))
    code, out, _ = run(capsys, lib, "cadences", AIA)
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith(f"Dataset {AIA} via fido: {LIB_TIMES[0]} to {LIB_TIMES[-1]} UTC")
    assert lines[1] == "Library: 4 observations, every 24 s"
    assert lines[3].startswith("Missing from the library (within 6 s): 3")
    assert "  2025-09-21T16:00:12.000" in lines
    code, out, _ = run(capsys, lib, "--json", "cadences", AIA)
    assert code == 0 and len(json.loads(out)["missing"]) == 3


def test_an_unreachable_archive_is_exit_1(filled, capsys, monkeypatch):
    lib, obs = filled

    class Down:
        def search(self, query, *, settings=None):
            raise sources.ArchiveError("archive search failed: ConnectionError: no route")
    monkeypatch.setattr(sources, "get", lambda name: Down())
    code, out, err = run(capsys, lib, "cadences", AIA)
    assert code == 1 and out == "" and "no route" in err
    code, out, err = run(capsys, lib, "cadences", "nothing/here")
    assert code == 2 and "no dataset 'nothing/here'" in err


def test_upgrade_is_a_plan_until_yes(filled, capsys, monkeypatch):
    lib, obs = filled
    archive = Archive(ARCHIVE_TIMES)
    monkeypatch.setattr(sources, "get", lambda name: archive)
    code, out, err = run(capsys, lib, "upgrade", AIA, "--sample", "12")
    assert code == 0 and out.startswith(f"Would ask fido for {AIA}:") and "sample_s=12" in out
    assert "--yes" in err and archive.queries == []
    code, out, _ = run(capsys, lib, "--json", "upgrade", AIA, "--sample", "12")
    plan = json.loads(out)
    assert plan["dry_run"] is True and plan["options"]["tolerance_s"] == 6.0
    code, out, err = run(capsys, lib, "upgrade", AIA, "--sample", "12", "--yes")
    assert code == 0 and "job 1 queued" in err
    assert out.splitlines()[0].split()[:2] == ["Job", "Status"] and "stored 3 of 3" in out
    code, out, _ = run(capsys, lib, "--json", "files")
    assert len(json.loads(out)) == 7


def test_refetch_runs_one_frame_to_the_end(filled, capsys, monkeypatch):
    lib, obs = filled
    archive = Archive([LIB_TIMES[1]])
    monkeypatch.setattr(sources, "get", lambda name: archive)
    code, out, err = run(capsys, lib, "--json", "refetch", str(obs[LIB_TIMES[1]]))
    assert code == 0
    (job,) = json.loads(out)
    assert job["status"] == "done" and job["options"]["observation_id"] == obs[LIB_TIMES[1]]
    assert job["result"]["ingest"] == {"duplicate": 1}
    code, out, err = run(capsys, lib, "refetch", "999")
    assert code == 2 and "no observation 999" in err
