"""The API's three descriptions agree: the methods Server answers, the API table that
`hfcore api` prints, and docs/api.md. A method added without its docs fails here."""

import argparse
import inspect
import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from heliofits.core import coverage, rpc, views
from heliofits.core.rpc import API, NOTIFICATIONS, Server

DOCS = (Path(__file__).resolve().parents[1] / "docs" / "api.md").read_text(encoding="utf-8")
SRC = Path(__file__).resolve().parents[1] / "src"
# Methods that pass **kw through, and the function whose params they really take (after lib).
PASSED_THROUGH = {"library.coverage": coverage.library, "view.create": views.create}


def _params(fn, skip_first=False) -> str:
    out = []
    params = list(inspect.signature(fn).parameters.values())[1 if skip_first else 0:]
    for p in params:
        if p.kind is p.VAR_KEYWORD:
            continue
        out.append(p.name if p.default is p.empty else f"{p.name}={p.default!r}")
    return ", ".join(out)


def _section(name: str) -> str:
    match = re.search(rf"^### `{re.escape(name)}`\n(.*?)(?=^##)", DOCS, re.S | re.M)
    assert match, f"docs/api.md has no section for {name}"
    return match.group(1)


@pytest.fixture
def methods(lib):
    return Server(lib, out=io.StringIO()).methods


def test_every_method_is_in_the_table_and_nothing_else(methods):
    assert set(API) == set(methods)


def test_the_table_names_each_methods_real_params(methods):
    for name, fn in methods.items():
        expected = _params(PASSED_THROUGH[name], skip_first=True) if name in PASSED_THROUGH else _params(fn)
        assert API[name][0] == expected, name


def test_every_method_and_notification_has_a_docs_section_naming_its_params():
    for name, (params, _) in {**API, **NOTIFICATIONS}.items():
        section = _section(name)
        for param in re.findall(r"(\w+)(?:=|,|$)", params):
            assert f"`{param}" in section or param in section, f"{name}: docs do not mention {param}"


def test_every_alias_answers_as_its_method(methods):
    for old, new in rpc.ALIASES.items():
        assert methods[old] is methods[new], old
        assert API[old][0] == API[new][0], old
        assert new in API[old][1]


def _cli_commands() -> set[str]:
    from heliofits.core.cli import build_parser
    (sub,) = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return set(sub.choices)


def test_every_command_is_in_the_cli_table_and_names_real_methods():
    table = DOCS[DOCS.index("## The command line and the API"):]
    rows = re.findall(r"^\| `hfcore ([a-z-]+)[^|]*\| (.*) \|$", table, re.M)
    listed = {cmd for cmd, _ in rows}
    assert _cli_commands() == listed
    for cmd, methods in rows:
        for name in re.findall(r"`([a-z_]+\.[a-z_]+)`", methods):
            assert name in API and name not in rpc.ALIASES, f"{cmd}: {name}"


def test_every_notification_the_core_sends_is_listed():
    sent = set()
    for path in SRC.joinpath("heliofits", "core").rglob("*.py"):
        sent |= set(re.findall(r'notify\("([a-z_.]+)"', path.read_text(encoding="utf-8")))
    assert sent == set(NOTIFICATIONS)


# No PATH, so nothing outside Python is found. Windows Python cannot start without SYSTEMROOT,
# and cannot find a home directory without USERPROFILE.
_BARE_ENV = {"PYTHONPATH": str(SRC), "PATH": "",
             **{k: os.environ[k] for k in ("SYSTEMROOT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH") if k in os.environ}}


def test_hfcore_api_prints_every_method():
    run = lambda *a: subprocess.run([sys.executable, "-m", "heliofits.core", "api", *a],  # noqa: E731
                                    capture_output=True, text=True,
                                    env=_BARE_ENV)
    listing = run()
    assert listing.returncode == 0
    for name in [*API, *NOTIFICATIONS]:
        assert f"  {name}(" in listing.stdout
    assert "docs/api.md" in listing.stdout
    one = run("ingest.path")
    assert one.returncode == 0 and one.stdout.startswith("ingest.path(path, source_url=None, query=None)")
    unknown = run("ingest.nothing")
    assert unknown.returncode == 2 and "hfcore api lists them" in unknown.stderr


def test_help_points_at_the_api():
    out = subprocess.run([sys.executable, "-m", "heliofits.core", "--help"], capture_output=True, text=True,
                         env=_BARE_ENV).stdout
    assert "hfcore api" in out and "docs/api.md" in out


def test_describe_refuses_an_unknown_name():
    with pytest.raises(KeyError):
        rpc.describe("nope")
