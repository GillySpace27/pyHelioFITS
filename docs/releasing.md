# Releasing pyheliofits to PyPI

This file is the runbook. The `deploy-pyheliofits` skill follows it; when the two disagree,
this file wins and the skill gets fixed.

One-time setup (PyPI account `DrGilly`, done 2026-11-08):

1. PyPI > Account settings > Publishing > trusted publisher: project `pyheliofits`, owner
   `GillySpace27`, repository `pyHelioFITS`, workflow `publish.yml`. The pending publisher
   was saved with environment "(Any)"; tighten it to `pypi` in the project's Publishing
   settings.
2. GitHub > repository Settings > Environments > `pypi`, with Gilly as required reviewer.
3. Zenodo > GitHub: switch the `pyHelioFITS` repository on. It must be on before a GitHub
   Release is published: a Release (not a tag) is what triggers the archive and the DOI.

Each release:

1. **Prepare on a PR.** Bump `version` in `pyproject.toml` (the core's `__version__` follows
   it) and in `CITATION.cff`; turn the `CHANGELOG.md` heading from "unreleased" to the
   release date. Merge to main with CI green on every platform.
2. **Check locally, uploading nothing**:

       ~/.claude/skills/deploy-pyheliofits/scripts/deploy.sh <V> --check
       ~/.claude/skills/deploy-pyheliofits/scripts/deploy.sh <V> --build-only

   Preflight (version on main, CI green, tag free, version not on PyPI), then a fresh clone,
   build, `twine check --strict`, wheel contents (only `pyheliofits`, its data files present,
   nothing secret), a clean-venv install and smoke test (imports, `hfcore --help` lists
   `layer-upgrade`, which HelioFITS Studio needs, `heliofits-mcp --help`, no `heliad`), and
   the sdist building a wheel on its own.
3. **TestPyPI: not set up yet (placeholder, Gilly 2026-11-08: "add it next time").** To do
   before the next release:
   - a TestPyPI account (test.pypi.org is a separate site with separate accounts), with a
     verified email and a passkey;
   - a pending trusted publisher on test.pypi.org: project `pyheliofits`, owner
     `GillySpace27`, repository `pyHelioFITS`, workflow `publish.yml`, environment `testpypi`;
   - a GitHub environment `testpypi` (no reviewer needed);
   - a `publish-testpypi` job in `publish.yml` after `build` and before `publish`, using
     `pypa/gh-action-pypi-publish` with `repository-url: https://test.pypi.org/legacy/`, and
     `publish` changed to need it;
   - a check that installs it back: dependencies from PyPI first, then
     `pip install --no-deps -i https://test.pypi.org/simple/ pyheliofits==<V>` (never
     `--extra-index-url` pointing at TestPyPI), and the same smoke test.
   Until then this step is skipped.
4. **Tag and push** the merged commit: `git tag -a v<V> -m "pyheliofits <V>" && git push origin v<V>`.
   The Publish workflow checks the tag matches the version, builds, runs `twine check`,
   installs the wheel, then waits on the `pypi` environment.
5. **Approve** it in the run's "Review deployments". The upload cannot be undone; a version
   number can never be reused, even after deleting it.
6. **Verify from PyPI** in a clean venv: download both files and check them against PyPI's
   published SHA-256, install with dependencies from PyPI, run the smoke test from step 2,
   and `pip install pyheliofits==<V>` in a second clean venv. A new project can take a few
   minutes to appear; retry before calling it a failure.
7. **GitHub Release**, only after Zenodo is switched on (setup 3):
   `gh release create v<V> --verify-tag --title "pyheliofits <V>"` with the two files
   downloaded from PyPI attached, so the Release holds the bytes PyPI serves. After Zenodo
   mints the DOIs, add the version DOI and the concept DOI to the README badge and
   `CITATION.cff` (`doi:`); never write one before Zenodo has minted it.

Dry run without publishing: Actions > Publish > Run workflow (builds and checks only).

## What has gone wrong

- **0.1.0 (2026-11-08)**: the first account (`GillySpace27`) could not upload: PyPI refuses
  uploads from an account without a verified primary email, and its email was unreachable.
  A new account (`DrGilly`) with trusted publishing replaced it. The 0.1.0 tag went out
  before the CHANGELOG heading was dated, so the 0.1.0 sdist says "unreleased"; the date
  was added on main afterwards. Step 1 is now first for that reason.
