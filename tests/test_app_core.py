"""What the Mac app and Studio need from the core: thumbnails and hfcore:// links."""

import io
import json
import struct
import zlib

import numpy as np
import pytest
from astropy.io import fits

from pyheliofits.core import thumbs
from pyheliofits.core.query import Query
from pyheliofits.core.rpc import Server
from conftest import aia_header, write_fits


def _png_pixels(png: bytes) -> np.ndarray:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos:pos + 4])
        kind, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
        (crc,) = struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks[kind] = chunks.get(kind, b"") + body
        pos += 12 + n
    width, height, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, colour) == (8, 0)
    raw = zlib.decompress(chunks[b"IDAT"])
    rows = [raw[y * (width + 1):(y + 1) * (width + 1)] for y in range(height)]
    assert all(r[0] == 0 for r in rows)
    return np.array([list(r[1:]) for r in rows], dtype=np.uint8)


def test_thumbnail_is_a_small_png_with_north_up(lib, src):
    data = np.tile(np.arange(600, dtype=np.float32)[:, None], (1, 1000))  # bright at the top row of FITS
    path = src / "big.fits"
    hdu = fits.PrimaryHDU(data=data)
    for k, v in aia_header().items():
        hdu.header[k] = v
    hdu.writeto(path)
    r = lib.ingest(path)
    out = lib.thumbnail(r.file_id)
    pixels = _png_pixels(out.read_bytes())
    assert pixels.shape == (150, 250)  # 1000 x 600 by blocks of 4
    assert pixels[0].mean() > pixels[-1].mean()  # last FITS row (largest values) is the PNG's first
    assert lib.thumbnail(r.file_id) == out  # cached
    assert out.parent.parent.name == ".thumbs"


def test_thumbnail_handles_compressed_and_nan(lib, src):
    r = lib.ingest(write_fits(src / "c.fits", aia_header(), compressed=True))
    assert _png_pixels(lib.thumbnail(r.file_id).read_bytes()).shape == (8, 8)
    flat = np.full((4, 4), np.nan, dtype=np.float32)
    assert thumbs.scale(flat).max() == 0


def test_delete_removes_the_thumbnail(lib, src):
    r = lib.ingest(write_fits(src / "a.fits", aia_header()))
    out = lib.thumbnail(r.file_id)
    big = lib.thumbnail(r.file_id, size=1024)  # the frame viewer's size
    assert big != out and big.exists()
    lib.delete(file_ids=[r.file_id])
    assert not out.exists() and not big.exists()


def test_hfcore_link_round_trip():
    q = Query(start="2025-09-21T16:00:00", end="2025-09-21T16:05:00", instrument="AIA",
              wavelength=193.0, sample_s=60.0)
    url = q.to_url()
    assert url == ("hfcore://search?end=2025-09-21T16%3A05%3A00&instrument=AIA&sample_s=60"
                   "&start=2025-09-21T16%3A00%3A00&wavelength=193")
    assert Query.from_url(url) == ("search", q)
    assert Query.from_url(q.to_url("get"))[0] == "get"


@pytest.mark.parametrize("url", [
    "https://search?start=a&end=b",
    "hfcore://delete?start=a&end=b",
    "hfcore://search?start=a",
    "hfcore://search?start=a&end=b&colour=red",
    "hfcore://search?start=a&end=b&end=c",
])
def test_bad_hfcore_links_are_refused(url):
    with pytest.raises(ValueError):
        Query.from_url(url)


def test_rpc_thumbnail_and_link(lib, src):
    r = lib.ingest(write_fits(src / "a.fits", aia_header()))
    server = Server(lib, out=io.StringIO())
    call = lambda m, **p: server.handle_line(json.dumps(  # noqa: E731
        {"jsonrpc": "2.0", "id": 1, "method": m, "params": p}))
    assert call("library.thumbnail", file_id=r.file_id) is None  # answered from the pool
    server._pool.shutdown(wait=True)
    (reply,) = [json.loads(line) for line in server.out.getvalue().splitlines()]
    assert reply["id"] == 1 and reply["result"]["path"].endswith(".png")
    parsed = call("query.from_url", url="hfcore://get?start=2025-09-21T16:00:00&end=2025-09-21T17:00:00&wavelength=171")
    assert parsed["result"] == {"action": "get", "query": {"start": "2025-09-21T16:00:00",
                                                           "end": "2025-09-21T17:00:00", "wavelength": 171.0}}
    assert call("query.from_url", url="hfcore://nope?start=a&end=b")["error"]["code"] == -32602


def test_slow_requests_do_not_hold_up_the_rest(lib, src):
    """A search still running does not stop the core answering other requests."""
    import threading

    release = threading.Event()
    server = Server(lib, out=io.StringIO())
    server.methods["search"] = lambda **p: release.wait(5) and {"total": 0}
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "search", "params": {"query": {}}},
             {"jsonrpc": "2.0", "id": 2, "method": "library.stats"}]
    assert server.handle_line(json.dumps(lines[0])) is None
    assert server.handle_line(json.dumps(lines[1]))["id"] == 2
    release.set()
    server._pool.shutdown(wait=True)
    (late,) = [json.loads(line) for line in server.out.getvalue().splitlines()]
    assert late == {"jsonrpc": "2.0", "id": 1, "result": {"total": 0}}


def test_serve_answers_background_requests_before_it_returns(lib, src):
    r = lib.ingest(write_fits(src / "a.fits", aia_header()))
    out = io.StringIO()
    Server(lib, out=out).serve(io.StringIO(json.dumps(
        {"jsonrpc": "2.0", "id": 7, "method": "library.thumbnail", "params": {"file_id": r.file_id}}) + "\n"))
    (reply,) = [json.loads(line) for line in out.getvalue().splitlines()]
    assert reply["id"] == 7 and "result" in reply


def test_files_page_through_with_offset(lib, src):
    for minute in range(5):
        lib.ingest(write_fits(src / f"a{minute}.fits", aia_header(date=f"2025-09-21T16:0{minute}:04.84")))
    pages = [lib.current_files(limit=2, offset=o) for o in (0, 2, 4)]
    assert [len(p) for p in pages] == [2, 2, 1]
    assert [f["date_obs"][:16] for p in pages for f in p] == [f"2025-09-21T16:0{m}" for m in range(5)]


def test_python_dash_m_runs_the_cli(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "home"))
    out = subprocess.run([sys.executable, "-m", "pyheliofits.core", "--json", "settings"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip().startswith("{")
