import json
import subprocess
import sys
from pathlib import Path

from pyheliofits.core import record as hfrecord
from pyheliofits.core import report

FIXTURE = Path(__file__).parent / "fixtures" / "example-studio-connect-still.hfrec.json"
SRC = Path(__file__).resolve().parents[1] / "src"


def _record():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_text_report_covers_the_file_the_scene_and_every_layer():
    out = report.text(_record())
    assert out.startswith("HELIOFITS SUITE PROCESSING RECORD")
    for needle in ("LAYERS (3, top layer first)", "PUNCH WFI+NFI Mosaic 530", "Proba-3 ASPIICS", "SDO AIA 193",
                   "RHEF", "PLEASE CITE", "CAVEATS", "not a calibrated radiance"):
        assert needle in out, needle
    assert "\u2014" not in out  # no em dash


def test_every_step_of_every_layer_is_listed_with_its_parameters():
    rec = _record()
    out = report.text(rec)
    for layer in rec["layers"]:
        for step in layer["steps"]:
            assert step["op"] in out


def test_a_long_file_list_is_reduced_unless_full():
    rec = _record()
    src = dict(rec["layers"][0]["sources"][0])
    rec["layers"][0]["sources"] = [{**src, "original_name": f"f{i:04d}.fits", "sha256": f"{i:064x}"} for i in range(400)]
    short, full = report.text(rec), report.text(rec, full=True)
    assert "400 files, first f0000.fits, last f0399.fits" in short and "f0200.fits" not in short
    assert "f0200.fits" in full
    assert len(short.splitlines()) < 150


def test_methods_tex_and_python():
    rec = _record()
    m = report.methods(rec)
    assert "RHEF" in m["paragraph"] and m["citations"]
    t = report.tex(rec)
    assert "\\begin{itemize}" in t and "Gilly & Cranmer" not in t and "Gilly \\& Cranmer" in t
    py = report.python(rec)
    compile(py, "script", "exec")
    assert "import sunpy.map" in py


def test_read_gives_the_view_a_report_block(tmp_path):
    side = tmp_path / "x.png.hfrec.json"
    side.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "x.png").write_bytes(b"")
    found = hfrecord.read(side)
    assert found["view"]["report"][0] == "HELIOFITS SUITE PROCESSING RECORD"
    rec = _record()
    rec["report"] = ["written by the producer"]
    side.write_text(json.dumps(rec), encoding="utf-8")
    assert hfrecord.read(side)["view"]["report"] == ["written by the producer"]
    assert not hfrecord.validate(rec)


def test_cli_record_show(tmp_path):
    side = tmp_path / "x.hfrec.json"
    side.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    run = lambda *a: subprocess.run([sys.executable, "-m", "pyheliofits.core", "record", "show", str(side), *a],  # noqa: E731
                                    capture_output=True, text=True, env={"PYTHONPATH": str(SRC), "PATH": "",
                                         "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)})
    for fmt, needle in (("text", "LAYERS"), ("methods", "processed with"), ("tex", "itemize"), ("python", "sunpy")):
        r = run("--format", fmt)
        assert r.returncode == 0 and needle in r.stdout, (fmt, r.stderr)


def test_a_dot_hfrec_sidecar_is_found_beside_its_export(tmp_path):
    (tmp_path / "x.png").write_bytes(b"")
    (tmp_path / "x.png.hfrec").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    assert hfrecord.find(tmp_path / "x.png")["found_in"]["path"].endswith("x.png.hfrec")
    assert hfrecord.find(tmp_path / "x.png.hfrec")["record"]["hfrecord"]


def test_migrate_renames_only_with_yes_and_never_overwrites(tmp_path):
    a, b, c = (tmp_path / n for n in ("a.png.hfrec.json", "b.png.hfrec.json", "c.png.hfrec.json"))
    for f in (a, b):
        f.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    c.write_text("not json", encoding="utf-8")
    (tmp_path / "b.png.hfrec").write_text("taken", encoding="utf-8")
    plan = {Path(r["from"]).name: r["status"] for r in hfrecord.migrate_sidecars([tmp_path])}
    assert plan == {a.name: "would_rename", b.name: "exists", c.name: "not_a_record"}
    assert a.exists()
    done = {Path(r["from"]).name: r["status"] for r in hfrecord.migrate_sidecars([tmp_path], apply=True)}
    assert done[a.name] == "renamed" and not a.exists() and (tmp_path / "a.png.hfrec").exists()
    assert (tmp_path / "b.png.hfrec").read_text() == "taken" and b.exists()
    (tmp_path / "a.png").write_bytes(b"")
    assert hfrecord.find(tmp_path / "a.png")["found_in"]["path"].endswith("a.png.hfrec")


def test_a_record_with_an_embedded_preview_validates_and_the_report_is_unchanged():
    import base64
    rec = _record()
    plain = report.text(rec)
    rec["preview"] = {"media_type": "image/png", "width": 1, "height": 1,
                      "data": base64.b64encode(b"\x89PNG").decode()}
    assert not hfrecord.validate(rec)
    assert report.text(rec) == plain
    rec["preview"]["media_type"] = "image/gif"
    assert hfrecord.validate(rec)
