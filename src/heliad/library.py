"""The library: a store of files kept once each, and the index that describes them.

Library is the only writer. It holds an exclusive lock on the library root so a
second Heliad process cannot write at the same time; readers never need it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, db, fitsmeta
from .query import Query

FITS_SUFFIXES = (".fits", ".fts", ".fit", ".fits.gz", ".fts.gz", ".fit.gz")
# Wavelengths closer than this fraction are one passband (0.2% is about 11 Å at 5500 Å
# and 0.4 Å at 193 Å), so a release that rewrites WAVELNTH slightly stays one dataset.
WAVELENGTH_BAND_TOLERANCE = 0.002


def is_fits(path: Path) -> bool:
    return path.name.lower().endswith(FITS_SUFFIXES)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def clone_or_copy(src: Path, dst: Path) -> str:
    """Copy a file, as an APFS clone when the platform allows it.

    A clone shares the source's blocks until either file changes, so importing an
    80 GB event folder costs no extra disk. Returns "clone" or "copy".
    """
    if sys.platform == "darwin":
        try:
            import ctypes
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            if libc.clonefile(os.fsencode(src), os.fsencode(dst), 0) == 0:
                return "clone"
        except (OSError, AttributeError):
            pass
    shutil.copyfile(src, dst)
    return "copy"


class LibraryLocked(RuntimeError):
    pass


class _RootLock:
    def __init__(self, root: Path):
        self.path = root / ".heliad.lock"
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise LibraryLocked(f"another Heliad process is writing to {self.path.parent}") from exc
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(str(os.getpid()))
        self._fh.flush()

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


@dataclass
class IngestResult:
    status: str               # added | duplicate | new_version | older_version | same_version
    file_id: int
    relpath: str
    findings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "file_id": self.file_id,
            "relpath": self.relpath,
            "findings": [f.__dict__ for f in self.findings],
        }


class Library:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.root = cfg.library_root
        self._lock = _RootLock(self.root)
        self._lock.acquire()
        for d in (cfg.store_dir, cfg.views_dir, cfg.staging_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.conn = db.connect_writer(cfg.db_path)
        self._write_lock = threading.RLock()
        config.write(cfg, db.SCHEMA_VERSION)

    @classmethod
    def open(cls, library_root=None) -> "Library":
        return cls(config.load(library_root))

    def close(self) -> None:
        self.conn.close()
        self._lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- paths -------------------------------------------------------------

    def abspath(self, relpath: str) -> Path:
        return self.root / relpath

    def _store_relpath(self, meta: fitsmeta.FitsMeta, name: str, sha: str) -> str:
        parts = [p for p in meta.dataset_key.split("/") if p != "-"] or ["unknown"]
        date = (meta.date_obs or "")[:10]
        if len(date) == 10:
            parts += [date[:4], date[5:7], date[8:10]]
        else:
            parts.append("undated")
        rel = Path("store", *parts, name)
        if (self.root / rel).exists():
            stem, dot, ext = name.partition(".")
            rel = rel.with_name(f"{stem}.{sha[:12]}{dot}{ext}")
        return rel.as_posix()

    # ---- writes ------------------------------------------------------------

    def _query_id(self, query: Query | None, source: str) -> int | None:
        if query is None:
            return None
        text = query.to_json()
        row = self.conn.execute(
            "SELECT id FROM queries WHERE source = ? AND query_json = ?", (source, text)
        ).fetchone()
        if row:
            return row["id"]
        return self.conn.execute(
            "INSERT INTO queries(source, query_json, created_at) VALUES (?, ?, ?)",
            (source, text, now()),
        ).lastrowid

    def _snap_wavelength(self, meta: fitsmeta.FitsMeta) -> list[checks.Finding]:
        """Read a wavelength a hair off an existing dataset's as that dataset's band.

        Releases of one product can disagree slightly on WAVELNTH (ASPIICS total
        brightness is 5508 in one release and 5511 in the next), and without this
        each release becomes its own dataset instead of versions of one another.
        """
        if meta.wavelength is None:
            return []
        prefix = meta.dataset_key.rsplit("/", 1)[0]
        rows = self.conn.execute(
            "SELECT dataset_key, wavelength FROM datasets WHERE wavelength IS NOT NULL"
        ).fetchall()
        best = None
        for r in rows:
            if r["dataset_key"].rsplit("/", 1)[0] != prefix:
                continue
            gap = abs(r["wavelength"] - meta.wavelength)
            if gap <= WAVELENGTH_BAND_TOLERANCE * meta.wavelength and (best is None or gap < best[0]):
                best = (gap, r["wavelength"])
        if best is None or best[0] == 0:
            return []
        written, meta.wavelength = meta.wavelength, best[1]
        return [checks.Finding(
            "wavelength_snapped", "info",
            f"WAVELNTH {written:g} Å filed under this dataset's {best[1]:g} Å band.",
        )]

    def _dataset_id(self, meta: fitsmeta.FitsMeta) -> int:
        row = self.conn.execute(
            "SELECT id FROM datasets WHERE dataset_key = ?", (meta.dataset_key,)
        ).fetchone()
        if row:
            return row["id"]
        return self.conn.execute(
            "INSERT INTO datasets(dataset_key, observatory, instrument, detector, product, "
            "level, wavelength, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (meta.dataset_key, meta.observatory, meta.instrument, meta.detector,
             meta.product, meta.level, meta.wavelength, meta.label),
        ).lastrowid

    def ingest(
        self,
        path: str | os.PathLike,
        *,
        consume: bool = False,
        source_url: str | None = None,
        query: Query | None = None,
        source: str = "local",
    ) -> IngestResult:
        """Add one FITS file to the library.

        The file is copied into the store (or moved, with consume=True, which is
        how downloads leave the staging area). A file already in the library by
        checksum is not stored twice.
        """
        path = Path(path)
        sha = sha256_of(path)
        with self._write_lock:
            row = self.conn.execute(
                "SELECT id, relpath FROM files WHERE sha256 = ?", (sha,)
            ).fetchone()
            if row:
                if consume:
                    path.unlink(missing_ok=True)
                return IngestResult("duplicate", row["id"], row["relpath"])

            meta = fitsmeta.read(path)
            findings = checks.run(meta) + self._snap_wavelength(meta)
            relpath = self._store_relpath(meta, path.name, sha)
            dest = self.root / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.unlink(missing_ok=True)
            if consume:
                shutil.move(str(path), tmp)
            else:
                clone_or_copy(path, tmp)
            os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(tmp, dest)

            try:
                status, file_id = self._record(meta, sha, relpath, dest, path.name,
                                               source_url, query, source, findings)
            except BaseException:
                os.chmod(dest, stat.S_IWUSR | stat.S_IRUSR)
                dest.unlink(missing_ok=True)
                raise
            return IngestResult(status, file_id, relpath, findings)

    def _record(self, meta, sha, relpath, dest, original_name, source_url, query, source, findings):
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            dataset_id = self._dataset_id(meta)
            obs_key = meta.obs_key if meta.date_obs else f"{meta.dataset_key}|sha:{sha}"
            obs = conn.execute(
                "SELECT o.id, o.current_file_id, f.version AS current_version "
                "FROM observations o LEFT JOIN files f ON f.id = o.current_file_id "
                "WHERE o.obs_key = ?", (obs_key,)
            ).fetchone()
            if obs is None:
                obs_id = conn.execute(
                    "INSERT INTO observations(obs_key, dataset_id, date_obs) VALUES (?, ?, ?)",
                    (obs_key, dataset_id, meta.date_obs or ""),
                ).lastrowid
            else:
                obs_id = obs["id"]
            file_id = conn.execute(
                "INSERT INTO files(sha256, relpath, size, observation_id, version, original_name, "
                "source_url, query_id, ingested_at, header_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sha, relpath, dest.stat().st_size, obs_id, meta.version, original_name,
                 source_url, self._query_id(query, source), now(),
                 json.dumps(meta.header, sort_keys=True)),
            ).lastrowid

            if obs is None:
                status = "added"
                conn.execute("UPDATE observations SET current_file_id = ? WHERE id = ?", (file_id, obs_id))
            else:
                new_key = fitsmeta.version_sort_key(meta.version)
                old_key = fitsmeta.version_sort_key(obs["current_version"])
                if new_key > old_key:
                    status = "new_version"
                    conn.execute("UPDATE observations SET current_file_id = ? WHERE id = ?", (file_id, obs_id))
                elif new_key < old_key:
                    status = "older_version"
                else:
                    status = "same_version"
                    findings.append(checks.Finding(
                        "same_version_differs", "warning",
                        f"Another file with version {meta.version!r} of this observation is already "
                        "in the library with different contents; the earlier one stays current.",
                    ))
            for f in findings:
                conn.execute(
                    "INSERT INTO checks(file_id, code, severity, message) VALUES (?, ?, ?, ?)",
                    (file_id, f.code, f.severity, f.message),
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return status, file_id

    def import_tree(self, top: str | os.PathLike, *, progress=None) -> dict:
        """Ingest every FITS file under a folder (an event folder, an old cache)."""
        counts: dict[str, int] = {}
        errors = []
        paths = sorted(p for p in Path(top).rglob("*") if p.is_file() and is_fits(p))
        for i, p in enumerate(paths, 1):
            try:
                result = self.ingest(p)
                counts[result.status] = counts.get(result.status, 0) + 1
            except Exception as exc:  # one bad file must not stop an import
                errors.append({"path": str(p), "error": f"{type(exc).__name__}: {exc}"})
                counts["error"] = counts.get("error", 0) + 1
            if progress:
                progress(i, len(paths), p)
        return {"files": len(paths), "counts": counts, "errors": errors}

    def verify(self) -> dict:
        """Recompute every checksum. Reports missing and altered files; changes nothing."""
        missing, altered, ok = [], [], 0
        for row in self.conn.execute("SELECT id, relpath, sha256 FROM files").fetchall():
            p = self.abspath(row["relpath"])
            if not p.exists():
                missing.append(row["relpath"])
            elif sha256_of(p) != row["sha256"]:
                altered.append(row["relpath"])
            else:
                ok += 1
        return {"ok": ok, "missing": missing, "altered": altered}

    # ---- reads -------------------------------------------------------------

    def stats(self) -> dict:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "library_root": str(self.root),
            "schema_version": db.SCHEMA_VERSION,
            "datasets": q("SELECT COUNT(*) FROM datasets"),
            "observations": q("SELECT COUNT(*) FROM observations"),
            "files": q("SELECT COUNT(*) FROM files"),
            "bytes": q("SELECT COALESCE(SUM(size), 0) FROM files"),
            "warnings": q("SELECT COUNT(*) FROM checks WHERE severity != 'info'"),
        }

    def datasets(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT d.*, COUNT(o.id) AS observations, MIN(o.date_obs) AS first, "
            "MAX(o.date_obs) AS last, "
            "(SELECT COALESCE(SUM(f.size), 0) FROM files f JOIN observations o2 "
            " ON o2.id = f.observation_id WHERE o2.dataset_id = d.id) AS bytes "
            "FROM datasets d LEFT JOIN observations o ON o.dataset_id = d.id "
            "GROUP BY d.id ORDER BY d.label"
        ).fetchall()
        return [dict(r) for r in rows]

    def current_files(self, *, dataset_key: str | None = None, start: str | None = None,
                      end: str | None = None, limit: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM current_files WHERE 1 = 1", []
        if dataset_key:
            sql += " AND dataset_key = ?"
            args.append(dataset_key)
        if start:
            sql += " AND date_obs >= ?"
            args.append(start)
        if end:
            sql += " AND date_obs <= ?"
            args.append(end)
        sql += " ORDER BY date_obs"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def checks_for(self, file_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT code, severity, message FROM checks WHERE file_id = ?", (file_id,)
        ).fetchall()
        return [dict(r) for r in rows]
