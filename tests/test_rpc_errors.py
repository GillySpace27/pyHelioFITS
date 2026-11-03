"""HelioFITS Core's own error codes and the renamed methods. A client can act on a code
without reading the message: not found, read-only, archive, locked."""

import io
import json

import pytest

from heliofits.core import rpc, sources
from heliofits.core.library import Library
from heliofits.core.rpc import Server
from tests.conftest import aia_header, write_fits


@pytest.fixture
def call(lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header()))
    server = Server(lib, out=io.StringIO())

    def call(method, **params):
        return server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    call.server = server
    return call


def test_what_the_library_does_not_have_is_not_found(call):
    for method, params in [
        ("library.refetch", {"observation_id": 999}),
        ("dataset.query", {"dataset_key": "nothing/here"}),
        ("dataset.upgrade", {"dataset_key": "nothing/here"}),
        ("library.reject", {"observation_ids": [999], "reason": "x"}),
        ("download.cancel", {"job_id": 999}),
        ("download.enqueue", {"query": {"start": "2025-09-21T16:00:00", "end": "2025-09-21T17:00:00"},
                              "source": "nowhere"}),
    ]:
        err = call(method, **params)["error"]
        assert err["code"] == rpc.NOT_FOUND, method
        assert "999" in err["message"] or "nothing/here" in err["message"] or "nowhere" in err["message"]
        assert "data" not in err  # not a crash: no traceback


def test_a_param_the_core_cannot_use_is_invalid_params(call):
    assert call("library.reject", observation_ids=[1], reason="")["error"]["code"] == rpc.INVALID_PARAMS
    assert call("library.coverage", bins=0)["error"]["code"] == rpc.INVALID_PARAMS
    assert call("library.coverage", start="2025-09-21T17:00:00", end="2025-09-21T16:00:00")["error"]["code"] \
        == rpc.INVALID_PARAMS


def test_a_write_asked_of_a_read_only_core_is_read_only(lib, src):
    lib.ingest(write_fits(src / "a.fits", aia_header()))
    with lib:
        pass
    with Library.open(lib.root, readonly=True) as ro:
        server = Server(ro, out=io.StringIO())
        def call(method, **params):
            return server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
        assert call("download.enqueue", query={"start": "a", "end": "b"})["error"]["code"] == rpc.READ_ONLY
        assert call("library.reject", observation_ids=[1], reason="x")["error"]["code"] == rpc.READ_ONLY
        assert call("library.stats")["result"]["files"] == 1


def test_an_archive_that_cannot_be_asked_is_archive(call, monkeypatch):
    class Down:
        def search(self, query, *, settings=None):
            raise sources.ArchiveError("archive search failed: ConnectionError: no route")
    call.server.jobs._modules["fido"] = Down()
    err = call("dataset.cadences", dataset_key="sdo/aia/-/-/1/193")
    assert err is None  # answered in the background
    call.server._pool.shutdown(wait=True)
    reply = json.loads(call.server.out.getvalue().strip().splitlines()[-1])
    assert reply["error"]["code"] == rpc.ARCHIVE and "no route" in reply["error"]["message"]


def test_jsoc_without_an_email_is_archive():
    from heliofits.core.sources import jsoc
    with pytest.raises(sources.ArchiveError):
        jsoc._client({})


def test_the_old_names_answer_as_the_new_ones(call):
    url = "hfcore://get?start=2025-09-21T16:00:00&end=2025-09-21T17:00:00&wavelength=171"
    new, old = call("link.parse", url=url)["result"], call("query.from_url", url=url)["result"]
    assert new == old == {"action": "get", "query": {"start": "2025-09-21T16:00:00", "end": "2025-09-21T17:00:00",
                                                      "wavelength": 171.0}}
    uid = call("library.datasets")["result"][0]["dataset_uid"]
    link = f"hfcore://dataset/{uid}?start=2025-09-21T16:00:00"
    new, old = call("link.parse", url=link)["result"], call("dataset.from_url", url=link)["result"]
    assert new == old
    assert new["action"] == "dataset" and new["dataset_uid"] == uid and new["end"] is None
    assert new["dataset"]["dataset_key"] == "sdo/aia/-/-/1/193"
    assert call("link.parse", url="hfcore://dataset/")["error"]["code"] == rpc.INVALID_PARAMS
    assert call("link.parse", url="https://example.org")["error"]["code"] == rpc.INVALID_PARAMS
    hello = call("core.hello")["result"]
    assert hello["aliases"] == rpc.ALIASES
    assert {"search", "archive.search", "link.parse", "query.from_url"} <= set(hello["methods"])
    # The alias of a background method answers in the background too.
    assert call("search", query={"start": "a", "end": "b"}) is None
    call.server._pool.shutdown(wait=True)
