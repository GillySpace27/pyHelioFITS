"""Download jobs: queued in the index, run one at a time on a worker thread.

Enqueueing writes a jobs row and returns at once, so the RPC server keeps
answering while an archive fetch runs. Each job fetches into its own staging
folder (.staging/job-<id>) and ingests what arrived; a file cut off in
transit fails the truncation check, is recorded as a failed download and is
deleted, never stored. A job left active by a core that quit is queued again
when the next core starts, and its fetch resumes from what is already staged.

A cancel takes effect at the next phase boundary: a fetch already running
finishes its files first, then nothing from it is ingested.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Callable

from . import config, fitsmeta, sources
from .library import Library, is_fits, now
from .query import Query


class Cancelled(Exception):
    pass


class JobRunner:
    def __init__(self, lib: Library, notify: Callable[[str, dict], None] | None = None,
                 source_modules: dict | None = None):
        lib._require_writer()
        self.lib = lib
        self.notify = notify or (lambda method, params: None)
        self._modules = dict(source_modules or {})
        self._cancel: set[int] = set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()
        with lib._write_lock:
            lib.conn.execute("UPDATE jobs SET status = 'queued', phase = NULL, updated_at = ? "
                             "WHERE status = 'active'", (now(),))

    def _source(self, name: str):
        if name not in self._modules:
            self._modules[name] = sources.get(name)
        return self._modules[name]

    # ---- queue -------------------------------------------------------------

    def enqueue(self, query: Query, source: str = "fido") -> dict:
        if source not in sources.SOURCES and source not in self._modules:
            raise KeyError(f"unknown source {source!r}")
        lib = self.lib
        with lib._write_lock:
            lib.conn.execute("BEGIN IMMEDIATE")
            try:
                qid = lib._query_id(query, source)
                job_id = lib.conn.execute(
                    "INSERT INTO jobs(source, query_id, status, created_at, updated_at) "
                    "VALUES (?, ?, 'queued', ?, ?)", (source, qid, now(), now())).lastrowid
                lib.conn.execute("COMMIT")
            except BaseException:
                lib.conn.execute("ROLLBACK")
                raise
        self._wake.set()
        return self.get(job_id)

    def get(self, job_id: int) -> dict | None:
        rows = self.list(job_id=job_id)
        return rows[0] if rows else None

    def list(self, *, status: str | None = None, job_id: int | None = None, limit: int = 50) -> list[dict]:
        return list_jobs(self.lib, status=status, job_id=job_id, limit=limit)

    def cancel(self, job_id: int) -> dict:
        lib = self.lib
        with lib._write_lock:
            cur = lib.conn.execute(
                "UPDATE jobs SET status = 'cancelled', phase = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'queued'", (now(), job_id))
            if cur.rowcount:
                return {"job_id": job_id, "status": "cancelled"}
        job = self.get(job_id)
        if job is None:
            raise KeyError(f"no job {job_id}")
        if job["status"] != "active":
            return {"job_id": job_id, "status": job["status"]}
        self._cancel.add(job_id)
        return {"job_id": job_id, "status": "cancelling",
                "note": "a fetch already running finishes first; nothing from it is ingested"}

    def retry(self, job_id: int) -> dict:
        """Queue a failed job again; its fetch resumes from what is staged."""
        with self.lib._write_lock:
            cur = self.lib.conn.execute(
                "UPDATE jobs SET status = 'queued', error = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'failed'", (now(), job_id))
        if not cur.rowcount:
            raise ValueError(f"job {job_id} is not a failed job")
        self._wake.set()
        return self.get(job_id)

    # ---- running -----------------------------------------------------------

    def run_next(self) -> dict | None:
        """Run the oldest queued job to its end; None when the queue is empty."""
        with self._busy:
            with self.lib._write_lock:
                row = self.lib.conn.execute(
                    "SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            self._run(row["id"])
            # The worker thread reads through the writer connection, leaving the
            # reader to the thread answering requests.
            with self.lib._write_lock:
                return list_jobs(self.lib, job_id=row["id"], conn=self.lib.conn)[0]

    def run_pending(self) -> list[dict]:
        done = []
        while (job := self.run_next()) is not None:
            done.append(job)
        return done

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="heliad-jobs", daemon=True)
            self._thread.start()
            self._wake.set()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            self.run_pending()

    def _update(self, job_id: int, **cols) -> None:
        cols["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in cols)
        with self.lib._write_lock:
            self.lib.conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*cols.values(), job_id))

    def _run(self, job_id: int) -> None:
        lib = self.lib
        with lib._write_lock:
            job = lib.conn.execute(
                "SELECT j.*, q.query_json FROM jobs j JOIN queries q ON q.id = j.query_id "
                "WHERE j.id = ?", (job_id,)).fetchone()
            claimed = lib.conn.execute(
                "UPDATE jobs SET status = 'active', error = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'queued'", (now(), job_id)).rowcount
        if not claimed:  # cancelled between being picked and starting
            return
        query = Query.from_json(job["query_json"])
        staging = lib.cfg.staging_dir / f"job-{job_id}"
        staging.mkdir(parents=True, exist_ok=True)
        _drop_incomplete(staging)

        def phase(name: str, done: int | None = None, total: int | None = None) -> None:
            if job_id in self._cancel:
                raise Cancelled()
            cols = {"phase": name}
            if total is not None:
                cols["files_total"] = total
            if done is not None:
                cols["files_done"] = done
            self._update(job_id, **cols)
            self.notify("download.progress", {"job_id": job_id, "phase": name,
                                              "done": done, "total": total})

        try:
            fetched = self._source(job["source"]).fetch(
                query, staging, settings=config.load_settings(), phase=phase)
            paths = [Path(p) for p in fetched["paths"]]
            phase("ingesting", 0, len(paths))
            counts: dict[str, int] = {}
            for i, p in enumerate(paths, 1):
                status = self._ingest_one(job, query, p, fetched.get("urls", {}).get(p.name))
                counts[status] = counts.get(status, 0) + 1
                if i == len(paths) or i % 10 == 0:
                    phase("ingesting", i, len(paths))
            result = {"found": fetched.get("found"), "fetched": len(paths),
                      "errors": fetched.get("errors", []), "ingest": counts}
            self._update(job_id, status="done", phase=None, result_json=json.dumps(result))
            shutil.rmtree(staging, ignore_errors=True)
            self.notify("download.finished", {"job_id": job_id, "status": "done", "result": result})
        except Cancelled:
            self._cancel.discard(job_id)
            self._update(job_id, status="cancelled", phase=None)
            shutil.rmtree(staging, ignore_errors=True)
            self.notify("download.finished", {"job_id": job_id, "status": "cancelled"})
        except Exception as exc:
            # Staged files are kept, so running the job again resumes the fetch.
            error = f"{type(exc).__name__}: {exc}"
            self._update(job_id, status="failed", phase=None, error=error)
            self.notify("download.finished", {"job_id": job_id, "status": "failed", "error": error})

    def _ingest_one(self, job, query: Query, path: Path, url: str | None) -> str:
        lib = self.lib
        size = path.stat().st_size if path.exists() else None
        record = json.dumps({"name": path.name, "url": url}, sort_keys=True)
        try:
            r = lib.ingest(path, consume=True, query=query, source=job["source"], source_url=url)
            status, file_id, error, kind = "done", r.file_id, None, r.status
        except fitsmeta.IncompleteFile as exc:
            path.unlink(missing_ok=True)
            status, file_id, error, kind = "failed", None, str(exc), "incomplete"
        except Exception as exc:
            status, file_id, error, kind = "failed", None, f"{type(exc).__name__}: {exc}", "error"
        with lib._write_lock:
            lib.conn.execute(
                "INSERT INTO downloads(query_id, url, record_json, status, bytes_total, bytes_done, "
                "error, file_id, updated_at, job_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job["query_id"], url, record, status, size, size if status == "done" else 0,
                 error, file_id, now(), job["id"]))
        return kind


def _drop_incomplete(staging: Path) -> None:
    """Delete staged FITS files cut short by an earlier run, so the fetch gets them again."""
    for p in staging.iterdir():
        if p.is_file() and is_fits(p):
            try:
                fitsmeta.check_complete(p)
            except fitsmeta.IncompleteFile:
                p.unlink(missing_ok=True)
            except Exception:
                pass  # not ours to judge here; ingest reports it


def list_jobs(lib: Library, *, status: str | None = None, job_id: int | None = None,
              limit: int = 50, conn=None) -> list[dict]:
    """Jobs newest first. Works on a read-only Library; empty before schema 2."""
    conn = conn or lib.reader
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'").fetchone():
        return []
    sql = ("SELECT j.id AS job_id, j.source, j.status, j.phase, j.files_total, j.files_done, "
           "j.result_json, j.error, j.created_at, j.updated_at, q.query_json "
           "FROM jobs j JOIN queries q ON q.id = j.query_id WHERE 1 = 1")
    args: list = []
    if status:
        sql += " AND j.status = ?"
        args.append(status)
    if job_id is not None:
        sql += " AND j.id = ?"
        args.append(job_id)
    sql += f" ORDER BY j.id DESC LIMIT {int(limit)}"
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        d["query"] = json.loads(d.pop("query_json"))
        result = d.pop("result_json")
        d["result"] = json.loads(result) if result else None
        out.append(d)
    return out
