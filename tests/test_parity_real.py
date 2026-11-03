"""Decode parity on REAL files: Studio's dump of each fixture against sunpy's reading of the same file.

Skipped unless HFPARITY_DIR names a folder of Studio decode dumps (``*.hfdump.json``, written by
Studio's DecodeParityDump check on the Mac; see docs/decode-parity.md). Each dump names its source
file by SHA-256; the file is found under HFPARITY_SOURCES (a path list, default: the library the
Core would open) by that hash. A dump whose source cannot be found is a failure, not a skip: a
parity claim needs the file.

tests/parity/manifest.json lists the fixture each instrument must have; the coverage test reports
the instruments that have no dump yet, so a missing one is visible rather than silent.
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("sunpy.map", reason="decode parity needs sunpy")

from heliofits.core import parity  # noqa: E402

DUMP_DIR = os.environ.get("HFPARITY_DIR")
MANIFEST = json.loads((Path(__file__).parent / "parity" / "manifest.json").read_text())

pytestmark = pytest.mark.skipif(not DUMP_DIR, reason="HFPARITY_DIR not set: no Studio decode dumps to compare")


def _dumps():
    return sorted(Path(DUMP_DIR).glob("*.hfdump.json")) if DUMP_DIR else []


def _find_source(sha: str, name: str) -> Path | None:
    roots = [Path(p) for p in os.environ.get("HFPARITY_SOURCES", "").split(os.pathsep) if p]
    for root in roots:
        for cand in root.rglob(name):
            if parity.sha256_of(cand) == sha:
                return cand
    return None


@pytest.mark.parametrize("dump_path", _dumps() or [None], ids=lambda p: p.name if p else "no-dumps")
def test_studio_dump_matches_sunpy(dump_path):
    if dump_path is None:
        pytest.skip("HFPARITY_DIR holds no *.hfdump.json")
    studio = parity.read_dump(dump_path)
    src = _find_source(studio.source["sha256"], studio.source["name"])
    assert src is not None, f"source {studio.source['name']} (sha256 {studio.source['sha256'][:12]}) not under HFPARITY_SOURCES"
    ref = parity.reference_dump(src, studio.plane, like=studio)
    report = parity.compare_dumps(studio, ref)
    assert report.ok, report.text()


def test_every_instrument_has_a_dump():
    have = {parity.read_dump(p).instrument for p in _dumps()}
    missing = [i for i in MANIFEST["instruments"] if i not in have]
    assert not missing, f"no Studio dump yet for: {', '.join(missing)}"
