"""Synthetic FITS files shaped like the archives HelioFITS Core meets.

The keyword sets are modelled on SDO/AIA level 1 and PUNCH level 3 files as
the vault describes them (FILEVRSN versions, TYPECODE/OBSCODE products); they
are test fixtures, not copies of real headers.
"""

import numpy as np
import pytest
from astropy.io import fits

from heliofits.core.library import Library


def write_fits(path, header: dict, compressed: bool = False):
    data = np.arange(64, dtype=np.float32).reshape(8, 8)
    if compressed:
        primary = fits.PrimaryHDU()
        ext = fits.CompImageHDU(data=data)
        for k, v in header.items():
            ext.header[k] = v
        fits.HDUList([primary, ext]).writeto(path)
    else:
        hdu = fits.PrimaryHDU(data=data)
        for k, v in header.items():
            hdu.header[k] = v
        hdu.writeto(path)
    return path


def aia_header(date="2025-09-21T16:00:04.84", wave=193, **extra):
    h = {
        "TELESCOP": "SDO/AIA", "INSTRUME": "AIA_3", "WAVELNTH": wave, "WAVEUNIT": "angstrom",
        "LVL_NUM": 1.0, "DATE-OBS": date, "TIMESYS": "UTC",
        "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "CUNIT1": "arcsec", "CUNIT2": "arcsec",
        "CDELT1": 0.6, "CDELT2": 0.6, "CROTA2": 0.0, "PC1_1": 1.0, "PC1_2": 0.0, "PC2_1": 0.0, "PC2_2": 1.0,
        "RSUN_REF": 696000000.0, "DSUN_OBS": 1.5e11, "RSUN_OBS": 957.07,
    }
    h.update(extra)
    return h


def punch_header(date="2025-09-21T17:00:00.000", version="0l", **extra):
    h = {
        "OBSRVTRY": "PUNCH", "INSTRUME": "WFI+NFI", "LEVEL": "3", "TYPECODE": "CT", "OBSCODE": "M",
        "FILEVRSN": version, "DATE-OBS": date, "TIMESYS": "UTC",
        "CTYPE1": "HPLN-ARC", "CTYPE2": "HPLT-ARC", "CUNIT1": "deg", "CUNIT2": "deg",
    }
    h.update(extra)
    return h


@pytest.fixture(autouse=True)
def _no_real_config(tmp_path, monkeypatch):
    """Every test's config folder is its own temp folder. A test that opened a Library with no
    HFCORE_HOME once wrote the machine's real config.json, pointing it at a pytest library
    (Gilly's Mac, 2026-10-25); a test that wants another folder sets HFCORE_HOME itself."""
    for name in ("HELIAD_HOME", "HELIAD_LIBRARY", "HELIAD_JSOC_EMAIL", "HFCORE_LIBRARY", "HFCORE_JSOC_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "appsupport"))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    for name in ("HELIAD_HOME", "HELIAD_LIBRARY", "HELIAD_JSOC_EMAIL", "HFCORE_LIBRARY"):
        monkeypatch.delenv(name, raising=False)  # nothing from the machine running the tests
    monkeypatch.setenv("HFCORE_HOME", str(tmp_path / "appsupport"))
    with Library.open(tmp_path / "library") as library:
        yield library


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    return d
