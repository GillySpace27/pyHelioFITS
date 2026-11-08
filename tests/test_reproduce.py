"""Pixel digests, comparison verdicts and preflight (docs/reproduce.md)."""

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from pyheliofits import reproduce
from pyheliofits.core import record as hfrecord

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "example-studio-connect-still.hfrec.json"


def example() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def rgba(seed: int = 1, shape=(24, 32, 4)) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, shape, dtype=np.uint8)


def test_digest_is_the_documented_stream_and_ignores_text_chunks(tmp_path):
    arr = rgba()
    a = reproduce.write_png(tmp_path / "a.png", arr)
    expected = hashlib.sha256(b"hfpix/1 32 24 4 8\n" + arr.tobytes()).hexdigest()
    d = reproduce.digests(a)
    assert d["pixel_sha256"] == expected
    assert (d["width"], d["height"], d["channels"], d["bit_depth"]) == (32, 24, 4, 8)
    # the same picture with a text chunk appended: pixel and content digests hold, the file hash moves
    data = a.read_bytes()
    chunk = b"hfrecord\0{}"
    import struct
    import zlib
    text = struct.pack(">I", len(chunk)) + b"tEXt" + chunk + struct.pack(">I", zlib.crc32(b"tEXt" + chunk))
    b = tmp_path / "b.png"
    b.write_bytes(data[:-12] + text + data[-12:])
    e = reproduce.digests(b)
    assert e["pixel_sha256"] == d["pixel_sha256"]
    assert e["content_sha256"] == d["content_sha256"]
    assert e["sha256"] != d["sha256"]
    assert reproduce.compare(a, b).verdict == "identical"


def test_builtin_decoder_agrees_with_pillow_on_every_filter_and_depth(tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    rng = np.random.default_rng(3)
    # smooth data makes the encoder pick Sub, Up, Average and Paeth rows
    y, x = np.mgrid[0:40, 0:50]
    smooth = ((np.sin(x / 7) + np.cos(y / 5)) * 100 + 128).astype(np.uint8)
    cases = {
        "grey8": smooth,
        "rgb8": np.stack([smooth, smooth[::-1], rng.integers(0, 256, smooth.shape, dtype=np.uint8)], axis=2),
        "rgba8": np.concatenate([np.stack([smooth] * 3, axis=2), rng.integers(0, 256, smooth.shape + (1,), dtype=np.uint8)], axis=2),
        "grey16": (smooth.astype(np.uint16) * 257),
    }
    for name, arr in cases.items():
        p = tmp_path / f"{name}.png"
        PIL.fromarray(arr).save(p, optimize=True)
        own = reproduce._read_png(p)
        via = reproduce.read_image(p)
        want = arr if arr.ndim == 3 else arr[:, :, None]
        assert own.samples.shape == want.shape, name
        assert np.array_equal(own.samples, want), name
        assert np.array_equal(via.samples, want), name
        assert own.bit_depth == via.bit_depth == (16 if name.endswith("16") else 8), name


def test_sixteen_bit_rgba_keeps_every_sample(tmp_path):
    """Pillow reduces 16-bit RGB(A) to 8 bits; the digest must not."""
    import struct
    import zlib
    arr = np.random.default_rng(5).integers(0, 65536, (6, 7, 4), dtype=np.uint16)
    rows = b"".join(b"\x00" + arr[y].astype(">u2").tobytes() for y in range(6))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    p = tmp_path / "rgba16.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 7, 6, 16, 6, 0, 0, 0))
                  + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    img = reproduce.read_image(p)
    assert img.bit_depth == 16 and np.array_equal(img.samples, arr)
    assert reproduce.pixel_digest(img) == hashlib.sha256(b"hfpix/1 7 6 4 16\n" + arr.astype(">u2").tobytes()).hexdigest()


def test_compare_reports_metrics_and_needs_a_tolerance_for_equivalent(tmp_path):
    arr = rgba()
    a = reproduce.write_png(tmp_path / "a.png", arr)
    other = arr.copy()
    other[3, 5, 1] = (int(other[3, 5, 1]) + 7) % 256
    other[10, 20, 0] = (int(other[10, 20, 0]) + 2) % 256
    b = reproduce.write_png(tmp_path / "b.png", other)
    r = reproduce.compare(a, b, diff_out=tmp_path / "diff.png")
    assert r.verdict == "different"
    m = r.metrics
    assert m["differing_pixels"] == 2 and m["pixels"] == 24 * 32
    assert m["max_abs_diff"] in (7, 249) and m["levels"] == 255
    assert m["first_difference"] == {"row": 3, "column": 5}
    assert any("no tolerance" in x for x in r.reasons)
    assert Path(r.difference_image).is_file()
    diff = reproduce.read_image(r.difference_image)
    assert diff.samples.max() == 255 and np.count_nonzero(diff.samples.max(axis=2)) == 2
    assert reproduce.compare(a, b, tolerance=m["max_abs_diff"]).verdict == "equivalent"
    assert reproduce.compare(a, b, tolerance=m["max_abs_diff"] - 1).verdict == "different"
    text = r.text()
    assert text.startswith("Verdict: DIFFERENT") and "Differing pixels: 2 of 768" in text
    assert "\u2014" not in text


def test_compare_refuses_to_guess_across_sizes_and_flags_same_environment(tmp_path):
    a = reproduce.write_png(tmp_path / "a.png", rgba())
    b = reproduce.write_png(tmp_path / "b.png", rgba(shape=(24, 30, 4)))
    r = reproduce.compare(a, b, tolerance=255)
    assert r.verdict == "cannot_check" and not r.comparable  # a size mismatch is the replay's export, not the picture
    c = reproduce.write_png(tmp_path / "c.png", rgba(seed=2))
    env = {"gpu": {"renderer": "Apple M2"}, "display": {"dither": False}, "producer": {"commit": "abc"}}
    r = reproduce.compare(a, c, original_environment=env, replay_environment=copy.deepcopy(env))
    assert r.environment == {"compared": True, "differences": []}
    assert any("same" in x and "bug" in x for x in r.reasons)
    other = copy.deepcopy(env)
    other["gpu"]["renderer"] = "Mesa llvmpipe"
    r = reproduce.compare(a, c, original_environment=env, replay_environment=other)
    assert r.environment["differences"] == [{"key": "gpu.renderer", "original": "Apple M2", "replay": "Mesa llvmpipe"}]
    assert "gpu.renderer" in r.text()


def _sidecar(tmp_path: Path, arr: np.ndarray, **output) -> tuple[Path, Path]:
    png = reproduce.write_png(tmp_path / "figure.png", arr)
    rec = example()
    rec["output"].update({"file_name": "figure.png", "sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
                          "width": arr.shape[1], "height": arr.shape[0], "bit_depth": 8, **output})
    side = tmp_path / "figure.png.hfrec"
    side.write_text(json.dumps(rec), encoding="utf-8")
    return png, side


def test_verify_against_a_record_with_and_without_the_original(tmp_path):
    arr = rgba()
    png, side = _sidecar(tmp_path, arr)
    digest = reproduce.digests(png)["pixel_sha256"]
    rec = json.loads(side.read_text())
    rec["output"]["pixel_sha256"] = digest
    rec["hfrecord"] = "1.1"
    side.write_text(json.dumps(rec), encoding="utf-8")
    assert hfrecord.validate(rec) == []
    replay = reproduce.write_png(tmp_path / "replay.png", arr)
    r = reproduce.verify(side, replay)
    assert r.verdict == "identical"
    assert r.original_digest == digest
    # the original export vanished: the stored digest still judges the replay
    png.unlink()
    assert reproduce.verify(side, replay).verdict == "identical"
    changed = arr.copy()
    changed[0, 0, 0] ^= 0xFF
    bad = reproduce.write_png(tmp_path / "bad.png", changed)
    r = reproduce.verify(side, bad)
    assert r.verdict == "different" and r.metrics == {}
    assert any("only the digests" in x for x in r.reasons)
    # a 1.0 record without its export cannot be judged
    del rec["output"]["pixel_sha256"]
    rec["hfrecord"] = "1.0"
    side.write_text(json.dumps(rec), encoding="utf-8")
    assert reproduce.verify(side, replay).verdict == "cannot_check"


def test_verify_notices_an_edited_original(tmp_path):
    arr = rgba()
    png, side = _sidecar(tmp_path, arr, pixel_sha256="0" * 64)
    r = reproduce.verify(side, png)
    assert r.verdict == "identical"  # the file beside it equals the replay...
    assert any("does not match the record's own pixel digest" in x for x in r.reasons)  # ...but not the record


def test_preflight_and_replay_reasons_read_both_formats():
    rec = example()
    pre = reproduce.preflight(rec)
    assert pre["can_replay"] and pre["scope"] == "one" and not pre["has_digest"]
    assert pre["frames"] == 1
    rec11 = copy.deepcopy(rec)
    rec11["hfrecord"] = "1.1"
    rec11["producer"]["dirty"] = True
    rec11["snapshot"] = {"taken": "export_start", "changed_during_export": True}
    rec11["layers"][0]["files_pending"] = 2
    rec11["environment"] = {"gpu": {"vendor": "Apple", "renderer": "Apple M2"}, "display": {"dither": True}}
    rec11["inputs_external"] = [{"kind": "overlay_feed", "name": "CACTus CMEs", "pinned": False},
                                {"kind": "star_catalog", "name": "hipparcos", "version": "v9", "pinned": True}]
    rec11["reproducibility"] = {"exact_replay_possible": False, "reasons": ["the writer says so"], "replay_scope": "range"}
    rec11["companions"] = {"data_archive": {"file_name": "figure.png.data.zip", "sha256": "a" * 64, "size": 12, "scope": "one"}}
    assert hfrecord.validate(rec11) == []
    reasons = hfrecord.replay_reasons(rec11)
    assert reasons[0] == "the writer says so"
    joined = " | ".join(reasons)
    for needle in ("uncommitted", "changed while the export ran", "2 source file(s)", "CACTus", "dither was on"):
        assert needle in joined, needle
    assert "hipparcos" not in joined
    pre = reproduce.preflight(rec11)
    assert pre["can_replay"] and not pre["exact"] and pre["scope"] == "range"
    view = hfrecord.display(rec11)
    assert view["reproducibility"]["reasons"] == reasons and view["reproducibility"]["scope"] == "range"
    assert {"key": "gpu.renderer", "value": "Apple M2"} in view["environment"]
    assert view["not_guaranteed_to_rebuild"] == reasons


def test_schema_rejects_bad_new_keys():
    rec = example()
    rec["output"]["pixel_sha256"] = "not hex"
    rec["reproducibility"] = {"reasons": []}
    rec["inputs_external"] = [{"kind": "weather", "name": "x"}]
    errors = hfrecord.validate(rec)
    assert any("pixel_sha256" in e for e in errors)
    assert any("exact_replay_possible" in e for e in errors)
    assert any("weather" in e for e in errors)


def test_cli_digest_verify_and_preflight(tmp_path, capsys):
    from pyheliofits.core.cli import main
    arr = rgba()
    png, side = _sidecar(tmp_path, arr)
    assert main(["record", "digest", str(png)]) == 0
    out = capsys.readouterr().out
    assert reproduce.digests(png)["pixel_sha256"] in out and "pixel (hfpix/1" in out
    replay = reproduce.write_png(tmp_path / "replay.png", arr)
    assert main(["record", "verify", str(side), str(replay)]) == 0
    assert capsys.readouterr().out.startswith("Verdict: IDENTICAL")
    changed = arr.copy()
    changed[1, 1, 2] ^= 0x0F
    bad = reproduce.write_png(tmp_path / "bad.png", changed)
    assert main(["--json", "record", "verify", str(side), str(bad), "--tolerance", "1"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "different" and data["metrics"]["differing_pixels"] == 1
    assert main(["record", "preflight", str(side)]) == 0
    assert "Exact replay possible" in capsys.readouterr().out


# ---- the driver ----------------------------------------------------------------------------------

SESSION_LOAD = {
    "name": "sessionLoad", "description": "test copy of Studio's sessionLoad", "x-effect": "destructive",
    "inputSchema": {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
        "path": {"type": "string"}, "range": {"type": "boolean"}, "window": {"enum": ["this", "new"]}}},
    "outputSchema": {"type": "object"},
}


def _replay_fixture(tmp_path, arr, **output):
    png = reproduce.write_png(tmp_path / "figure.png", arr)
    rec = example()
    rec["hfrecord"] = "1.1"
    rec["output"].update({"file_name": "figure.png", "width": arr.shape[1], "height": arr.shape[0], "bit_depth": 8,
                          "pixel_sha256": reproduce.digests(png)["pixel_sha256"], **output})
    side = tmp_path / "figure.png.hfrec"
    side.write_text(json.dumps(rec), encoding="utf-8")
    return png, side


def _fake(replayed: np.ndarray, notes=()):
    from pyheliofits.studio import Studio
    from pyheliofits.testing import FakeStudio

    def load(a):
        return {"path": a["path"], "window": a.get("window", "this"), "notes": list(notes)}

    def still(a):
        reproduce.write_png(a["path"], replayed)
        return {"path": a["path"]}

    fake = FakeStudio(handlers={"sessionLoad": load, "exportStill": still})
    fake.tools["sessionLoad"] = SESSION_LOAD
    return fake, Studio


def test_reproduce_drives_studio_and_judges_the_picture(tmp_path):
    arr = rgba()
    png, side = _replay_fixture(tmp_path, arr)
    png.unlink()  # only the record's digest is left to judge by
    fake, Studio = _fake(arr, notes=["2 files found in the data archive"])
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api.json").read_text()))
        r = reproduce.reproduce(side, tmp_path / "replay", studio)
    assert r.verdict == "identical"
    assert "2 files found in the data archive" in r.reasons
    names = [c[0] for c in fake.calls]
    assert names == ["sessionLoad", "exportStill"]
    load = fake.calls[0][1]
    assert load["window"] == "this" and load["range"] is False and load["path"].endswith("figure.png.hfrec")
    assert fake.calls[1][1]["long_side"] == 32  # the long side of 24 x 32
    assert (tmp_path / "replay" / "figure-replay.png").is_file()


def test_reproduce_reports_a_different_picture_and_a_size_the_export_cannot_match(tmp_path):
    arr = rgba()
    png, side = _replay_fixture(tmp_path, arr)
    changed = arr.copy()
    changed[0, 0, 0] ^= 0x20
    fake, Studio = _fake(changed)
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api.json").read_text()))
        r = reproduce.reproduce(side, tmp_path / "replay", studio, tolerance=0)
        assert r.verdict == "different" and r.metrics["differing_pixels"] == 1 and r.difference_image
        r = reproduce.reproduce(side, tmp_path / "replay", studio, tolerance=0x20)
        assert r.verdict == "equivalent"
    fake, Studio = _fake(rgba(shape=(10, 10, 4)))
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api2.json").read_text()))
        r = reproduce.reproduce(side, tmp_path / "replay2", studio)
    assert r.verdict == "cannot_check" and not r.comparable


def test_reproduce_movie_range_and_refusals(tmp_path):
    arr = rgba()
    png, side = _replay_fixture(tmp_path, arr, kind="movie", frames=12)
    fake, Studio = _fake(arr)
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api.json").read_text()))
        one = reproduce.reproduce(side, tmp_path / "one", studio)
        rng = reproduce.reproduce(side, tmp_path / "range", studio, frames="range")
        with pytest.raises(ValueError):
            reproduce.reproduce(side, tmp_path / "x", studio, frames="all")
    assert one.verdict == "identical" and "1 of 12 frames checked (the first)" in one.reasons
    assert [c[1].get("range") for c in fake.calls if c[0] == "sessionLoad"] == [False, True]
    assert rng.verdict == "identical" and any("first frame only" in x for x in rng.reasons)
    # a record that only lives inside its export cannot be handed to Studio as a file
    from pyheliofits.core import record as hfr
    embedded = tmp_path / "embedded.png"
    chunk = json.dumps(json.loads(side.read_text())).encode()
    import struct, zlib
    data = png.read_bytes()
    body = b"hfrecord\0" + chunk
    text = struct.pack(">I", len(body)) + b"tEXt" + body + struct.pack(">I", zlib.crc32(b"tEXt" + body))
    embedded.write_bytes(data[:-12] + text + data[-12:])
    assert hfr.read(embedded)["found"]
    fake, Studio = _fake(arr)
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api3.json").read_text()))
        r = reproduce.reproduce(embedded, tmp_path / "emb", studio)
    assert r.verdict == "cannot_check" and any("sidecar" in x for x in r.reasons)
    assert fake.calls == []


def test_a_movie_beside_its_record_is_not_read_as_a_picture(tmp_path):
    # Found replaying a real Studio movie: movie.mp4 sits beside movie.mp4.hfrec, and verify used to
    # open it as a PNG and crash. A lossy movie cannot be compared pixel for pixel; the record's
    # frame digest is what judges the replayed first frame.
    arr = rgba()
    png = reproduce.write_png(tmp_path / "first.png", arr)
    digest = reproduce.digests(png)["pixel_sha256"]
    rec = example()
    rec["hfrecord"] = "1.1"
    rec["output"].update({"kind": "movie", "frames": 4, "file_name": "movie.mp4", "media_type": "video/mp4",
                          "width": arr.shape[1], "height": arr.shape[0], "bit_depth": 8, "pixel_sha256": digest,
                          "frame_digests": [digest, "1" * 64, "2" * 64, "3" * 64]})
    side = tmp_path / "movie.mp4.hfrec"
    side.write_text(json.dumps(rec), encoding="utf-8")
    (tmp_path / "movie.mp4").write_bytes(b"\x00\x00\x00\x20ftypisom not a PNG")
    r = reproduce.verify(side, png)
    assert r.verdict == "identical" and r.original_digest == digest
    # and a 16-bit replay of 8-bit frames says it cannot compare, not that the picture differs
    r = reproduce.verify(side, _png16(tmp_path / "deep.png", arr))
    assert r.verdict == "cannot_check" and any("8-bit" in x and "16-bit" in x for x in r.reasons)


def _png16(path: Path, arr: np.ndarray) -> Path:
    import struct
    import zlib
    h, w, c = arr.shape
    deep = (arr.astype(">u2") * 257)
    rows = b"".join(b"\x00" + deep[y].tobytes() for y in range(h))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    path.write_bytes(reproduce._PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, {1: 0, 3: 2, 4: 6}[c], 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))
    return path


def _movie_fake(tmp_path, replay_digests, **replay_output):
    """A Studio whose exportMovie writes a replay record carrying ``replay_digests`` beside the movie."""
    from pyheliofits.studio import Studio
    from pyheliofits.testing import FakeStudio

    def movie(a):
        rec = example()
        rec["hfrecord"] = "1.1"
        rec["output"].update({"kind": "movie", "frames": len(replay_digests), "file_name": Path(a["path"]).name,
                              "width": 32, "height": 24, "bit_depth": 8, "frame_digests": replay_digests,
                              "pixel_sha256": replay_digests[0], **replay_output})
        Path(a["path"] + ".hfrec").write_text(json.dumps(rec), encoding="utf-8")
        return {"path": a["path"], "frames": len(replay_digests)}

    fake = FakeStudio(handlers={"sessionLoad": lambda a: {"path": a["path"], "window": "this", "notes": []},
                                "exportMovie": movie})
    fake.tools["sessionLoad"] = SESSION_LOAD
    return fake, Studio


def _movie_record(tmp_path, digests):
    rec = example()
    rec["hfrecord"] = "1.1"
    rec["output"].update({"kind": "movie", "frames": len(digests), "file_name": "movie.mp4", "media_type": "video/mp4",
                          "width": 32, "height": 24, "bit_depth": 8, "pixel_sha256": digests[0], "frame_digests": digests})
    side = tmp_path / "movie.mp4.hfrec"
    side.write_text(json.dumps(rec), encoding="utf-8")
    return side


def test_a_movie_record_is_judged_frame_by_frame_from_the_replays_own_record(tmp_path):
    # Found replaying a real Studio movie: a still export is 16-bit and the frames are 8-bit, so a frame
    # cannot be re-exported as a still. The replay exports its own movie and its record's digests are compared.
    good = [f"{i:x}" * 64 for i in range(4)]
    side = _movie_record(tmp_path, good)
    fake, Studio = _movie_fake(tmp_path, good)
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api.json").read_text()))
        rng = reproduce.reproduce(side, tmp_path / "range", studio, frames="range")
        one = reproduce.reproduce(side, tmp_path / "one", studio)
    assert rng.verdict == "identical" and rng.metrics["frames"] == {"original": 4, "replay": 4, "compared": 4, "identical": 4, "differing": []}
    assert one.verdict == "identical" and one.metrics["frames"]["compared"] == 1 and "1 of 4 frames checked (the first)" in one.reasons
    assert [c[0] for c in fake.calls] == ["sessionLoad", "exportMovie", "sessionLoad", "exportMovie"]
    assert fake.calls[1][1]["long_side"] == 32
    # one frame differs: the first is fine, so "one" stays identical and "range" says which frame
    bad = list(good)
    bad[2] = "e" * 64
    fake, Studio = _movie_fake(tmp_path, bad)
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api2.json").read_text()))
        rng = reproduce.reproduce(side, tmp_path / "range2", studio, frames="range")
        one = reproduce.reproduce(side, tmp_path / "one2", studio)
        tol = reproduce.reproduce(side, tmp_path / "range3", studio, frames="range", tolerance=5)
    assert rng.verdict == "different" and rng.metrics["frames"]["differing"] == [2]
    assert any("differing frame index(es): 2" in x for x in rng.reasons)
    assert one.verdict == "identical"
    assert tol.verdict == "different" and any("no tolerance was applied" in x for x in tol.reasons)


def test_a_movie_replay_that_cannot_be_compared_says_so(tmp_path):
    good = [f"{i:x}" * 64 for i in range(4)]
    side = _movie_record(tmp_path, good)
    cases = [
        (good, {"bit_depth": 16}, "bit_depth"),
        (good, {"width": 64}, "width"),
        (good[:3], {}, "3 frames"),
    ]
    for n, (digests, extra, word) in enumerate(cases):
        fake, Studio = _movie_fake(tmp_path, digests, **extra)
        with fake as f:
            studio = Studio(json.loads(f.endpoint_file(tmp_path / f"api{n}.json").read_text()))
            r = reproduce.reproduce(side, tmp_path / f"c{n}", studio, frames="range")
        assert r.verdict == "cannot_check" and not r.comparable and any(word in x for x in r.reasons), (word, r.reasons)
    # Studio wrote no record beside the replayed movie
    from pyheliofits.studio import Studio
    from pyheliofits.testing import FakeStudio
    fake = FakeStudio(handlers={"sessionLoad": lambda a: {"path": a["path"], "window": "this", "notes": []}})
    fake.tools["sessionLoad"] = SESSION_LOAD
    with fake as f:
        studio = Studio(json.loads(f.endpoint_file(tmp_path / "api9.json").read_text()))
        r = reproduce.reproduce(side, tmp_path / "c9", studio)
    assert r.verdict == "cannot_check" and any("no frame digests" in x for x in r.reasons)


def test_isolated_home_and_launch_command_guard(tmp_path):
    home = reproduce.isolated_home(tmp_path / "home")
    props = (home / "HFStudio" / "Settings" / "user.properties").read_text()
    assert "api.enabled=true" in props and "api.confirmDestructive=false" in props
    with pytest.raises(ValueError, match=r"\{home\}"):
        reproduce.launch_studio(["java", "-jar", "HFStudio.jar"], tmp_path / "h2")
