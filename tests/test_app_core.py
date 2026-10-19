"""What the Mac app and Studio need from the core: thumbnails and heliad:// links."""

import io
import json
import struct
import zlib

import numpy as np
import pytest
from astropy.io import fits

from heliad import thumbs
from heliad.query import Query
from heliad.rpc import Server
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
    lib.delete(file_ids=[r.file_id])
    assert not out.exists()


def test_heliad_link_round_trip():
    q = Query(start="2025-09-21T16:00:00", end="2025-09-21T16:05:00", instrument="AIA",
              wavelength=193.0, sample_s=60.0)
    url = q.to_url()
    assert url == ("heliad://search?end=2025-09-21T16%3A05%3A00&instrument=AIA&sample_s=60"
                   "&start=2025-09-21T16%3A00%3A00&wavelength=193")
    assert Query.from_url(url) == ("search", q)
    assert Query.from_url(q.to_url("get"))[0] == "get"


@pytest.mark.parametrize("url", [
    "https://search?start=a&end=b",
    "heliad://delete?start=a&end=b",
    "heliad://search?start=a",
    "heliad://search?start=a&end=b&colour=red",
    "heliad://search?start=a&end=b&end=c",
])
def test_bad_heliad_links_are_refused(url):
    with pytest.raises(ValueError):
        Query.from_url(url)


def test_rpc_thumbnail_and_link(lib, src):
    r = lib.ingest(write_fits(src / "a.fits", aia_header()))
    server = Server(lib, out=io.StringIO())
    call = lambda m, **p: server.handle_line(json.dumps(  # noqa: E731
        {"jsonrpc": "2.0", "id": 1, "method": m, "params": p}))
    assert call("library.thumbnail", file_id=r.file_id)["result"]["path"].endswith(".png")
    parsed = call("query.from_url", url="heliad://get?start=2025-09-21T16:00:00&end=2025-09-21T17:00:00&wavelength=171")
    assert parsed["result"] == {"action": "get", "query": {"start": "2025-09-21T16:00:00",
                                                           "end": "2025-09-21T17:00:00", "wavelength": 171.0}}
    assert call("query.from_url", url="heliad://nope?start=a&end=b")["error"]["code"] == -32602
