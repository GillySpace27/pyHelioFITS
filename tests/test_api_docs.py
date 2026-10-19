"""The API's three descriptions agree: the methods Server answers, the API table that
`heliad api` prints, and docs/api.md. A method added without its docs fails here."""

import inspect
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

from heliad import coverage, rpc, views
from heliad.rpc import API, NOTIFICATIONS, Server

DOCS = (Path(__file__).resolve().parents[1] / "docs" / "api.md").read_text()
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


def test_every_notification_the_core_sends_is_listed():
    sent = set()
    for path in SRC.joinpath("heliad").rglob("*.py"):
        sent |= set(re.findall(r'notify\("([a-z_.]+)"', path.read_text()))
    assert sent == set(NOTIFICATIONS)


def test_heliad_api_prints_every_method():
    run = lambda *a: subprocess.run([sys.executable, "-m", "heliad", "api", *a],  # noqa: E731
                                    capture_output=True, text=True,
                                    env={"PYTHONPATH": str(SRC), "PATH": ""})
    listing = run()
    assert listing.returncode == 0
    for name in [*API, *NOTIFICATIONS]:
        assert f"  {name}(" in listing.stdout
    assert "docs/api.md" in listing.stdout
    one = run("ingest.path")
    assert one.returncode == 0 and one.stdout.startswith("ingest.path(path, source_url=None, query=None)")
    unknown = run("ingest.nothing")
    assert unknown.returncode == 2 and "heliad api lists them" in unknown.stderr


def test_help_points_at_the_api():
    out = subprocess.run([sys.executable, "-m", "heliad", "--help"], capture_output=True, text=True,
                         env={"PYTHONPATH": str(SRC), "PATH": ""}).stdout
    assert "heliad api" in out and "docs/api.md" in out


def test_describe_refuses_an_unknown_name():
    with pytest.raises(KeyError):
        rpc.describe("nope")
