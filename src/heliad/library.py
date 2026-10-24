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

from . import checks, config, db, fitsmeta, thumbs, times
from .query import Query

FITS_SUFFIXES = (".fits", ".fts", ".fit", ".fits.gz", ".fts.gz", ".fit.gz")
# Wavelengths closer than this fraction are one passband (0.2% is about 11 Å at 5500 Å
# and 0.4 Å at 193 Å), so a release that rewrites WAVELNTH slightly stays one dataset.
WAVELENGTH_BAND_TOLERANCE = 0.002


def is_fits(path: Path) -> bool:
    return path.name.lower().endswith(FITS_SUFFIXES)


GZIP_MAGIC = b"\x1f\x8b"
FITS_MAGIC = b"SIMPLE  ="


def fits_name(name: str, path: Path) -> str:
    """The name to store a file under: its own, with .fits or .fits.gz added when it has no
    FITS suffix, chosen by what the file starts with (gzip's magic bytes 1f 8b, or a FITS
    primary header's "SIMPLE  ="). A name that already ends in .gz, or a file that is
    neither, is left alone. Studio's oldest cache files are named by a bare SHA-256 and
    reach Heliad that way; without a suffix, Finder and HelioFITS Peek do not see FITS.
    """
    if is_fits(Path(name)) or name.lower().endswith(".gz"):
        return name
    try:
        with open(path, "rb") as fh:
            head = fh.read(len(FITS_MAGIC))
    except OSError:
        return name
    if head.startswith(GZIP_MAGIC):
        return name + ".fits.gz"
    if head == FITS_MAGIC:
        return name + ".fits"
    return name


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
    elif sys.platform.startswith("linux") and _reflink(src, dst):
        return "clone"
    shutil.copyfile(src, dst)
    return "copy"


def remove_file(path: Path) -> None:
    """Unlink a file, even a read-only one on Windows.

    POSIX lets the owner of a folder delete a read-only file in it; Windows refuses with
    PermissionError until the read-only attribute is cleared. Store files are read-only,
    so on Windows this clears the attribute and tries again. Elsewhere it is a plain unlink.
    """
    try:
        path.unlink()
    except PermissionError:
        if os.name != "nt":
            raise
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()


def protect(path: Path) -> None:
    """Make a stored file read-only again (a hard link shares the attribute on Windows,
    so clearing it to delete a link clears it on the store file too)."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except FileNotFoundError:
        pass


_FICLONE = 0x40049409  # _IOW(0x94, 9, int), linux/fs.h


def _reflink(src: Path, dst: Path) -> bool:
    """A Linux reflink (ioctl FICLONE): Btrfs, XFS and bcachefs share the blocks. False,
    with nothing left at dst, when the filesystem or the kernel cannot (ext4, tmpfs, two
    filesystems), so the caller copies instead."""
    try:
        import fcntl
    except ImportError:
        return False
    try:
        with open(src, "rb") as s, open(dst, "xb") as d:
            fcntl.ioctl(d.fileno(), _FICLONE, s.fileno())
        return True
    except OSError:
        try:
            Path(dst).unlink()
        except OSError:
            pass
        return False


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
                # msvcrt locks bytes from the current position, and "a+" opens at the end,
                # which is past the pid a running writer wrote: every process must lock
                # the same byte (0) or a second writer would get a lock of its own.
                self._fh.seek(0)
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
            if os.name == "nt":
                import msvcrt
                try:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass  # closing the file releases it too
            self._fh.close()
            self._fh = None



def time_bound(text: str | None) -> str | None:
    """A range bound in the index's own form (isot with milliseconds), so that comparing
    text compares times. Unchanged, an end of "16:30:00" sorts before an observation at
    16:30:00.000 and leaves it out. Text that is not a time is passed on as it is."""
    if not text:
        return text
    return times.index_text(text) or text

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
            "level, wavelength, label, dataset_uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (meta.dataset_key, meta.observatory, meta.instrument, meta.detector,
             meta.product, meta.level, meta.wavelength, meta.label, db.new_uid()),
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
                if consume and path.exists():
                    remove_file(path)  # may be a read-only hard link to the stored copy (jobs._restage)
                    if os.name == "nt":
                        protect(self.abspath(row["relpath"]))
                return IngestResult("duplicate", row["id"], row["relpath"])

            try:
                meta = fitsmeta.read(path)
            except OSError as exc:
                if exc.errno is not None:  # the disk, not the file
                    raise
                raise fitsmeta.Unstorable(f"{path.name}: {exc}") from exc  # astropy cannot read it as FITS
            findings = checks.run(meta) + self._snap_wavelength(meta)
            relpath = self._store_relpath(meta, fits_name(path.name, path), sha)
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
                      end: str | None = None, limit: int | None = None, offset: int = 0,
                      include_rejected: bool = False) -> list[dict]:
        """The newest file of every observation, as readers see it in current_files.

        include_rejected adds the observations the cache editor set aside, and gives
        every row `rejected`: the reason, or None.
        """
        start, end = time_bound(start), time_bound(end)
        sql, args = "SELECT * FROM current_files WHERE 1 = 1", []
        if include_rejected and db.has_table(self.reader, "rejections"):
            sql = ("SELECT * FROM (SELECT f.id AS file_id, f.relpath, f.sha256, f.size, f.version, "
                   "o.id AS observation_id, o.date_obs, d.id AS dataset_id, d.dataset_key, d.label, "
                   "d.observatory, d.instrument, d.detector, d.product, d.level, d.wavelength, "
                   + ("d.dataset_uid, " if db.has_column(self.reader, "datasets", "dataset_uid") else "") +
                   "r.reason AS rejected FROM observations o JOIN files f ON f.id = o.current_file_id "
                   "JOIN datasets d ON d.id = o.dataset_id "
                   "LEFT JOIN rejections r ON r.observation_id = o.id) WHERE 1 = 1")
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

    def rejected(self, dataset_key: str | None = None) -> list[dict]:
        """Observations set aside by reject(), with the reason and their current file."""
        if not db.has_table(self.reader, "rejections"):
            return []
        sql = ("SELECT o.id AS observation_id, d.dataset_key, d.label, o.date_obs, r.reason, "
               "r.rejected_at, f.id AS file_id, f.relpath FROM rejections r "
               "JOIN observations o ON o.id = r.observation_id JOIN datasets d ON d.id = o.dataset_id "
               "LEFT JOIN files f ON f.id = o.current_file_id")
        args = []
        if dataset_key:
            sql += " WHERE d.dataset_key = ?"
            args.append(dataset_key)
        sql += " ORDER BY o.date_obs, o.id"
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

    # ---- reject --------------------------------------------------------------

    def reject(self, observation_ids, reason: str) -> dict:
        """Set observations aside: current_files, coverage and views stop showing them.

        Nothing is deleted; unreject() brings them back. An upgrade counts a rejected
        observation as one the library has, so it is never fetched again. Rejecting
        one already rejected replaces its reason.
        """
        self._require_writer()
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("a rejection needs a reason")
        ids = sorted({int(i) for i in observation_ids})
        with self._write_lock:
            q = ",".join("?" * len(ids))
            known = {r[0] for r in self.conn.execute(
                f"SELECT id FROM observations WHERE id IN ({q})", ids)} if ids else set()
            if set(ids) - known:
                raise KeyError(f"no observation {sorted(set(ids) - known)}")
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.executemany(
                    "INSERT INTO rejections(observation_id, reason, rejected_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(observation_id) DO UPDATE SET reason = excluded.reason, "
                    "rejected_at = excluded.rejected_at", [(i, reason.strip(), now()) for i in ids])
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        return {"rejected": len(ids), "observation_ids": ids, "views_rebuilt": self._rebuild_views_of(ids)}

    def unreject(self, observation_ids) -> dict:
        """Undo reject(): the observations are served to readers again."""
        self._require_writer()
        ids = sorted({int(i) for i in observation_ids})
        if not ids:
            return {"unrejected": 0, "observation_ids": [], "views_rebuilt": []}
        with self._write_lock:
            q = ",".join("?" * len(ids))
            n = self.conn.execute(f"DELETE FROM rejections WHERE observation_id IN ({q})", ids).rowcount
        return {"unrejected": n, "observation_ids": ids, "views_rebuilt": self._rebuild_views_of(ids)}

    def _rebuild_views_of(self, observation_ids: list[int]) -> list[str]:
        """Rebuild the folders of views holding these observations; their names."""
        if not observation_ids:
            return []
        q = ",".join("?" * len(observation_ids))
        with self._write_lock:
            names = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT v.name FROM views v JOIN view_members m ON m.view_id = v.id "
                f"WHERE m.observation_id IN ({q}) ORDER BY v.name", observation_ids)]
        from . import views  # views imports this module
        for name in names:
            views.materialize(self, name)
        return names

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
                if db.has_table(conn, "file_aliases"):
                    aliases = [r[0] for r in conn.execute(
                        f"SELECT old_relpath FROM file_aliases WHERE file_id IN ({q})", list(ids))]
                    conn.execute(f"DELETE FROM file_aliases WHERE file_id IN ({q})", list(ids))
                else:
                    aliases = []
                conn.execute(f"DELETE FROM files WHERE id IN ({q})", list(ids))
                for oid in obs_ids:
                    obs = conn.execute("SELECT dataset_id, current_file_id FROM observations WHERE id = ?",
                                       (oid,)).fetchone()
                    left = conn.execute("SELECT id, version FROM files WHERE observation_id = ?",
                                        (oid,)).fetchall()
                    if not left:
                        conn.execute("DELETE FROM view_members WHERE observation_id = ?", (oid,))
                        conn.execute("DELETE FROM rejections WHERE observation_id = ?", (oid,))
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
                    remove_file(p)
                    released += f["size"]
                    self._prune_empty_dirs(p.parent)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    failed.append({"relpath": f["relpath"], "error": str(exc)})
            for old in aliases:  # the links fix-extensions left at old names
                link = self.abspath(old)
                if link.is_symlink():
                    link.unlink()
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

    # ---- store names -----------------------------------------------------------

    def fix_extensions(self, *, dry_run: bool = False, progress=None) -> dict:
        """Give every stored FITS file without a FITS suffix one (.fits, or .fits.gz by the
        gzip magic), as ingest now does for new files.

        For each file: rename it, then point files.relpath at the new name and record the
        old one in file_aliases (one transaction), then leave a relative symlink at the old
        name so a saved Studio session holding the old path still opens. In that order a
        failure at any step leaves something the next run finishes: a renamed file whose
        row still has the old name is found by its new name and checksum; a missing link
        is made again from file_aliases. Running it twice changes nothing the second time.
        Where symlinks cannot be made (Windows without the privilege) the alias is kept
        and "links_skipped" counts it.

        A file another process has open is skipped and counted as "open" when that is
        cheap to see (Linux /proc, macOS lsof); otherwise it is renamed anyway, which an
        open file on macOS and Linux survives. Views are rebuilt afterwards. dry_run
        reports the same counts and touches nothing.
        """
        self._require_writer()
        conn = self.conn
        with self._write_lock:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, relpath, sha256 FROM files ORDER BY id") if not is_fits(Path(r["relpath"]))]
            in_use, open_check = _open_paths(self.cfg.store_dir) if rows else (set(), "not needed")
            out = {"candidates": len(rows), "renamed": 0, "fits": 0, "fits_gz": 0, "not_fits": 0,
                   "missing": 0, "open": 0, "failed": [], "links_made": 0, "links_skipped": 0,
                   "open_check": open_check, "dry_run": dry_run, "examples": []}
            for i, row in enumerate(rows, 1):
                if progress and (i % 200 == 0 or i == len(rows)):
                    progress(i, len(rows))
                old_rel = row["relpath"]
                old = self.abspath(old_rel)
                if not old.exists() or old.is_symlink():
                    new_rel = self._renamed_earlier(row)  # a run stopped between rename and index
                    if new_rel is None:
                        out["missing"] += 1
                        continue
                    target = new_rel
                else:
                    name = fits_name(old.name, old)
                    if name == old.name:
                        out["not_fits"] += 1
                        continue
                    target = (Path(old_rel).parent / name).as_posix()
                    if (self.root / target).exists() or (self.root / target).is_symlink():
                        stem, dot, ext = name.partition(".")
                        target = (Path(old_rel).parent / f"{stem}.{row['sha256'][:12]}{dot}{ext}").as_posix()
                    if str(old.resolve()) in in_use:
                        out["open"] += 1
                        continue
                kind = "fits_gz" if target.lower().endswith(".gz") else "fits"
                out[kind] += 1
                if len(out["examples"]) < 5:
                    out["examples"].append({"from": old_rel, "to": target})
                if dry_run:
                    continue
                try:
                    if old.exists() and not old.is_symlink():
                        os.rename(old, self.root / target)  # same folder: atomic, mode 444 kept
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        conn.execute("UPDATE files SET relpath = ? WHERE id = ? AND relpath = ?",
                                     (target, row["id"], old_rel))
                        conn.execute(
                            "INSERT INTO file_aliases(old_relpath, file_id, sha256, recorded_at) "
                            "VALUES (?, ?, ?, ?) ON CONFLICT(old_relpath) DO UPDATE SET "
                            "file_id = excluded.file_id, sha256 = excluded.sha256, "
                            "recorded_at = excluded.recorded_at", (old_rel, row["id"], row["sha256"], now()))
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
                except OSError as exc:
                    out[kind] -= 1
                    out["failed"].append({"relpath": old_rel, "error": str(exc)})
                    continue
                out["renamed"] += 1
            if not dry_run:
                made, skipped = self._alias_links()
                out["links_made"], out["links_skipped"] = made, skipped
        if not dry_run and out["renamed"]:
            from . import views  # views imports this module
            out["views_rebuilt"] = [r["name"] for r in views.rebuild_all(self)]
        return out

    def _renamed_earlier(self, row: dict) -> str | None:
        """The relpath a file already has on disk when an earlier fix-extensions renamed it
        but stopped before the index changed: its name plus .fits or .fits.gz, with the
        same checksum."""
        old = Path(row["relpath"])
        for suffix in (".fits", ".fits.gz"):
            names = [old.name + suffix]
            stem, dot, ext = (old.name + suffix).partition(".")
            names.append(f"{stem}.{row['sha256'][:12]}{dot}{ext}")
            for name in names:
                p = self.root / old.parent / name
                if p.is_file() and not p.is_symlink() and sha256_of(p) == row["sha256"]:
                    return (old.parent / name).as_posix()
        return None

    def _alias_links(self) -> tuple[int, int]:
        """Make the relative symlink at every old name in file_aliases that lacks one.
        Returns (made, skipped where the platform refuses symlinks)."""
        made = skipped = 0
        rows = self.conn.execute(
            "SELECT a.old_relpath, f.relpath FROM file_aliases a JOIN files f ON f.id = a.file_id").fetchall()
        for row in rows:
            link, target = self.abspath(row["old_relpath"]), self.abspath(row["relpath"])
            if link.is_symlink() or link.exists() or not target.exists():
                continue
            try:
                os.symlink(os.path.relpath(target, link.parent), link)
                made += 1
            except (OSError, NotImplementedError):
                skipped += 1
        return made, skipped

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

        A dataset whose key changes (PUNCH /530 became /5300 when WAVEUNIT 'nanometer'
        was read) keeps its row, id and dataset_uid: a new key takes over the old
        dataset that held most of its files (by sha256), and the old key is recorded in
        dataset_aliases so resolve_dataset() still finds it. A summary's
        "datasets_renamed" lists each {old_key, new_key, dataset_uid}.
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
            old_ds_uid = {r["id"]: r["dataset_uid"] for r in conn.execute("SELECT id, dataset_uid FROM datasets")}
            obs_ds = {r["id"]: r["dataset_id"] for r in conn.execute("SELECT id, dataset_id FROM observations")}
            sha_old_ds = {f["sha256"]: obs_ds.get(f["observation_id"]) for f in files}
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

            renamed = self._carry_datasets(new_ds, old_ds, sha_old_ds,
                                           {f["sha256"]: metas[f["id"]].dataset_key for f in files})
            file_obs_old = {f["id"]: f["observation_id"] for f in files}
            summary = {
                "files": len(files),
                "datasets_before": len(old_ds), "datasets_after": len(new_ds),
                "observations_before": len(old_obs), "observations_after": len(groups),
                "files_moved": 0, "current_changed": 0, "dry_run": dry_run,
                "datasets_renamed": [{"old_key": old, "new_key": new, "dataset_uid": old_ds_uid[did]}
                                     for new, (old, did) in sorted(renamed.items())],
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
                # A rejection follows the files of its observation, as a view membership does.
                rejected = [(obs_files.get(r["observation_id"], []), r["reason"], r["rejected_at"])
                            for r in conn.execute("SELECT * FROM rejections").fetchall()]
                conn.execute("DELETE FROM rejections")
                conn.execute("UPDATE observations SET current_file_id = NULL")
                ds_ids: dict[str, int] = {}
                for key, meta in new_ds.items():
                    cols = (meta.observatory, meta.instrument, meta.detector, meta.product,
                            meta.level, meta.wavelength, meta.label)
                    if key in old_ds:
                        conn.execute("UPDATE datasets SET observatory=?, instrument=?, detector=?, product=?, "
                                     "level=?, wavelength=?, label=? WHERE id = ?", (*cols, old_ds[key]))
                        ds_ids[key] = old_ds[key]
                    elif key in renamed:
                        old_key, did = renamed[key]
                        conn.execute("UPDATE datasets SET dataset_key=?, observatory=?, instrument=?, detector=?, "
                                     "product=?, level=?, wavelength=?, label=? WHERE id = ?", (key, *cols, did))
                        conn.execute("INSERT INTO dataset_aliases(old_key, dataset_uid, recorded_at) VALUES (?, ?, ?) "
                                     "ON CONFLICT(old_key) DO UPDATE SET dataset_uid = excluded.dataset_uid, "
                                     "recorded_at = excluded.recorded_at", (old_key, old_ds_uid[did], now()))
                        ds_ids[key] = did
                    else:
                        ds_ids[key] = conn.execute(
                            "INSERT INTO datasets(dataset_key, observatory, instrument, detector, product, "
                            "level, wavelength, label, dataset_uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (key, *cols, db.new_uid())).lastrowid
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
                for fids, reason, rejected_at in rejected:
                    conn.executemany("INSERT OR IGNORE INTO rejections(observation_id, reason, rejected_at) "
                                     "VALUES (?, ?, ?)", [(file_obs_new[f], reason, rejected_at) for f in fids])
                db.set_meta(conn, "rules_version", fitsmeta.RULES_VERSION)
                conn.execute("ROLLBACK" if dry_run else "COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        if not dry_run:
            from . import views
            summary["views_rebuilt"] = [r["name"] for r in views.rebuild_all(self)]
        return summary

    @staticmethod
    def _carry_datasets(new_ds: dict, old_ds: dict[str, int], sha_old_ds: dict[str, int | None],
                        sha_new_key: dict[str, str]) -> dict[str, tuple[str, int]]:
        """Which renamed dataset takes over which old one: {new_key: (old_key, old_id)}.

        A new key that was not a key before inherits the old dataset holding more than
        half of its files, matched by sha256 (a file's checksum never changes), as long as
        that old key is not itself still a key. When several new keys could take the same
        old dataset (a dataset split in two), the one with the most of its files wins; the
        others start with a uid of their own.
        """
        still = set(new_ds)
        key_of = {did: key for key, did in old_ds.items()}
        votes: dict[str, dict[int, int]] = {}
        sizes: dict[str, int] = {}
        for sha, new_key in sha_new_key.items():
            sizes[new_key] = sizes.get(new_key, 0) + 1
            did = sha_old_ds.get(sha)
            if new_key not in old_ds and did is not None and key_of.get(did) not in still:
                tally = votes.setdefault(new_key, {})
                tally[did] = tally.get(did, 0) + 1
        offers = []
        for new_key, tally in votes.items():
            did, n = max(tally.items(), key=lambda kv: (kv[1], -kv[0]))
            if 2 * n > sizes[new_key]:
                offers.append((n, new_key, did))
        out: dict[str, tuple[str, int]] = {}
        taken: set[int] = set()
        for n, new_key, did in sorted(offers, key=lambda o: (-o[0], o[1])):
            if did not in taken:
                taken.add(did)
                out[new_key] = (key_of[did], did)
        return out

    # ---- stable ids ------------------------------------------------------------

    def dataset(self, *, dataset_uid: str | None = None, dataset_key: str | None = None) -> dict | None:
        """One dataset as library.datasets lists it, by uid or by key, or None.

        A key that is no longer a dataset's key is looked up in dataset_aliases, so a
        key saved before a reindex renamed the dataset still finds it. The result says
        how it was found: "resolved_by" is "uid", "key" or "alias".
        """
        if (dataset_uid is None) == (dataset_key is None):
            raise ValueError("give exactly one of dataset_uid and dataset_key")
        if dataset_key is not None:
            found = self.resolve_dataset(dataset_key)
            if found is None:
                return None
            dataset_uid, how = found["dataset_uid"], found["resolved_by"]
        else:
            how = "uid"
        row = next((d for d in self.datasets() if d.get("dataset_uid") == dataset_uid), None)
        return None if row is None else {**row, "resolved_by": how}

    def resolve_dataset(self, key_or_uid: str, conn=None) -> dict | None:
        """{dataset_uid, dataset_key, resolved_by} for a uid, a current key or an old
        key recorded in dataset_aliases; None when nothing matches. A current key wins
        over an alias of the same text. conn, when given, is read instead of the
        library's reader (the core's background pool reads through its own)."""
        conn = conn or self.reader
        if not db.has_column(conn, "datasets", "dataset_uid"):
            return None
        for how, sql in (("uid", "SELECT dataset_uid, dataset_key FROM datasets WHERE dataset_uid = ?"),
                         ("key", "SELECT dataset_uid, dataset_key FROM datasets WHERE dataset_key = ?"),
                         ("alias", "SELECT d.dataset_uid, d.dataset_key FROM dataset_aliases a "
                                   "JOIN datasets d ON d.dataset_uid = a.dataset_uid WHERE a.old_key = ?")):
            row = conn.execute(sql, (key_or_uid,)).fetchone()
            if row:
                return {"dataset_uid": row["dataset_uid"], "dataset_key": row["dataset_key"], "resolved_by": how}
        return None

    # ---- thumbnails ----------------------------------------------------------

    def thumbnail(self, file_id: int, size: int = thumbs.DEFAULT_SIZE) -> Path:
        """A PNG preview of a stored file, made on first request and cached by checksum.

        Works on a read-only Library too: the cache is outside the index.
        """
        row = self.reader.execute("SELECT relpath, sha256 FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise KeyError(f"no file {file_id}")
        return thumbs.ensure(self.root, self.abspath(row["relpath"]), row["sha256"], size)


def _open_paths(store: Path) -> tuple[set[str], str]:
    """Files under store that some process has open, and how that was found out:
    "proc" (Linux), "lsof" (macOS), or "unavailable" (then the set is empty)."""
    prefix = str(Path(store).resolve()) + os.sep
    found: set[str] = set()
    if sys.platform.startswith("linux") and Path("/proc").is_dir():
        for fd_dir in Path("/proc").glob("[0-9]*/fd"):
            try:
                for fd in fd_dir.iterdir():
                    try:
                        target = os.readlink(fd)
                    except OSError:
                        continue
                    if target.startswith(prefix):
                        found.add(target)
            except OSError:
                continue  # another user's process, or gone
        return found, "proc"
    if sys.platform == "darwin":
        import subprocess
        try:
            out = subprocess.run(["/usr/sbin/lsof", "-n", "-Fn"], capture_output=True, text=True,
                                 timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return found, "unavailable"
        for line in out.splitlines():
            if line.startswith("n") and line[1:].startswith(prefix):
                found.add(line[1:])
        return found, "lsof"
    return found, "unavailable"

