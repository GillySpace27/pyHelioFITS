"""The notebooks run, against the fake Studio (synthetic frames), and their .ipynb files match the
.py sources they are built from."""

import csv
import json
import runpy
import sys
from pathlib import Path

import pytest

from pyheliofits import cache, studio
from pyheliofits.testing import FakeStudio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pct2ipynb  # noqa: E402

NOTEBOOKS = sorted((ROOT / "notebooks").glob("*.py"))


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(studio, "_default", None)
    with FakeStudio() as f:
        monkeypatch.setenv("HFSTUDIO_API", str(f.endpoint_file(tmp_path / "api.json")))
        yield f


def run(name):
    runpy.run_path(str(ROOT / "notebooks" / name), run_name="notebook")


@pytest.mark.parametrize("src", NOTEBOOKS, ids=lambda p: p.stem)
def test_ipynb_matches_source(src):
    built = pct2ipynb.notebook(src.read_text("utf-8"))
    assert json.loads(src.with_suffix(".ipynb").read_text("utf-8")) == built
    assert any(c["cell_type"] == "code" for c in built["cells"])


def test_load_and_step(fake, capsys):
    run("01_load_and_step.py")
    assert "(5, 5, " in capsys.readouterr().out
    assert ("seekTime", {"time": "2025-09-21T22:35:47"}) in fake.calls


def test_sweep_writes_a_png_per_lambda(fake, tmp_path):
    run("02_filter_and_projection_sweep.py")
    assert sorted(p.name for p in (tmp_path / "hfs-out/sweep").glob("*.png")) == [
        "lambda_+0.png", "lambda_+1.png", "lambda_-1.png"]
    assert [a["warp_lambda"] for n, a in fake.calls if n == "viewSet"] == [1, 0, -1]


def test_fill_a_gap(fake, monkeypatch, capsys):
    fake.layers[5] = {"layer_id": 5, "name": "x", "source": "fits", "filter": "none",
                      "times": ["2025-09-21T22:00:00.000", "2025-09-21T22:12:00.000"], "current": 0}
    seen = []

    def stub(method, _wait=True, **params):
        seen.append((method, params))
        return {"library.datasets": [{"key": "sdo/aia/-/-/1/193"}],
                "dataset.cadences": {"library": {"count": 1}, "archive": {"count": 3},
                                     "missing": ["2025-09-21T16:00:12.000"]},
                "dataset.upgrade": {"query": {"sample_s": 12}, "status": "done", "result": {"stored": 1}}}[method]

    monkeypatch.setattr(cache, "call", stub)
    run("03_fill_a_cadence_gap.py")
    assert ("dataset.upgrade", {"dataset_key": "sdo/aia/-/-/1/193", "dry_run": False}) in seen
    assert ("layerReload", {"layer_id": 5}) in fake.calls


def test_preview_then_export(fake, tmp_path):
    run("04_preview_then_export.py")
    assert (tmp_path / "hfs-out/exports/unrolled.png").read_bytes()[:4] == b"\x89PNG"
    assert (tmp_path / "hfs-out/exports/unrolled.mp4").exists()
    assert fake.calls.index(("viewSet", {"projection": "helioradialUnrolled"})) < \
        [n for n, _ in fake.calls].index("exportStill")


def test_cme_height_time_front_moves_out(fake, tmp_path):
    run("05_cme_height_time.py")
    rows = list(csv.DictReader(open(tmp_path / "hfs-out/cme/example_height_time.csv")))
    heights = [float(r["front_rsun"]) for r in rows]
    assert len(heights) == 5 and heights == sorted(heights) and heights[-1] > heights[0]
