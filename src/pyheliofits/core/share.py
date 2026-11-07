"""One writer, several clients.

The core that holds a library's lock is its only writer (library.py). When a
second client starts `hfcore serve` on the same library (Studio while the Mac
app is open, say), that core cannot take the lock, so instead of failing it
becomes a relay: it connects to the writer over a local socket and passes its
client's lines through unchanged. The writer answers each client on its own
connection and sends every notification (download and ingest progress) to all
of them, so a download started from Studio shows in the Mac app too.

The writer listens on 127.0.0.1 at a port it picks, and writes the port and a
random token to <root>/.heliad.endpoint, readable only by its owner (on POSIX).
A connection whose first line is not that token is closed unanswered; one that
has it gets "ok" and is then spoken to in JSON-RPC like stdin and stdout. When
the writer's own client goes away, it keeps serving until the last relay has
disconnected.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time
from concurrent.futures import wait
from pathlib import Path
from typing import TextIO

from .rpc import encode

ENDPOINT = ".heliad.endpoint"  # pre-rename name kept on purpose, with .heliad.lock: see library._RootLock
WAIT_FOR_WRITER = 5.0  # seconds a relay waits for a writer that has the lock but no endpoint yet


class WriterUnavailable(RuntimeError):
    pass


class _Client:
    """One relay's connection, as the writer sees it."""

    def __init__(self, conn: socket.socket):
        self.conn = conn
        self._lock = threading.Lock()
        self.alive = True

    def send(self, message: dict) -> None:
        self.send_line(encode(message))

    def send_line(self, line: str) -> None:
        with self._lock:
            if not self.alive:
                return
            try:
                self.conn.sendall((line + "\n").encode("utf-8"))
            except OSError:
                self.alive = False


class Hub:
    """The writer's side: accepts relays and hands their requests to the Server."""

    def __init__(self, server, root: Path):
        self.server = server
        self.path = Path(root) / ENDPOINT
        self.token = secrets.token_hex(32)
        self._sock = socket.create_server(("127.0.0.1", 0))
        self._sock.settimeout(0.2)  # so the accept loop notices close() on every platform
        self._clients: set[_Client] = set()
        self._changed = threading.Condition()
        self._closed = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        port = self._sock.getsockname()[1]
        _write_private(self.path, json.dumps({"port": port, "token": self.token, "pid": os.getpid()}))
        self._thread = threading.Thread(target=self._accept, name="hfcore-share", daemon=True)
        self._thread.start()

    def broadcast(self, message: dict) -> None:
        line = encode(message)
        with self._changed:
            clients = list(self._clients)
        for client in clients:
            client.send_line(line)

    def wait_until_idle(self) -> None:
        with self._changed:
            while self._clients:
                self._changed.wait()

    def close(self) -> None:
        with self._changed:
            self._closed = True
        if self._thread is not None:
            self._thread.join()
        self._sock.close()
        try:  # leave a newer writer's endpoint alone
            if json.loads(self.path.read_text(encoding="utf-8")).get("token") == self.token:
                self.path.unlink()
        except (OSError, ValueError):
            pass

    def _accept(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve_client, args=(conn,), name="hfcore-relay",
                             daemon=True).start()

    def _serve_client(self, conn: socket.socket) -> None:
        with conn, conn.makefile("rb") as rfile:
            conn.settimeout(10)
            try:
                first = rfile.readline().strip()
            except OSError:
                return
            if not hmac.compare_digest(first, self.token.encode()):
                return
            conn.settimeout(None)
            client = _Client(conn)
            with self._changed:
                if self._closed:
                    return
                client.send_line("ok")  # before any broadcast can reach this client
                self._clients.add(client)
            pending = []
            try:
                for raw in rfile:
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        reply = self.server.handle_line(line, reply=client.send, pending=pending)
                        if reply is not None:
                            client.send(reply)
            except OSError:
                pass
            finally:
                wait(pending)  # answer what was asked before the relay's input ended
                with self._changed:
                    self._clients.discard(client)
                    self._changed.notify_all()


def connect(root: Path, wait_s: float = WAIT_FOR_WRITER) -> socket.socket:
    """A connection to the writer of the library at root, past the token check."""
    path = Path(root) / ENDPOINT
    deadline = time.monotonic() + wait_s
    while True:
        try:
            endpoint = json.loads(path.read_text(encoding="utf-8"))
            conn = socket.create_connection(("127.0.0.1", int(endpoint["port"])), timeout=5)
            try:
                conn.sendall((endpoint["token"] + "\n").encode())
                if _handshake_reply(conn) == b"ok":
                    conn.settimeout(None)
                    return conn
            except OSError:
                pass
            conn.close()
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() > deadline:
            raise WriterUnavailable(f"another HelioFITS Core process is writing to {root} and is not serving it")
        time.sleep(0.1)


def relay(root: Path, inp: TextIO = sys.stdin, out: TextIO = sys.stdout) -> int:
    """Pass lines between this process's client and the library's writer until either
    side ends. 0 when the client ended, 1 when the writer went away first."""
    conn = connect(root)
    input_ended = threading.Event()

    def forward() -> None:
        try:
            for line in inp:
                conn.sendall(line.encode("utf-8"))
        except OSError:
            pass
        finally:
            input_ended.set()
            try:
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    threading.Thread(target=forward, name="hfcore-relay-in", daemon=True).start()
    with conn:
        try:
            for raw in conn.makefile("rb"):
                out.write(raw.decode("utf-8", "replace"))
                out.flush()
        except (OSError, ValueError):
            return 0 if input_ended.is_set() else 1
    if input_ended.is_set():
        return 0
    print("hfcore: the library's writer stopped", file=sys.stderr)
    return 1


def _handshake_reply(conn: socket.socket) -> bytes:
    """The writer's one-line answer, read a byte at a time so nothing after it is lost."""
    line = b""
    while len(line) < 64:
        byte = conn.recv(1)
        if not byte or byte == b"\n":
            break
        line += byte
    return line.strip()


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)  # in case the file was already there with wider permissions
    os.replace(tmp, path)
