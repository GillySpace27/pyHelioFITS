"""A fake Studio for tests and for trying notebooks without Studio: the same endpoint file
and JSON-RPC as the real listener, over the bundled command list. `commands.invoke` records the
call and answers `{}` unless a handler is given. Destructive commands answer 'declined' unless
`confirm=True`, mirroring Studio's confirmation dialog."""

from __future__ import annotations

import base64
import io
import json
import secrets
import struct
import tempfile
import threading
import zlib
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import jsonschema
import numpy as np

from .studio import bundled_commands


def _png(path: Path, w: int = 8, h: int = 8) -> str:
    """A small valid grey PNG, so a notebook can open what the fake 'rendered'."""
    raw = b"".join(b"\x00" + bytes([(x * 255) // max(w - 1, 1) for x in range(w)]) for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return str(path)


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", ""))


class FakeStudio:
    def __init__(self, handlers: dict[str, Callable[[dict], dict]] | None = None, confirm: bool = True):
        self.tools = {t["name"]: t for t in bundled_commands()}
        self.layers: dict[int, dict] = {}
        self.view: dict = {"projection": "orthographic", "zoom": 1.0, "warp_lambda": 1.0}
        self.time: str | None = None
        self.workdir = Path(tempfile.mkdtemp(prefix="fake-studio-"))
        self.handlers = {**self._data_verbs(), **(handlers or {})}
        self.confirm = confirm
        self.calls: list[tuple[str, dict]] = []
        self.token = secrets.token_hex(16)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps(outer._answer(req)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _data_verbs(self) -> dict[str, Callable[[dict], dict]]:
        """Studio's data commands (DataCommands in Studio, docs/commands.json) over synthetic
        frames: a layer has one frame per cadence step; a frame is a smooth disc plus a bright blob
        moving outward with time, so a notebook measuring it gets a sensible answer. Nothing here
        is real data. The answers have the shape Studio's have."""
        def layer_add(a):
            start, end = _t(a["start"]), _t(a["end"])
            step = float(a.get("cadence_s") or 720)
            n = int((end - start).total_seconds() // step) + 1
            lid = max(self.layers, default=0) + 1
            name = (a.get("observatory", "") + " " + a["dataset"]).strip()
            self.layers[lid] = {"layer_id": lid, "name": name, "source": "jp2" if a["source"] == "jp2" else "fits",
                                "filter": "none", "current": 0,
                                "times": [(start + timedelta(seconds=step * i)).isoformat(timespec="milliseconds")
                                          for i in range(n)]}
            return describe(self.layers[lid])

        def describe(L):
            return {"layer_id": L["layer_id"], "name": L["name"], "source": L["source"], "frames": len(L["times"]),
                    "loading": False, "active": L is list(self.layers.values())[-1], "filter": L["filter"],
                    "start": L["times"][0], "end": L["times"][-1], "frame": L["current"]}

        def layer_list(a):
            return {"layers": [describe(L) for L in self.layers.values()]}

        def layer_remove(a):
            self.layers.pop(a["layer_id"])
            return {}

        def need(a):
            if a["layer_id"] not in self.layers:
                raise KeyError(f"no layer {a['layer_id']}")
            return self.layers[a["layer_id"]]

        def filter_set(a):
            need(a)["filter"] = a["filter"]
            return {}

        def view_set(a):
            self.view.update(a)
            return {}

        def frame_get(a):
            L = need(a)
            i = a.get("frame", L["current"])
            if not 0 <= i < len(L["times"]):
                raise ValueError(f"frame {i} outside 0..{len(L['times']) - 1}")
            n = 64
            yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
            r = np.hypot(xx - n / 2, yy - n / 2)
            data = np.exp(-(r / 20) ** 2) + 3 * np.exp(-((r - (6 + 1.5 * i)) / 1.5) ** 2) * (xx > n / 2)
            data = data.astype(np.float32)
            grid = np.linspace(0, n - 1, 9)
            arcsec = (grid - n / 2) * 30.0
            dump = {"format": "hf-decode-dump/1", "writer": {"name": "FakeStudio"}, "plane": 0, "shape": [n, n],
                    "orientation": "fits", "bad_pixels": "nan", "unit": "DN", "corrections": [], "notes": ["synthetic"],
                    "instrument": "other", "time": {"keyword": "DATE-OBS", "iso": L["times"][i]},
                    "wcs": {"grid_pixels": [[float(x), float(y)] for y in grid for x in grid],
                            "grid_arcsec": [[float(x), float(y)] for y in arcsec for x in arcsec]},
                    "data": {"b64": base64.b64encode(_npy(data)).decode(), "encoding": "npy-float32-le"}}
            path = self.workdir / f"frame-{a['layer_id']}-{i}.json"
            path.write_text(json.dumps(dump), "utf-8")
            return {"path": str(path), "format": "hf-decode-dump/1", "frame": i, "time": L["times"][i]}

        def preview(a):
            side = a.get("long_side", 8)
            return {"path": _png(self.workdir / "preview.png", side, side)}

        def still(a):
            side = a.get("long_side", 8)
            return {"path": _png(Path(a["path"]), side, side)}

        def movie(a):
            frames = max((len(L["times"]) for L in self.layers.values()), default=0)
            Path(a["path"]).write_bytes(b"fake movie")
            return {"path": a["path"], "frames": frames}

        def seek_time(a):
            self.time = a["time"]
            return {}

        def seek_frame(a):
            for L in self.layers.values():
                L["current"] = min(a["frame"], len(L["times"]) - 1)
            return {}

        return {"layerAdd": layer_add, "layerList": layer_list, "layerRemove": layer_remove,
                "layerReload": lambda a: describe(need(a)), "filterSet": filter_set,
                "viewSet": view_set, "viewGet": lambda a: dict(self.view), "frameGet": frame_get,
                "previewRender": preview, "exportStill": still, "exportMovie": movie,
                "seekTime": seek_time, "seekFrame": seek_frame}

    def _answer(self, req: dict) -> dict:
        rid, params = req.get("id"), req.get("params") or {}

        def err(kind, msg):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": msg, "data": {"kind": kind}}}

        if req["method"] == "commands.list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": list(self.tools.values())}}
        if req["method"] != "commands.invoke":
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such method"}}
        name, args = params["id"], params.get("args") or {}
        tool = self.tools.get(name)
        if tool is None:
            return err("unknown-command", f"no command '{name}'")
        try:
            jsonschema.validate(args, tool["inputSchema"])
        except jsonschema.ValidationError as e:
            return err("invalid-arguments", e.message)
        if tool.get("x-effect") == "destructive" and not self.confirm:
            return err("declined", f"'{name}' was declined in the confirmation dialog")
        self.calls.append((name, args))
        try:
            result: Any = self.handlers.get(name, lambda a: {})(args)
        except (KeyError, ValueError) as e:
            return err("failed", str(e.args[0]) if isinstance(e, KeyError) else str(e))
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def endpoint_file(self, path: Path) -> Path:
        path.write_text(json.dumps({"port": self.port, "token": self.token, "pid": 0}), "utf-8")
        return path

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


def _npy(a: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, a.astype("<f4"), allow_pickle=False)
    return buf.getvalue()


def main() -> None:
    """`python -m heliofits.testing`: run a fake Studio and print how to point notebooks at it."""
    import tempfile
    import time
    with FakeStudio() as f:
        ep = f.endpoint_file(Path(tempfile.mkdtemp()) / "api.json")
        print(f"Fake Studio (synthetic frames, nothing real). In another terminal:\n  export HFSTUDIO_API={ep}")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
