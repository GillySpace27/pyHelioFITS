"""Replay gate: the golden set (tests/golden/) must keep reproducing the same pixel digests.

Offline layer (runs everywhere): each golden record's stored digest, the digest of its picture and the
digest in tests/golden/manifest.json must agree, and reproduce() driven by a stand-in Studio that
re-renders the picture must say ``identical``. A one-level change must say ``different``. This fails
when the hfpix/1 digest, the PNG reader or the verdict logic drifts.

Live layer (opt-in, needs a real Studio): set HFS_REPLAY_LAUNCH to the launch command, for example
``java -Duser.home={home} -jar HFStudio.jar``, and every golden record is replayed in an isolated
Studio. The synthetic pictures carry no scene Studio can render, so the live layer replays the
records listed in HFS_REPLAY_RECORDS (colon-separated .hfrec paths) instead; with none it is skipped.
On a GPU other than the one that made the records, set HFS_REPLAY_TOLERANCE (a profile name such as
cross-gpu-provisional, or a measured profile JSON file) and ``equivalent`` passes the gate too.

Tolerance layer (runs everywhere): rounding noise and a lone edge pixel must say ``equivalent`` under
the cross-GPU profile between two environments, a block shift or a spike must say ``different``, and
nothing is excused inside one environment.
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


def _pair(tmp_path, name, change):
    """The golden picture and a changed copy; ``change(arr, unit)`` edits in place, unit = one 8-bit level."""
    spec = CASES[name]
    arr = render(spec).astype(np.int64)
    top = 255 if spec["bit_depth"] == 8 else 65535
    change(arr, top // 255)
    arr = np.clip(arr, 0, top).astype(np.uint8 if spec["bit_depth"] == 8 else np.uint16)
    from patterns import write_png16
    out = tmp_path / f"{name}-other.png"
    (reproduce.write_png if spec["bit_depth"] == 8 else write_png16)(out, arr)
    return GOLD / f"{name}.png", out


def _rounding(arr, unit):  # one sample in six one 8-bit level off, toward mid scale so nothing clips
    arr[::2, ::3] += np.where(arr[::2, ::3] > 128 * unit, -unit, unit)


def _edge(arr, unit):  # one pixel six levels off
    arr[3, 3] += np.where(arr[3, 3] > 128 * unit, -6 * unit, 6 * unit)


def _shift(arr, unit):  # a whole block three levels off: a real change, not noise
    arr[:16, :16] += np.where(arr[:16, :16] > 128 * unit, -3 * unit, 3 * unit)


def _spike(arr, unit):  # one pixel forty levels off
    arr[3, 3] += np.where(arr[3, 3] > 128 * unit, -40 * unit, 40 * unit)


GPU_A = {"gpu": {"vendor": "Apple", "renderer": "Apple M4 Max"}}
GPU_B = {"gpu": {"vendor": "NVIDIA Corporation", "renderer": "stand-in"}}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("change,verdict", [(_rounding, "equivalent"), (_edge, "equivalent"),
                                            (_shift, "different"), (_spike, "different")])
def test_cross_gpu_tolerance_gate(tmp_path, name, change, verdict):
    a, b = _pair(tmp_path, name, change)
    assert reproduce.compare(a, b).verdict == "different"
    r = reproduce.compare(a, b, tolerance="cross-gpu-provisional", original_environment=GPU_A, replay_environment=GPU_B)
    assert r.verdict == verdict, r.text()
    assert r.to_dict()["tolerance"]["basis"] == "assumed" and "tolerance_checks" in r.metrics
    if verdict == "equivalent":
        assert any("not measured" in x for x in r.reasons)
        # A hardware tolerance never excuses a difference inside one environment.
        same = reproduce.compare(a, b, tolerance="cross-gpu-provisional", original_environment=GPU_A, replay_environment=GPU_A)
        assert same.verdict == "different", same.text()


def test_measured_profile_comes_from_pairs_and_round_trips(tmp_path):
    a, b = _pair(tmp_path, "gradient8_rgb", _edge)
    t = reproduce.measure_tolerance([(a, b)], "apple-vs-standin", "stand-in pair", margin=2.0)
    assert t.basis == "measured" and t.max_levels == 12.0 and 0 < t.max_outlier_fraction < 0.01
    f = tmp_path / "profile.json"
    f.write_text(json.dumps(t.to_dict()))
    assert reproduce.resolve_tolerance(str(f)) == t
    r = reproduce.compare(a, b, tolerance=str(f))
    assert r.verdict == "equivalent" and not any("not measured" in x for x in r.reasons)
    assert reproduce.resolve_tolerance("2") == 2 and reproduce.compare(a, b, tolerance=6).verdict == "equivalent"
    (tmp_path / "spike").mkdir()
    assert reproduce.compare(*_pair(tmp_path / "spike", "gradient8_rgb", _spike), tolerance=t).verdict == "different"
    with pytest.raises(ValueError):
        reproduce.resolve_tolerance("no-such-profile")


@pytest.mark.skipif(not (os.environ.get("HFS_REPLAY_LAUNCH") and os.environ.get("HFS_REPLAY_RECORDS")),
                    reason="live replay needs HFS_REPLAY_LAUNCH and HFS_REPLAY_RECORDS")
def test_live_replay_of_real_records(tmp_path):
    proc, studio = reproduce.launch_studio(shlex.split(os.environ["HFS_REPLAY_LAUNCH"]), tmp_path / "home")
    try:
        tolerance = os.environ.get("HFS_REPLAY_TOLERANCE") or None  # levels, a profile name or a profile file
        verdicts = {rec: reproduce.reproduce(rec, tmp_path / Path(rec).stem, studio, tolerance=tolerance).verdict
                    for rec in os.environ["HFS_REPLAY_RECORDS"].split(os.pathsep)}
    finally:
        proc.terminate()
    assert all(v in (("identical", "equivalent") if tolerance else ("identical",)) for v in verdicts.values()), verdicts
