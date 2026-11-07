"""Two clients on one library, as when Studio starts a core while the Mac app is open.

The first `hfcore serve` holds the library's lock and is its one writer. A second
one, finding the lock taken, relays its client's requests to the writer, so both
clients can ingest and download, and both hear every download's progress."""

import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import aia_header, write_fits
from test_blockers import QUERY, FakeSource

from pyheliofits.core import share
from pyheliofits.core.library import Library
from pyheliofits.core.rpc import Server

SRC = Path(__file__).resolve().parents[1] / "src"


class Core:
    """A `hfcore serve` process, spoken to over its stdin and stdout."""

    def __init__(self, root, home):
        env = {**os.environ, "HFCORE_HOME": str(home),
               "PYTHONPATH": os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")])}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pyheliofits.core", "--library", str(root), "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 0

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(json.loads(line))
        self.lines.put(None)

    def call(self, method, **params):
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id,
                                          "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = self.lines.get(timeout=30)
            assert msg is not None, f"core exited: {self.proc.stderr.read()}"
            if msg.get("id") == self.next_id:
                assert "error" not in msg, msg["error"]
                return msg["result"]

    def end_input(self):
        self.proc.stdin.close()

    def wait(self):
        code = self.proc.wait(timeout=30)
        self.stderr = self.proc.stderr.read()
        return code


@pytest.fixture
def home(tmp_path):
    return tmp_path / "appsupport"


def test_a_second_serve_relays_to_the_writer(tmp_path, home, src):
    root = tmp_path / "library"
    mac = Core(root, home)
    assert mac.call("core.hello")["readonly"] is False
    studio = Core(root, home)
    hello = studio.call("core.hello")
    assert hello["readonly"] is False
    assert hello["library_root"] == str(root.resolve())

    added = studio.call("ingest.path", path=str(write_fits(src / "a.fits", aia_header())))
    assert added["status"] == "added"
    assert mac.call("library.stats")["files"] == 1

    # The writer's own client leaves first; the writer keeps serving the relay.
    mac.end_input()
    time.sleep(0.5)
    assert mac.proc.poll() is None
    assert studio.call("library.stats")["files"] == 1
    studio.end_input()
    assert studio.wait() == 0, studio.stderr
    assert mac.wait() == 0, mac.stderr

    with Library.open(root):  # both have let go of the lock
        pass


def test_serve_says_why_when_the_writer_is_not_serving(tmp_path, home, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setenv("HFCORE_HOME", str(home))
    with Library.open(root):  # a writer that is not `hfcore serve`, like `hfcore import`
        core = Core(root, home)
        core.end_input()
        assert core.wait() == 3
    assert "is not serving it" in core.stderr
    assert "Traceback" not in core.stderr


class Collect(io.StringIO):
    def messages(self):
        return [json.loads(line) for line in self.getvalue().splitlines()]


def _serve_in_thread(lib):
    out = Collect()
    server = Server(lib, out=out)
    r, w = os.pipe()
    inp, feed = os.fdopen(r, "r"), os.fdopen(w, "w")
    thread = threading.Thread(target=server.serve, args=(inp,), kwargs={"shared": True}, daemon=True)
    thread.start()
    for _ in range(100):
        if (lib.root / share.ENDPOINT).exists():
            break
        time.sleep(0.05)
    return server, out, feed, thread


def _until(predicate, what):
    for _ in range(200):
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def test_both_clients_hear_a_download_from_either(lib, src):
    server, out, feed, thread = _serve_in_thread(lib)
    server.jobs._modules["fake"] = FakeSource(src, files=[("good.fits", aia_header(), False)])
    endpoint = json.loads((lib.root / share.ENDPOINT).read_text())
    if os.name == "posix":
        assert (lib.root / share.ENDPOINT).stat().st_mode & 0o777 == 0o600

    conn = share.connect(lib.root)
    lines = conn.makefile("r", encoding="utf-8")
    conn.sendall((json.dumps({"jsonrpc": "2.0", "id": 7, "method": "download.enqueue",
                              "params": {"query": json.loads(QUERY.to_json()), "source": "fake"}})
                  + "\n").encode())
    relayed = []
    while not relayed or relayed[-1].get("method") != "download.finished":
        relayed.append(json.loads(lines.readline()))
    (queued,) = [m for m in relayed if m.get("id") == 7]  # the job may report before this reply
    assert queued["result"]["status"] in ("queued", "active")  # the worker may already have it
    assert relayed[-1]["params"]["status"] == "done"
    _until(lambda: any(m.get("method") == "download.finished" for m in out.messages()),
           "the writer's own client to hear the download")
    assert lib.stats()["files"] == 1

    # A connection without the token is dropped before it can ask anything.
    port = endpoint["port"]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as stranger:
        stranger.sendall(b"not-the-token\n")
        stranger.sendall(b'{"jsonrpc":"2.0","id":1,"method":"library.stats"}\n')
        try:
            assert stranger.makefile("rb").read() == b""
        except (ConnectionResetError, ConnectionAbortedError):
            # Closed with our second line unread: macOS sends RST (ConnectionResetError), Windows
            # reports it as WinError 10053 (ConnectionAbortedError). Both are still "dropped".
            pass

    feed.close()
    time.sleep(0.3)
    assert thread.is_alive()  # the relay is still connected
    conn.shutdown(socket.SHUT_WR)
    assert lines.read() == ""
    conn.close()
    thread.join(timeout=10)
    assert not thread.is_alive()
