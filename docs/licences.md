# Licences of the Python packages HelioFITS Core installs

What `pip install heliad[archives]` (the package now named heliofits-core) brought into a clean virtual environment on 2026-10-18
(Python 3, Linux), as reported by pip-licenses from each package's metadata. This is an
inventory for deciding what the Mac app can bundle, not legal advice.

Summary: everything is permissive (BSD, MIT, Apache, PSF) except two MPL-2.0 packages,
and one package whose wheel declares no licence.

- **MPL-2.0**: `certifi` and part of `tqdm`. MPL is file-level copyleft: bundling them
  unmodified is fine, and changes to their own files would have to be published.
- **drms** declares no licence in its wheel; check the upstream repository before bundling.
- Nothing found is GPL or LGPL.

| package | version | licence |
| --- | --- | --- |
| aioftp | 0.28.3 | Apache-2.0 |
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache Software License |
| astropy | 8.0.2 | BSD-3-Clause |
| astropy-iers-data | 0.2026.10.12.1.3.27 | BSD-3-Clause |
| attrs | 26.1.0 | MIT |
| beautifulsoup4 | 4.15.0 | MIT License |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) |
| charset-normalizer | 3.5.2 | MIT |
| drms | 0.9.1 | not declared in the installed wheel (confirm in the sunpy/drms repository before bundling) |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| isodate | 0.7.2 | BSD License |
| lxml | 6.1.3 | BSD-3-Clause |
| multidict | 7.1.0 | Apache-2.0 |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.6 | BSD License |
| parfive | 2.3.1 | MIT (from the LICENSE file; the metadata field holds only the copyright line) |
| platformdirs | 4.13.4 | MIT |
| propcache | 0.5.4 | Apache-2.0 |
| pyerfa | 2.0.1.5 | BSD License |
| python-dateutil | 2.9.0.post0 | Apache Software License; BSD License |
| PyYAML | 6.0.3 | MIT License |
| requests | 2.34.2 | Apache Software License |
| requests-file | 3.0.1 | Apache Software License |
| requests-toolbelt | 1.0.0 | Apache Software License |
| six | 1.17.0 | MIT License |
| soupsieve | 3.0.3 | MIT |
| sunpy | 8.0.1 | BSD-3-Clause |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| yarl | 1.25.1 | Apache-2.0 |
| zeep | 4.3.3 | MIT License |

Not in this list: Python itself (PSF licence) and the SQLite library it links
(public domain), which a bundled app also ships.

Regenerate with:

    python -m venv /tmp/lic && /tmp/lic/bin/pip install -e '.[archives]' pip-licenses
    /tmp/lic/bin/pip-licenses --from=mixed --ignore-packages pip-licenses prettytable wcwidth heliad pip setuptools
