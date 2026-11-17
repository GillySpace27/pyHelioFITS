"""Regenerate the golden set (tests/golden/). Never overwrites: move the old files aside first.

    python tests/golden/make_golden.py

Pictures are synthetic and built from integer arithmetic only, so they are the same on every
platform and numpy version. Each gets a record (a copy of the example record with the output
block set) and a row in manifest.json holding the expected hfpix/1 digest. Changing a digest
here means the digest algorithm or the PNG reader changed: that is a format break, not a refresh.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from patterns import CASES, render, write  # noqa: E402

from pyheliofits import reproduce  # noqa: E402

example = json.loads((HERE.parent / "fixtures" / "example-studio-connect-still.hfrec.json").read_text("utf-8"))
manifest = {}
for name, spec in CASES.items():
    png = HERE / f"{name}.png"
    side = HERE / f"{name}.png.hfrec"
    if png.exists() or side.exists():
        sys.exit(f"{name}: already exists; move it aside first")
    arr = render(spec)
    write(png, spec)
    d = reproduce.digests(png)["pixel_sha256"]
    rec = json.loads(json.dumps(example))
    rec["hfrecord"] = "1.1"
    rec["output"].update({"file_name": png.name, "sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
                          "width": arr.shape[1], "height": arr.shape[0], "bit_depth": spec["bit_depth"],
                          "pixel_sha256": d})
    side.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    manifest[name] = {"pixel_sha256": d}
(HERE / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
