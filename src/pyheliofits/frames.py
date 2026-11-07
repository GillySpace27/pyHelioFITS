"""Read an `hf-decode-dump/1` document (what Studio's `frameGet` returns; docs/decode-parity.md)."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass
class Frame:
    data: np.ndarray          # float32, row 0 = lowest y (FITS order), NaN for bad pixels
    time: datetime            # UTC, the observation time Studio reads
    unit: str
    grid_pixels: np.ndarray   # (n, 2) zero-based pixel centres (x = column, y = row)
    grid_arcsec: np.ndarray   # (n, 2) helioprojective (Tx, Ty) in arcsec at those pixels
    doc: dict                 # the whole document

    def arcsec_per_pixel(self) -> float:
        """Mean plate scale from the dump's own grid (a straight-line fit, so not for strong distortion)."""
        px, sky = self.grid_pixels, self.grid_arcsec
        return float(abs(np.polyfit(px[:, 0], sky[:, 0], 1)[0]))


def load_dump(path: str | Path) -> Frame:
    path = Path(path)
    doc = json.loads(path.read_text("utf-8"))
    if doc.get("format") != "hf-decode-dump/1":
        raise ValueError(f"{path} is not an hf-decode-dump/1 document")
    d = doc["data"]
    raw = base64.b64decode(d["b64"]) if "b64" in d else (path.parent / d["file"]).read_bytes()
    data = np.load(io.BytesIO(raw), allow_pickle=False).astype(np.float32)
    w = doc["wcs"]
    return Frame(data, datetime.fromisoformat(doc["time"]["iso"].replace("Z", "")), doc.get("unit", ""),
                 np.asarray(w["grid_pixels"], float), np.asarray(w["grid_arcsec"], float), doc)
