"""Replay gate: the golden set (tests/golden/) must keep reproducing the same pixel digests.

Offline layer (runs everywhere): each golden record's stored digest, the digest of its picture and the
digest in tests/golden/manifest.json must agree, and reproduce() driven by a stand-in Studio that
re-renders the picture must say ``identical``. A one-level change must say ``different``. This fails
when the hfpix/1 digest, the PNG reader or the verdict logic drifts.

Live layer (opt-in, needs a real Studio): set HFS_REPLAY_LAUNCH to the launch command, for example
``java -Duser.home={home} -jar HFStudio.jar``, and every golden record is replayed in an isolated
Studio. The synthetic pictures carry no scene Studio can render, so the live layer replays the
records listed in HFS_REPLAY_RECORDS (colon-separated .hfrec paths) instead; with none it is skipped.
"""
import json
import os
import shlex
import sys
from pathlib import Path

import numpy as np
import pytest

from pyheliofits import reproduce
from pyheliofits.core import record as hfrecord

GOLD = Path(__file__).resolve().parent / "golden"
sys.path.insert(0, str(GOLD))
from patterns import CASES, render, write  # noqa: E402

MANIFEST = json.loads((GOLD / "manifest.json").read_text("utf-8"))


def test_every_case_is_in_the_manifest_and_on_disk():
    assert sorted(MANIFEST) == sorted(CASES)
    for name in CASES:
        assert (GOLD / f"{name}.png").is_file() and (GOLD / f"{name}.png.hfrec").is_file()


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_digest_is_stable(name):
    expected = MANIFEST[name]["pixel_sha256"]
    rec = json.loads((GOLD / f"{name}.png.hfrec").read_text("utf-8"))
    assert hfrecord.validate(rec) == []
    assert rec["output"]["pixel_sha256"] == expected
    assert reproduce.digests(GOLD / f"{name}.png")["pixel_sha256"] == expected


def _fake_studio(tmp_path, spec, nudge=False):
    from pyheliofits.studio import Studio
    from pyheliofits.testing import FakeStudio

    def still(a):
        arr = render(spec)
        if nudge:
            arr = arr.copy()
            arr[0, 0, 0] = arr[0, 0, 0] ^ 1
            if spec["bit_depth"] == 8:
                reproduce.write_png(a["path"], arr)
            else:
                from patterns import write_png16
                write_png16(Path(a["path"]), arr)
        else:
            write(Path(a["path"]), spec)
        return {"path": a["path"]}

    fake = FakeStudio(handlers={"sessionLoad": lambda a: {"path": a["path"], "notes": []}, "exportStill": still})
    from test_reproduce import SESSION_LOAD
    fake.tools["sessionLoad"] = SESSION_LOAD
    return fake, Studio


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("nudge", [False, True], ids=["replays", "one-level-drift"])
def test_replay_gate_passes_and_can_fail(tmp_path, name, nudge):
    for suffix in (".png", ".png.hfrec"):
        (tmp_path / f"{name}{suffix}").write_bytes((GOLD / f"{name}{suffix}").read_bytes())
    fake, Studio = _fake_studio(tmp_path, CASES[name], nudge=nudge)
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api.json").read_text()))
        r = reproduce.reproduce(tmp_path / f"{name}.png.hfrec", tmp_path / "replay", studio)
    assert r.verdict == ("different" if nudge else "identical"), r.text()


@pytest.mark.skipif(not (os.environ.get("HFS_REPLAY_LAUNCH") and os.environ.get("HFS_REPLAY_RECORDS")),
                    reason="live replay needs HFS_REPLAY_LAUNCH and HFS_REPLAY_RECORDS")
def test_live_replay_of_real_records(tmp_path):
    proc, studio = reproduce.launch_studio(shlex.split(os.environ["HFS_REPLAY_LAUNCH"]), tmp_path / "home")
    try:
        verdicts = {rec: reproduce.reproduce(rec, tmp_path / Path(rec).stem, studio).verdict
                    for rec in os.environ["HFS_REPLAY_RECORDS"].split(os.pathsep)}
    finally:
        proc.terminate()
    assert all(v == "identical" for v in verdicts.values()), verdicts
