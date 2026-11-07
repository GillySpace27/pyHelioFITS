#!/usr/bin/env python3
"""Remove what a bundled Python never runs: test suites, pip, IDLE and C headers.

    python3 app/tools/trim_python.py <python dir>

Run after HelioFITS Core is installed, by bundle_python.sh (the Mac) and by HelioFITS Studio's
release/bundle_core.py (Windows, Linux), so every package is trimmed the same way. Saves about
50 MB of the 216 MB bundle (measured 2026-11-07, macOS arm64). Standard library only.
"""
import shutil
import sys
from pathlib import Path



def imported_at_import(sp: Path, rel) -> bool:
    """A top-level package that imports its own tests on import (astropy's TestRunner, sunpy's
    self_test) keeps that folder."""
    if len(rel) != 2:
        return False
    init = sp / rel[0] / "__init__.py"
    return init.is_file() and (".tests" in init.read_text(errors="ignore"))

# Paths relative to the stdlib folder (lib/python3.x, or Lib on Windows).
STDLIB_DROP = ["ensurepip", "idlelib", "turtledemo", "lib2to3", "test", "site-packages/pip"]


def trim(root: Path) -> int:
    lib = next((p for p in [*root.glob("lib/python3.*"), root / "Lib"] if (p / "site-packages").is_dir()), None)
    if lib is None:
        sys.exit(f"!! no lib/python3.x/site-packages under {root}")
    gone = []
    sp = lib / "site-packages"
    for d in sorted(sp.rglob("tests")):
        rel = d.relative_to(sp).parts
        if not d.is_dir() or imported_at_import(sp, rel) or any(g in d.parents for g in gone):
            continue
        gone.append(d)
    gone += [lib / p for p in STDLIB_DROP if (lib / p).exists()]
    gone += [d for d in (lib / "site-packages").glob("pip-*.dist-info")]
    gone += [p for p in [root / "include", *root.glob("bin/pip*"), *root.glob("bin/idle*"), *root.glob("Scripts/pip*")] if p.exists()]
    freed = 0
    for p in gone:
        if p.is_dir():
            freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
            shutil.rmtree(p)
        else:
            freed += p.lstat().st_size
            p.unlink()
    return freed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(f"trimmed {trim(Path(sys.argv[1])) / 1048576:.0f} MB from {sys.argv[1]}")
