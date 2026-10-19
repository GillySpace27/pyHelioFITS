"""The library: a store of files kept once each, and the index that describes them.

A writable Library is the only writer. It holds an exclusive lock on the library
root so a second Heliad process cannot write at the same time. A read-only
Library (Library.open(readonly=True)) takes no lock and cannot change anything,
so scripts and read commands keep working while the Mac app's core is running.
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

from . import checks, config, db, fitsmeta, thumbs
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


class ReadOnlyLibrary(RuntimeError):
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



def time_bound(text: str | None) -> str | None:
    """A range bound in the index's own form (isot with milliseconds), so that comparing
    text compares times. Unchanged, an end of "16:30:00" sorts before an observation at
    16:30:00.000 and leaves it out. Text that is not a time is passed on as it is."""
    if not text:
        return text
    try:
        when = datetime.fromisoformat(text.strip().removesuffix("Z").replace(" ", "T"))
    except ValueError:
        return text
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

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
    def __init__(self, cfg: config.Config, *, readonly: bool = False):
        self.cfg = cfg
        self.root = cfg.library_root
        self.readonly = readonly
        self._write_lock = threading.RLock()
        self._lock = None
        self.conn = None
        if readonly:
            if not cfg.db_path.exists():
                raise FileNotFoundError(f"no Heliad library at {self.root} (run: heliad init)")
            self.reader = db.connect_reader(cfg.db_path)
            compat = int(db.meta_value(self.reader, "reader_compat") or 1)
            if compat > db.SCHEMA_VERSION:
                self.reader.close()
                raise RuntimeError(f"{cfg.db_path} needs a newer Heliad (reader_compat {compat})")
            return
        self._lock = _RootLock(self.root)
        self._lock.acquire()
        for d in (cfg.store_dir, cfg.views_dir, cfg.staging_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.conn = db.connect_writer(cfg.db_path)
        self.reader = db.connect_reader(cfg.db_path)
        if db.meta_value(self.conn, "rules_version") is None:
            empty = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
            # A library from before rules were versioned is marked 0, so reindex is due.
            db.set_meta(self.conn, "rules_version", fitsmeta.RULES_VERSION if empty else 0)
        if not config.exists():
            config.write(cfg, db.SCHEMA_VERSION)

    @classmethod
    def open(cls, library_root=None, *, readonly: bool = False) -> "Library":
        return cls(config.load(library_root), readonly=readonly)

    def make_default(self) -> Path:
        """Point Studio, the Mac app and scripts at this library."""
        return config.write(self.cfg, db.SCHEMA_VERSION)

    def _require_writer(self) -> None:
        if self.readonly:
            raise ReadOnlyLibrary("this Library was opened read-only")

    def close(self) -> None:
        self.reader.close()
        if self.conn is not None:
            self.conn.close()
        if self._lock is not None:
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
        self._require_writer()
        path = Path(path)
        fitsmeta.check_complete(path)  # a cut-off download must not enter the store
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
            obs_key = meta.obs_key if meta.date_obs or meta.sequence else f"{meta.dataset_key}|sha:{sha}"
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
                    # The observation is dated by its current file; releases can date it differently.
                    conn.execute("UPDATE observations SET current_file_id = ?, date_obs = ? WHERE id = ?",
                                 (file_id, meta.date_obs or "", obs_id))
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
        for row in self.reader.execute("SELECT id, relpath, sha256 FROM files").fetchall():
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
        q = lambda sql: self.reader.execute(sql).fetchone()[0]  # noqa: E731
        rules = int(db.meta_value(self.reader, "rules_version") or 0)
        return {
            "library_root": str(self.root),
            "schema_version": db.schema_version(self.reader),
            "rules_version": rules,
            "reindex_due": rules != fitsmeta.RULES_VERSION,
            "datasets": q("SELECT COUNT(*) FROM datasets"),
            "observations": q("SELECT COUNT(*) FROM observations"),
            "files": q("SELECT COUNT(*) FROM files"),
            "bytes": q("SELECT COALESCE(SUM(size), 0) FROM files"),
            "warnings": q("SELECT COUNT(*) FROM checks WHERE severity != 'info'"),
            "collisions": len(self.collisions()),
        }

    def datasets(self) -> list[dict]:
        rows = self.reader.execute(
            "SELECT d.*, COUNT(o.id) AS observations, MIN(o.date_obs) AS first, "
            "MAX(o.date_obs) AS last, "
            "(SELECT COALESCE(SUM(f.size), 0) FROM files f JOIN observations o2 "
            " ON o2.id = f.observation_id WHERE o2.dataset_id = d.id) AS bytes "
            "FROM datasets d LEFT JOIN observations o ON o.dataset_id = d.id "
            "GROUP BY d.id ORDER BY d.label"
        ).fetchall()
        return [dict(r) for r in rows]

    def current_files(self, *, dataset_key: str | None = None, start: str | None = None,
                      end: str | None = None, limit: int | None = None, offset: int = 0) -> list[dict]:
        start, end = time_bound(start), time_bound(end)
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
        sql += " ORDER BY date_obs, file_id"
        if limit:
            sql += f" LIMIT {int(limit)} OFFSET {max(0, int(offset))}"
        return [dict(r) for r in self.reader.execute(sql, args).fetchall()]

    def checks_for(self, file_id: int) -> list[dict]:
        rows = self.reader.execute(
            "SELECT code, severity, message FROM checks WHERE file_id = ?", (file_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def versions(self, observation_id: int) -> list[dict]:
        """Every file of one observation, newest version first, with which one is current."""
        rows = self.reader.execute(
            "SELECT f.id AS file_id, f.relpath, f.version, f.size, f.original_name, f.ingested_at, "
            "f.id = o.current_file_id AS current "
            "FROM files f JOIN observations o ON o.id = f.observation_id WHERE o.id = ?",
            (observation_id,),
        ).fetchall()
        out = [dict(r) for r in rows]
        out.sort(key=lambda r: (fitsmeta.version_sort_key(r["version"]), -r["file_id"]), reverse=True)
        return out

    def collisions(self) -> list[dict]:
        """Observations holding two different files with the same version as the current one.

        Only one of them can be current, so the other is hidden from readers. Zero is
        normal; anything else means the dataset key misses a keyword that tells the
        files apart (for example a polarizer or a data product written at one time).
        """
        rows = self.reader.execute(
            "SELECT o.id AS observation_id, d.label, o.date_obs, COUNT(f.id) AS files "
            "FROM observations o JOIN files cur ON cur.id = o.current_file_id "
            "JOIN files f ON f.observation_id = o.id AND f.version IS cur.version "
            "JOIN datasets d ON d.id = o.dataset_id "
            "GROUP BY o.id HAVING COUNT(f.id) > 1 ORDER BY d.label, o.date_obs"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- delete --------------------------------------------------------------

    def delete(self, *, file_ids=(), observation_ids=(), dataset_keys=(), dry_run: bool = False) -> dict:
        """Remove files from the library: their index rows first, then the stored files.

        Deleting the current version of an observation makes the next newest one
        current; deleting an observation's last file removes the observation (and
        its place in views), and a dataset with no observations left goes too.
        Views that lost members have their link folders rebuilt.

        bytes_released counts the stored files removed. On APFS a file imported
        from an event folder is a clone of the original, and a view built with the
        hard-link fallback shares the file's blocks, so free disk can grow by less.
        """
        self._require_writer()
        conn = self.conn
        with self._write_lock:
            ids = set(int(i) for i in file_ids)
            if observation_ids:
                q = ",".join("?" * len(observation_ids))
                ids |= {r[0] for r in conn.execute(
                    f"SELECT id FROM files WHERE observation_id IN ({q})", list(observation_ids))}
            if dataset_keys:
                q = ",".join("?" * len(dataset_keys))
                ids |= {r[0] for r in conn.execute(
                    "SELECT f.id FROM files f JOIN observations o ON o.id = f.observation_id "
                    f"JOIN datasets d ON d.id = o.dataset_id WHERE d.dataset_key IN ({q})",
                    list(dataset_keys))}
            if not ids:
                return {"files": 0, "observations": 0, "datasets": 0, "bytes_released": 0,
                        "repointed": 0, "dry_run": dry_run, "views_rebuilt": []}
            q = ",".join("?" * len(ids))
            files = [dict(r) for r in conn.execute(
                f"SELECT id, relpath, size, observation_id, sha256 FROM files WHERE id IN ({q})", list(ids))]
            obs_ids = sorted({f["observation_id"] for f in files})
            oq = ",".join("?" * len(obs_ids))
            touched_views = [r[0] for r in conn.execute(
                f"SELECT DISTINCT v.name FROM views v JOIN view_members m ON m.view_id = v.id "
                f"WHERE m.observation_id IN ({oq})", obs_ids)]
            removed_obs, repointed, emptied_datasets = [], 0, set()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(f"DELETE FROM checks WHERE file_id IN ({q})", list(ids))
                conn.execute(f"UPDATE downloads SET file_id = NULL WHERE file_id IN ({q})", list(ids))
                conn.execute(f"UPDATE observations SET current_file_id = NULL "
                             f"WHERE current_file_id IN ({q})", list(ids))
                conn.execute(f"DELETE FROM files WHERE id IN ({q})", list(ids))
                for oid in obs_ids:
                    obs = conn.execute("SELECT dataset_id, current_file_id FROM observations WHERE id = ?",
                                       (oid,)).fetchone()
                    left = conn.execute("SELECT id, version FROM files WHERE observation_id = ?",
                                        (oid,)).fetchall()
                    if not left:
                        conn.execute("DELETE FROM view_members WHERE observation_id = ?", (oid,))
                        conn.execute("DELETE FROM observations WHERE id = ?", (oid,))
                        removed_obs.append(oid)
                        emptied_datasets.add(obs["dataset_id"])
                    elif obs["current_file_id"] is None:
                        best = max(left, key=lambda r: (fitsmeta.version_sort_key(r["version"]), -r["id"]))
                        conn.execute("UPDATE observations SET current_file_id = ? WHERE id = ?",
                                     (best["id"], oid))
                        repointed += 1
                gone_datasets = 0
                for did in emptied_datasets:
                    if not conn.execute("SELECT 1 FROM observations WHERE dataset_id = ? LIMIT 1",
                                        (did,)).fetchone():
                        conn.execute("DELETE FROM datasets WHERE id = ?", (did,))
                        gone_datasets += 1
                conn.execute("ROLLBACK" if dry_run else "COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        released, failed = 0, []
        if not dry_run:
            for f in files:
                p = self.abspath(f["relpath"])
                thumbs.remove(self.root, f["sha256"])
                try:
                    p.unlink()
                    released += f["size"]
                    self._prune_empty_dirs(p.parent)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    failed.append({"relpath": f["relpath"], "error": str(exc)})
            from . import views  # views imports this module
            for name in touched_views:
                views.materialize(self, name)
        else:
            released = sum(f["size"] for f in files)
        return {"files": len(files), "observations": len(removed_obs), "datasets": gone_datasets,
                "repointed": repointed, "bytes_released": released, "unlink_failed": failed,
                "views_rebuilt": touched_views, "dry_run": dry_run}

    def _prune_empty_dirs(self, d: Path) -> None:
        store = self.cfg.store_dir.resolve()
        d = d.resolve()
        while d != store and store in d.parents:
            try:
                d.rmdir()
            except OSError:
                return
            d = d.parent

    # ---- reindex -------------------------------------------------------------

    def reindex(self, *, dry_run: bool = False, from_files: bool = True, progress=None) -> dict:
        """Rebuild every dataset and observation key with the current fitsmeta rules.

        Keys are fixed when a file is ingested, so a rule change (a new keyword that
        separates products, a fix for one mission's headers) only reaches files that
        arrive afterwards. This applies it to everything already in the library, in
        one transaction. Headers are re-read from the stored files (from_files) or,
        for a file that is missing, taken from the indexed keywords. File ids, paths
        and checksums never change; datasets and observations keep their ids when
        their key is unchanged; views keep every file they held.
        """
        self._require_writer()
        conn = self.conn
        with self._write_lock:
            files = [dict(r) for r in conn.execute(
                "SELECT f.id, f.relpath, f.original_name, f.version, f.header_json, f.observation_id, "
                "f.sha256 FROM files f ORDER BY f.id")]
            metas = {}
            for i, f in enumerate(files, 1):
                p = self.abspath(f["relpath"])
                meta = None
                if from_files and p.exists():
                    try:
                        meta = fitsmeta.read(p)
                    except Exception:
                        meta = None
                if meta is None:
                    meta = fitsmeta.from_header(json.loads(f["header_json"]), f["original_name"])
                metas[f["id"]] = meta
                if progress and (i % 200 == 0 or i == len(files)):
                    progress(i, len(files))
            old_ds = {r["dataset_key"]: r["id"] for r in conn.execute("SELECT id, dataset_key FROM datasets")}
            old_obs = {r["obs_key"]: r["id"] for r in conn.execute("SELECT id, obs_key FROM observations")}
            old_current = {r["id"]: r["current_file_id"] for r in conn.execute(
                "SELECT id, current_file_id FROM observations")}

            # Datasets in ingest order, snapping wavelengths the way ingest does.
            new_ds: dict[str, fitsmeta.FitsMeta] = {}
            for f in files:
                meta = metas[f["id"]]
                if meta.wavelength is not None:
                    prefix = meta.dataset_key.rsplit("/", 1)[0]
                    best = None
                    for key, m in new_ds.items():
                        if key.rsplit("/", 1)[0] == prefix and m.wavelength is not None:
                            gap = abs(m.wavelength - meta.wavelength)
                            if gap <= WAVELENGTH_BAND_TOLERANCE * meta.wavelength and (best is None or gap < best[0]):
                                best = (gap, m.wavelength)
                    if best is not None:
                        meta.wavelength = best[1]
                new_ds.setdefault(meta.dataset_key, meta)
            groups: dict[str, list[int]] = {}
            for f in files:
                meta = metas[f["id"]]
                key = meta.obs_key if meta.date_obs or meta.sequence else f"{meta.dataset_key}|sha:{f['sha256']}"
                groups.setdefault(key, []).append(f["id"])
            version = {f["id"]: metas[f["id"]].version for f in files}
            current = {}
            for key, fids in groups.items():
                # Newest version wins; among equals the earliest ingested stays, as in ingest.
                current[key] = max(fids, key=lambda i: (fitsmeta.version_sort_key(version[i]), -i))

            file_obs_old = {f["id"]: f["observation_id"] for f in files}
            summary = {
                "files": len(files),
                "datasets_before": len(old_ds), "datasets_after": len(new_ds),
                "observations_before": len(old_obs), "observations_after": len(groups),
                "files_moved": 0, "current_changed": 0, "dry_run": dry_run,
            }
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("PRAGMA defer_foreign_keys = ON")
                members = conn.execute("SELECT view_id, observation_id FROM view_members").fetchall()
                view_files: dict[int, set[int]] = {}
                obs_files: dict[int, list[int]] = {}
                for fid, oid in file_obs_old.items():
                    obs_files.setdefault(oid, []).append(fid)
                for m in members:
                    view_files.setdefault(m["view_id"], set()).update(obs_files.get(m["observation_id"], []))
                conn.execute("DELETE FROM view_members")
                conn.execute("UPDATE observations SET current_file_id = NULL")
                ds_ids: dict[str, int] = {}
                for key, meta in new_ds.items():
                    cols = (meta.observatory, meta.instrument, meta.detector, meta.product,
                            meta.level, meta.wavelength, meta.label)
                    if key in old_ds:
                        conn.execute("UPDATE datasets SET observatory=?, instrument=?, detector=?, product=?, "
                                     "level=?, wavelength=?, label=? WHERE id = ?", (*cols, old_ds[key]))
                        ds_ids[key] = old_ds[key]
                    else:
                        ds_ids[key] = conn.execute(
                            "INSERT INTO datasets(dataset_key, observatory, instrument, detector, product, "
                            "level, wavelength, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (key, *cols)).lastrowid
                obs_ids: dict[str, int] = {}
                for key, fids in groups.items():
                    meta = metas[current[key]]  # dated by its current file, as in ingest
                    did = ds_ids[meta.dataset_key]
                    if key in old_obs:
                        conn.execute("UPDATE observations SET dataset_id = ?, date_obs = ? WHERE id = ?",
                                     (did, meta.date_obs or "", old_obs[key]))
                        obs_ids[key] = old_obs[key]
                    else:
                        obs_ids[key] = conn.execute(
                            "INSERT INTO observations(obs_key, dataset_id, date_obs) VALUES (?, ?, ?)",
                            (key, did, meta.date_obs or "")).lastrowid
                file_obs_new = {}
                for key, fids in groups.items():
                    for fid in fids:
                        file_obs_new[fid] = obs_ids[key]
                        conn.execute("UPDATE files SET observation_id = ?, version = ?, header_json = ? "
                                     "WHERE id = ?", (obs_ids[key], version[fid],
                                                     json.dumps(metas[fid].header, sort_keys=True), fid))
                    conn.execute("UPDATE observations SET current_file_id = ? WHERE id = ?",
                                 (current[key], obs_ids[key]))
                    if old_current.get(obs_ids[key]) != current[key]:
                        summary["current_changed"] += 1
                summary["files_moved"] = sum(1 for fid in file_obs_new
                                             if file_obs_new[fid] != file_obs_old[fid])
                keep_obs = set(obs_ids.values())
                for oid in set(old_obs.values()) - keep_obs:
                    conn.execute("DELETE FROM observations WHERE id = ?", (oid,))
                keep_ds = set(ds_ids.values())
                for did in set(old_ds.values()) - keep_ds:
                    conn.execute("DELETE FROM datasets WHERE id = ?", (did,))
                for vid, fids in view_files.items():
                    conn.executemany("INSERT OR IGNORE INTO view_members(view_id, observation_id) VALUES (?, ?)",
                                     [(vid, file_obs_new[f]) for f in fids])
                db.set_meta(conn, "rules_version", fitsmeta.RULES_VERSION)
                conn.execute("ROLLBACK" if dry_run else "COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        if not dry_run:
            from . import views
            summary["views_rebuilt"] = [r["name"] for r in views.rebuild_all(self)]
        return summary

    # ---- thumbnails ----------------------------------------------------------

    def thumbnail(self, file_id: int, size: int = thumbs.DEFAULT_SIZE) -> Path:
        """A PNG preview of a stored file, made on first request and cached by checksum.

        Works on a read-only Library too: the cache is outside the index.
        """
        row = self.reader.execute("SELECT relpath, sha256 FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise KeyError(f"no file {file_id}")
        return thumbs.ensure(self.root, self.abspath(row["relpath"]), row["sha256"], size)
