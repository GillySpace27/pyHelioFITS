"""Deterministic synthetic pictures for the golden set (integer arithmetic only)."""
import numpy as np

CASES = {
    "gradient8_rgb": {"shape": (40, 56, 3), "bit_depth": 8, "kind": "gradient"},
    "checker8_rgba": {"shape": (32, 48, 4), "bit_depth": 8, "kind": "checker"},
    "ramp16_gray": {"shape": (36, 64, 1), "bit_depth": 16, "kind": "ramp"},
    "disk16_rgb": {"shape": (64, 64, 3), "bit_depth": 16, "kind": "disk"},
}


def render(spec: dict) -> np.ndarray:
    h, w, c = spec["shape"]
    top = 255 if spec["bit_depth"] == 8 else 65535
    dt = np.uint8 if spec["bit_depth"] == 8 else np.uint16
    y, x = np.mgrid[0:h, 0:w].astype(np.int64)
    kind = spec["kind"]
    chans = []
    for k in range(c):
        if kind == "gradient":
            v = (x * (k + 2) * 5 + y * (3 - k) * 3) % (top + 1)
        elif kind == "checker":
            v = np.where(((x // 4) + (y // 4) + k) % 2 == 0, top, (x * y + k * 40) % (top + 1))
        elif kind == "ramp":
            v = (x * top) // (w - 1)
        else:  # disk
            r2 = (x - w // 2) ** 2 + (y - h // 2) ** 2
            v = np.where(r2 < (h // 3) ** 2, top - r2 * (k + 1) * (top // 4096 + 1), (x + y * (k + 1)) * (top // 160))
            v = np.clip(v, 0, top)
        chans.append(v)
    return np.stack(chans, axis=-1).astype(dt)


def write_png16(path, arr):
    """A non-interlaced 16-bit grey/RGB/RGBA PNG (reproduce.write_png is 8-bit only)."""
    import struct
    import zlib
    h, w, c = arr.shape
    deep = arr.astype(">u2")
    rows = b"".join(b"\x00" + deep[y].tobytes() for y in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, {1: 0, 3: 2, 4: 6}[c], 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))
    return path


def write(path, spec):
    from pyheliofits import reproduce
    arr = render(spec)
    return (reproduce.write_png if spec["bit_depth"] == 8 else write_png16)(path, arr)
