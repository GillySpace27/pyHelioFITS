# Releasing pyheliofits to PyPI

One-time setup (PyPI account `DrGilly`):

1. PyPI > Account settings > Publishing > add a pending trusted publisher: project `pyheliofits`, owner `GillySpace27`, repository `pyHelioFITS`, workflow `publish.yml`, environment `pypi`.
2. GitHub > repository Settings > Environments > create `pypi`, with yourself as required reviewer.

Each release:

1. Bump `version` in `pyproject.toml` (the core's `__version__` follows it) and in `CITATION.cff`, turn the `CHANGELOG.md` heading from "unreleased" to the release date, and merge to main with green CI.
2. Tag and push: `git tag v1.0.0 && git push origin v1.0.0`.
3. The Publish workflow builds the sdist and wheel, runs `twine check`, installs the wheel in a clean venv, then waits for your approval on the `pypi` environment.
4. Approve it. The upload cannot be undone; a version number can never be reused.
5. Check in a fresh venv: `pip install pyheliofits && hfcore --help`.

Dry run without publishing: Actions > Publish > Run workflow (builds and checks only).

Zenodo: in zenodo.org > GitHub, switch the `pyHelioFITS` repository on before the first release is published on GitHub (a GitHub Release, not only a tag, triggers the archive). `.zenodo.json` supplies the metadata. After the first archive, add the version DOI and the concept DOI to the README badge and `CITATION.cff` (`doi:`); never write one before Zenodo has minted it.
