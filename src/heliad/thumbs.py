"""Small preview images of stored files, for the Mac app's lists and Studio's cache tab.

A thumbnail is a greyscale PNG at most `size` pixels on its long side, cached
under <library>/.thumbs by the file's SHA-256, so it is made once per file
and any reader can find it. It is a preview: an asinh stretch between the 1st
and 99.5th percentiles, with no colormap and no WCS. Written with zlib alone,
so no imaging library is needed.
"""

from __future__ import annotations

import os
import struct
import warnings
import zlib
from pathlib import Path

import numpy as np
from astropy.io import fits

DEFAULT_SIZE = 256


def image_data(path: Path) -> np.ndarray:
    """The first 2-D image in the file (the first plane of a cube)."""
    with fits.open(path, memmap=False) as hdul:
        for hdu in hdul:
            data = getattr(hdu, "data", None)
            if data is not None and getattr(data, "ndim", 0) >= 2:
                while data.ndim > 2:
                    data = data[0]
                return np.asarray(data, dtype=np.float32)
    raise ValueError(f"{Path(path).name} holds no image")


def downsample(data: np.ndarray, size: int) -> np.ndarray:
    """Block mean down to at most `size` on the long side, ignoring NaNs."""
    factor = max(1, -(-max(data.shape) // size))
    if factor == 1:
        return data
    ny, nx = (data.shape[0] // factor) * factor, (data.shape[1] // factor) * factor
    blocks = data[:ny, :nx].reshape(ny // factor, factor, nx // factor, factor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks stay NaN
        return np.nanmean(blocks, axis=(1, 3))


def scale(data: np.ndarray) -> np.ndarray:
    """8-bit greyscale, top row first (FITS puts row 1 at the bottom)."""
    finite = data[np.isfinite(data)]
    out = np.zeros(data.shape, dtype=np.uint8)
    if finite.size:
        lo, hi = np.percentile(finite, [1.0, 99.5])
        if hi > lo:
            with np.errstate(invalid="ignore"):
                v = np.clip((data - lo) / (hi - lo), 0.0, 1.0)
            v = np.arcsinh(10.0 * v) / np.arcsinh(10.0)
            out = np.where(np.isfinite(v), np.round(v * 255.0), 0).astype(np.uint8)
    return np.flipud(out)


def encode_png(gray: np.ndarray) -> bytes:
    """A greyscale 8-bit PNG (ISO/IEC 15948: IHDR colour type 0, filter 0 on every row)."""
    height, width = gray.shape

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + gray[y].tobytes() for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def thumb_path(root: Path, sha256: str, size: int = DEFAULT_SIZE) -> Path:
    suffix = "" if size == DEFAULT_SIZE else f"-{size}"
    return Path(root) / ".thumbs" / sha256[:2] / f"{sha256}{suffix}.png"


def ensure(root: Path, path: Path, sha256: str, size: int = DEFAULT_SIZE) -> Path:
    """The thumbnail for a stored file, rendering it on first request."""
    out = thumb_path(root, sha256, size)
    if out.exists():
        return out
    png = encode_png(scale(downsample(image_data(path), size)))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.part")
    tmp.write_bytes(png)
    os.replace(tmp, out)
    return out
