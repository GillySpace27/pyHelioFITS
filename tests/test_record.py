"""Processing records (hfrecord 1.x): finding, checking, showing and resolving them.

The example record is the design folder's worked CONNECT still, copied unchanged into
tests/fixtures. PNG, EXR, FITS and movie containers are built here, small and synthetic.
"""

import copy
import hashlib
import io
import json
import struct
import subprocess
import zlib
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from heliofits.core import record
from heliofits.core.rpc import RECORD_UNSUPPORTED, Server
from conftest import aia_header, punch_header, write_fits

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "example-studio-connect-still.hfrec.json"
# sha256 of the design folder's hfrecord-1.schema.json (2026-10-22): the copy must stay byte for byte.
SCHEMA_SHA256 = "3eef3c1fc208c95deb0b9b48163fa17391c70041b012b68c0bb1b1f4d927b8e8"


def example() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))


def write_png(path: Path, texts: list[tuple[bytes, bytes, bytes]] = ()) -> Path:
    """A 1x1 grey PNG with the given (chunk type, keyword, payload) text chunks before IEND."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x80")
    data = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat)
    for ctype, key, payload in texts:
        data += _chunk(ctype, key + b"\0" + payload)
    path.write_bytes(data + _chunk(b"IEND", b""))
    return path


def studio_session() -> dict:
    """Shaped like Provenance.session(): writer, build, created, scene, sources, caveat, stamp."""
    scene = example()["native"]["studio_session"]
    return {
        "writer": "HelioFITS Studio test-stamp",
        "buildId": "test-build",
        "commit": "abc1234",
        "dirty": False,
        "created": "2026-10-01T12:00:00Z",
        "scene": scene,
        "sources": [
            {"layer": "PUNCH WFI+NFI Mosaic 530 Å CAM L3", "uris": []},
            {"layer": "Proba-3 ASPIICS 5508 Å L3", "uris": []},
            {"layer": "SDO AIA 193 Å L1", "uris": ["https://example.invalid/aia.fits"]},
        ],
        "caveat": "not a calibrated radiance",
        "rhef_stamp": "RHEF oRHEF-2.0 via hfstudio-java test",
    }


# ---- the schema and versions ---------------------------------------------------------------


def test_schema_is_the_design_copy_byte_for_byte():
    assert hashlib.sha256(record.SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256


def test_example_validates_and_agrees_with_jsonschema():
    rec = example()
    assert record.validate(rec) == []
    bad = copy.deepcopy(rec)
    del bad["layers"][0]["steps"]
    bad["layers"][1]["sources"][0]["sha256"] = "XYZ"
    bad["output"]["kind"] = "poster"
    errors = record.validate(bad)
    assert any("missing 'steps'" in e for e in errors)
    assert any("sha256" in e and "does not match" in e for e in errors)
    assert any("output.kind" in e for e in errors)
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(record.schema())
    assert list(validator.iter_errors(rec)) == []
    assert len(list(validator.iter_errors(bad))) == len(errors)


def test_a_newer_major_version_is_refused_and_a_newer_minor_read(tmp_path):
    rec = example()
    rec["hfrecord"] = "2.0"
    (tmp_path / "a.hfrec.json").write_text(json.dumps(rec))
    with pytest.raises(record.UnsupportedRecord, match="2.0 needs a newer HelioFITS Core"):
        record.find(tmp_path / "a.hfrec.json")
    rec["hfrecord"], rec["reader_compat"] = "1.0", 2
    (tmp_path / "b.hfrec.json").write_text(json.dumps(rec))
    with pytest.raises(record.UnsupportedRecord, match="reader 2"):
        record.find(tmp_path / "b.hfrec.json")
    rec["hfrecord"], rec["reader_compat"] = "1.7", 1
    rec["a_key_from_1_7"] = {"anything": True}
    (tmp_path / "c.hfrec.json").write_text(json.dumps(rec))
    found = record.find(tmp_path / "c.hfrec.json")
    assert found["valid"] and found["record"]["a_key_from_1_7"] == {"anything": True}


# ---- finding it -------------------------------------------------------------------------------


def test_sidecar_by_full_name_first_and_basename_as_fallback(tmp_path):
    png = write_png(tmp_path / "still.png")
    stem = tmp_path / "still.hfrec.json"
    stem.write_bytes(FIXTURE.read_bytes())
    found = record.find(png)
    assert found["found_in"] == {"kind": "sidecar", "container": "sidecar", "path": str(stem)}
    assert found["valid"] and not found["legacy"]
    full = tmp_path / "still.png.hfrec.json"  # the canonical name wins when both are there
    full.write_bytes(FIXTURE.read_bytes())
    assert record.find(png)["found_in"]["path"] == str(full)
    assert record.sidecar_paths(png)[:2] == [tmp_path / "still.png.hfrec", full]
    assert stem in record.sidecar_paths(png)


def test_a_sidecar_checks_the_exports_hash(tmp_path):
    png = write_png(tmp_path / "still.png")
    rec = example()
    rec["output"]["file_name"] = "still.png"
    rec["output"]["sha256"] = hashlib.sha256(png.read_bytes()).hexdigest()
    (tmp_path / "still.png.hfrec.json").write_text(json.dumps(rec))
    assert record.find(png)["output_check"]["matches"] is True
    # Opened as the sidecar itself, it finds the export by output.file_name.
    assert record.find(tmp_path / "still.png.hfrec.json")["output_check"]["file"] == str(png)
    png.write_bytes(png.read_bytes() + b"changed")
    assert record.find(png)["output_check"]["matches"] is False


def test_png_text_chunk(tmp_path):
    payload = json.dumps(example(), ensure_ascii=True).encode("ascii")  # tEXt is Latin-1
    png = write_png(tmp_path / "x.png", [(b"tEXt", b"other", b"ignored"), (b"tEXt", b"hfrecord", payload)])
    found = record.find(png)
    assert found["found_in"]["container"] == "png:tEXt:hfrecord"
    assert found["record"]["layers"][2]["name"] == "SDO AIA 193 Å L1"
    assert found["output_check"] is None  # an embedded copy proves nothing about the pixels


def test_png_itxt_compressed_and_a_bad_crc_is_not_trusted(tmp_path):
    text = json.dumps(example(), ensure_ascii=False).encode("utf-8")
    itxt = b"\x01\x00" + b"\0" + b"\0" + zlib.compress(text)
    png = write_png(tmp_path / "x.png", [(b"iTXt", b"hfrecord", itxt)])
    assert record.find(png)["found_in"]["container"] == "png:iTXt:hfrecord"
    raw = bytearray(png.read_bytes())
    at = raw.index(b"iTXthfrecord") + 20
    raw[at] ^= 0xFF  # damage the chunk's data, keeping its CRC
    png.write_bytes(bytes(raw))
    assert record.find(png) is None


def test_legacy_hfstudio_png_becomes_a_minimal_record(tmp_path):
    session = json.dumps(studio_session(), ensure_ascii=True).encode("ascii")
    png = write_png(tmp_path / "old.png", [(b"tEXt", b"hfstudio", session)])
    found = record.find(png)
    assert found["legacy"] and found["valid"], found["errors"]
    assert found["found_in"]["container"] == "png:tEXt:hfstudio"
    rec = found["record"]
    assert rec["native_ref"] == "png:tEXt:hfstudio"
    assert rec["producer"] == {"program": "HelioFITS Studio", "version": "test-build", "build_id": "test-build",
                               "commit": "abc1234", "stamp": "HelioFITS Studio test-stamp", "dirty": False}
    assert rec["created"] == "2026-10-01T12:00:00Z" and rec["output"]["file_name"] == "old.png"
    punch, _aspiics, aia = rec["layers"]
    assert [s["op"] for s in punch["steps"]][:4] == ["select_plane", "clip", "stretch", "image_filter"]
    assert punch["steps"][3]["algorithm"] == "RHEF" and "rhef_upsilon" in [s["op"] for s in punch["steps"]]
    assert "rhef_upsilon" not in [s["op"] for s in aia["steps"]]
    assert aia["sources"] == [{"source_url": "https://example.invalid/aia.fits"}]
    assert rec["legacy"]["rhef_stamp"] == "RHEF oRHEF-2.0 via hfstudio-java test"
    assert record.RHEF_CITATION in rec["citations"]
    assert record.find(png)["record"]["record_id"] == rec["record_id"]  # stable


def test_hfrecord_wins_over_hfstudio_in_the_same_file(tmp_path):
    png = write_png(tmp_path / "both.png", [
        (b"tEXt", b"hfstudio", json.dumps(studio_session()).encode("ascii")),
        (b"tEXt", b"hfrecord", json.dumps(example()).encode("ascii")),
    ])
    found = record.find(png)
    assert not found["legacy"] and found["found_in"]["container"] == "png:tEXt:hfrecord"


def test_a_file_with_no_record(tmp_path):
    assert record.find(write_png(tmp_path / "plain.png")) is None
    (tmp_path / "notes.txt").write_text("hello")
    assert record.find(tmp_path / "notes.txt") is None
    with pytest.raises(FileNotFoundError):
        record.find(tmp_path / "gone.png")


def _exr_attr(name: str, atype: str, value: bytes) -> bytes:
    return name.encode() + b"\0" + atype.encode() + b"\0" + struct.pack("<i", len(value)) + value


def test_exr_string_attribute(tmp_path):
    rec = json.dumps(example()).encode("utf-8")
    header = (b"\x76\x2f\x31\x01" + struct.pack("<I", 2)
              + _exr_attr("compression", "compression", b"\x00")
              + _exr_attr("hfstudio", "string", json.dumps(studio_session()).encode())
              + _exr_attr("hfrecord", "string", rec) + b"\0")
    exr = tmp_path / "frame.exr"
    exr.write_bytes(header + b"\0" * 16)
    found = record.find(exr)
    assert found["found_in"]["container"] == "exr:attr:hfrecord" and found["valid"]
    exr.write_bytes(header.replace(b"hfrecord\0string", b"hfrecorX\0string") + b"\0" * 16)
    assert record.find(exr)["legacy"]


def test_fits_hfrecord_extension_as_bytes_and_as_a_table(tmp_path):
    text = json.dumps(example())
    raw = np.frombuffer(text.encode("utf-8"), dtype=np.uint8)
    image = tmp_path / "phys.fits"
    fits.HDUList([fits.PrimaryHDU(np.zeros((2, 2), np.float32)),
                  fits.ImageHDU(raw, name="HFRECORD")]).writeto(image)
    assert record.find(image)["found_in"]["container"] == "fits:hdu:HFRECORD"
    rows = [text[i:i + 60] for i in range(0, len(text), 60)]
    table = tmp_path / "table.fits"
    col = fits.Column(name="JSON", format="60A", array=np.array(rows))
    fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.from_columns([col], name="HFRECORD")]).writeto(table)
    assert record.find(table)["record"]["record_id"] == example()["record_id"]


def test_fits_export_folder_record(tmp_path):
    phys = tmp_path / "x_phys.fits"
    fits.PrimaryHDU(np.zeros((2, 2), np.float32)).writeto(phys)
    assert record.find(phys) is None
    (tmp_path / "hfrecord.json").write_bytes(FIXTURE.read_bytes())
    assert record.find(phys)["found_in"]["container"] == "folder:hfrecord.json"


def test_movie_without_ffprobe_is_skipped_gracefully(tmp_path, monkeypatch):
    mp4 = tmp_path / "m.mp4"
    mp4.write_bytes(b"\0\0\0\x18ftypisom" + b"\0" * 16)
    monkeypatch.setattr(record.shutil, "which", lambda name: None)
    assert record.find(mp4) is None


def test_movie_tag_through_ffprobe(tmp_path, monkeypatch):
    mp4 = tmp_path / "m.mp4"
    mp4.write_bytes(b"\0\0\0\x18ftypisom" + b"\0" * 16)
    out = json.dumps({"format": {"tags": {"HFRECORD": json.dumps(example()), "comment": "{}"}}})
    monkeypatch.setattr(record.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(record.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))
    found = record.find(mp4)
    assert found["found_in"]["container"] == "mp4:hfrecord" and found["valid"]


# ---- rows for the app ------------------------------------------------------------------------------


def test_display_rows():
    view = record.display(example())
    assert view["summary"].startswith("HelioFITS Studio <version>, still at 2025-09-21T20:32:00Z.")
    top, _middle, bottom = view["layers"]  # top layer first
    assert (top["name"], bottom["name"]) == ("SDO AIA 193 Å L1", "PUNCH WFI+NFI Mosaic 530 Å CAM L3")
    assert bottom["drawn"] == 0 and bottom["filter"] == "RHEF" and bottom["lut"] == "PUNCH"
    steps = bottom["steps"]
    assert [s["index"] for s in steps] == list(range(1, len(steps) + 1))
    clip = steps[1]
    assert clip["op"] == "clip" and {"name": "clippingMode", "value": "Percentile05"} in clip["params"]
    assert {"name": "low", "value": "null"} in clip["resolved"]
    rhef = steps[3]
    assert rhef["citation"].startswith("Gilly & Cranmer (2025)") and rhef["stamp"].startswith("RHEF")
    assert view["studio"]["args"] == ["-dataset", "sdo/aia/-/-/1/193", "-start", "2025-09-21T20:32:04.840",
                                      "-end", "2025-09-21T20:32:04.840"]  # the master layer
    assert len(view["citations"]) == 2 and view["caveats"]


def test_display_flags_varies_reference_automatic_and_unknown_ops():
    rec = example()
    layer = rec["layers"][2]
    layer["dataset"]["dataset_uid"] = "ds-0001"
    layer["acknowledgement"] = "Courtesy of NASA/SDO and the AIA science team."
    layer["steps"].insert(0, {"op": "instrument_correction", "stage": "decode", "automatic": True, "params": {}})
    layer["steps"].append({"op": "a_future_op", "params": {"k": 1}})
    diff = next(s for s in layer["steps"] if s["op"] == "difference")
    diff["params"]["differenceMode"] = "Base"
    diff["reference"] = {"kind": "base", "source_index": 0}
    diff["varies"] = {"params": ["differenceMode"], "automation": ["track-1"], "changed_by_user": True}
    rec["provenance"] = {"product": "still", "credit": "NASA/SDO"}
    top = record.display(rec)["layers"][0]
    assert top["automatic_steps"] == 1 and top["steps"][0]["automatic"]
    assert top["varies"]
    row = next(s for s in top["steps"] if s["op"] == "difference")
    assert row["varies_params"] == ["differenceMode"] and row["automation"] == ["track-1"]
    assert row["reference"] == "base frame: source 0"
    assert top["steps"][-1]["known_op"] is False and top["steps"][-1]["op"] == "a_future_op"
    assert top["studio"]["dataset"] == "ds-0001"  # dataset_uid before dataset_key
    view = record.display(rec)
    assert view["acknowledgements"] == [{"layer": "SDO AIA 193 Å L1",
                                         "text": "Courtesy of NASA/SDO and the AIA science team."}]
    assert {"name": "credit", "value": "NASA/SDO"} in view["provenance"]


# ---- against the library ---------------------------------------------------------------------------


def _sha(lib, relpath):
    return lib.reader.execute("SELECT sha256 FROM files WHERE relpath = ?", (relpath,)).fetchone()[0]


@pytest.fixture
def stocked(lib, src):
    """A library holding one AIA frame and two versions of one PUNCH frame."""
    aia = lib.ingest(write_fits(src / "aia.fits", aia_header(date="2025-09-21T20:32:04.84")))
    old = lib.ingest(write_fits(src / "PUNCH_L3_CTM_20250921203200_v0k.fits",
                                punch_header(date="2025-09-21T20:32:00.000", version="0k"), compressed=True))
    new = lib.ingest(write_fits(src / "PUNCH_L3_CTM_20250921203200_v0l.fits",
                                punch_header(date="2025-09-21T20:32:00.000", version="0l"), compressed=True))
    assert new.status == "new_version"
    return {"aia": aia, "punch_old": old, "punch_new": new}


def _record_for(lib, stocked) -> dict:
    rec = example()
    punch, aspiics, aia = rec["layers"]
    aia_key = lib.reader.execute("SELECT dataset_key FROM datasets WHERE instrument LIKE 'AIA%'").fetchone()[0]
    aia["dataset"]["dataset_key"] = aia_key
    aia["sources"][0]["sha256"] = _sha(lib, stocked["aia"].relpath)
    aia["sources"].append({**aia["sources"][0], "sha256": "0" * 64, "date_obs": "2025-09-21T20:33:04.840"})
    punch["sources"][0]["sha256"] = _sha(lib, stocked["punch_old"].relpath)
    aspiics["sources"].append({"helioviewer": {"server": "x", "sourceId": 1}})
    return rec


def test_resolve_against_a_library(lib, stocked):
    rec = _record_for(lib, stocked)
    out = record.resolve(lib.reader, rec)
    assert out["counts"] == {"in_library": 1, "superseded": 1, "missing": 2, "no_hash": 1}
    assert not out["complete"]
    punch, aspiics, aia = out["layers"]
    assert punch["sources"][0]["status"] == "superseded"
    assert punch["sources"][0]["current"]["version"] == "0l"
    assert aia["sources"][0]["status"] == "in_library" and aia["sources"][0]["relpath"] == stocked["aia"].relpath
    assert aia["sources"][1]["status"] == "missing"
    assert aspiics["sources"][0]["status"] == "missing" and aspiics["sources"][1]["status"] == "no_hash"
    groups = {g["dataset_key"]: g for g in out["missing_by_dataset"]}
    kept = groups[aia["dataset_key"]]
    assert kept["in_library"] and kept["count"] == 1
    assert kept["fetch"]["method"] == "dataset.upgrade"
    assert kept["fetch"]["params"]["dataset_key"] == aia["dataset_key"] and not kept["fetch"]["params"]["dry_run"]
    assert kept["start"] < "2025-09-21T20:33:04.840" < kept["end"]
    away = groups["proba-3/aspiics/-/-/3/5508"]
    assert not away["in_library"] and away["fetch"]["method"] == "download.enqueue"
    assert away["fetch"]["inferred"] and away["fetch"]["params"]["query"]["instrument"] == "ASPIICS"


def test_resolve_prefers_dataset_uid_when_the_index_has_one(lib, stocked, tmp_path):
    """Against an index with a datasets.dataset_uid column (schema 5 has it; added here on a
    copy when an older schema lacks it), a record's dataset_uid finds the dataset whatever its key."""
    import sqlite3

    conn = sqlite3.connect(tmp_path / "copy.sqlite")
    with sqlite3.connect(lib.cfg.db_path) as live:
        live.backup(conn)  # the index is in WAL mode, so copy it through SQLite, not the file
    conn.row_factory = sqlite3.Row
    if "dataset_uid" not in {r[1] for r in conn.execute("PRAGMA table_info(datasets)")}:
        conn.execute("ALTER TABLE datasets ADD COLUMN dataset_uid TEXT")
    conn.execute("UPDATE datasets SET dataset_uid = 'uid-aia' WHERE instrument LIKE 'AIA%'")
    conn.commit()
    rec = _record_for(lib, stocked)
    aia = rec["layers"][2]
    aia["dataset"]["dataset_key"] = "renamed/after/a/reindex"
    aia["dataset"]["dataset_uid"] = "uid-aia"
    out = record.resolve(conn, rec)
    assert out["layers"][2]["dataset_in_library"] is not None
    group = next(g for g in out["missing_by_dataset"] if g["dataset"] == "uid-aia")
    assert group["in_library"] and group["fetch"]["method"] == "dataset.upgrade"
    conn.close()


def test_rpc_methods(lib, stocked, tmp_path):
    server = Server(lib, out=io.StringIO())
    png = write_png(tmp_path / "still.png")
    (tmp_path / "still.png.hfrec.json").write_text(json.dumps(_record_for(lib, stocked)))
    reply = server._call(1, server.methods["record.read"], {"path": str(png)})
    result = reply["result"]
    assert result["found"] and result["view"]["layers"][0]["name"] == "SDO AIA 193 Å L1"
    json.dumps(result)  # all of it goes over the wire
    resolved = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "record.resolve",
                                              "params": {"record": result["record"]}}))
    assert resolved["result"]["counts"]["in_library"] == 1
    none = server._call(3, server.methods["record.read"], {"path": str(write_png(tmp_path / "plain.png"))})
    assert none["result"] == {"found": False, "path": str(tmp_path / "plain.png"), "record": None, "view": None}
    bad = example()
    bad["hfrecord"] = "2.0"
    refused = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 4, "method": "record.resolve",
                                             "params": {"record": bad}}))
    assert refused["error"]["code"] == RECORD_UNSUPPORTED
    server._pool.shutdown()
