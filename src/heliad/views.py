"""Views: named selections of observations, such as an event.

The index (view_members) plus the exported manifest.csv are the view of record.
The folder of links next to the manifest is only a projection for people,
Finder and ad hoc tools: it can be deleted and rebuilt at any time, and no
Heliad-aware program should follow its links.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
from pathlib import Path

from .library import Library, now

MARKER = ".heliad-view"
LINK_MODES = ("symlink", "hardlink", "copy")


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "view"


def create(lib: Library, name: str, *, description: str | None = None,
           dataset_keys: list[str] | None = None, start: str | None = None,
           end: str | None = None, observation_ids: list[int] | None = None) -> dict:
    """Create or extend a view. Members are observations, so a view always resolves
    to the newest version of each file."""
    conn = lib.conn
    with lib._write_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT id FROM views WHERE name = ?", (name,)).fetchone()
            view_id = row["id"] if row else conn.execute(
                "INSERT INTO views(name, description, created_at) VALUES (?, ?, ?)",
                (name, description, now()),
            ).lastrowid
            ids = list(observation_ids or [])
            if dataset_keys or start or end:
                sql = ("SELECT o.id FROM observations o JOIN datasets d ON d.id = o.dataset_id "
                       "WHERE 1 = 1")
                args: list = []
                if dataset_keys:
                    sql += f" AND d.dataset_key IN ({','.join('?' * len(dataset_keys))})"
                    args += dataset_keys
                if start:
                    sql += " AND o.date_obs >= ?"
                    args.append(start)
                if end:
                    sql += " AND o.date_obs <= ?"
                    args.append(end)
                ids += [r["id"] for r in conn.execute(sql, args).fetchall()]
            conn.executemany(
                "INSERT OR IGNORE INTO view_members(view_id, observation_id) VALUES (?, ?)",
                [(view_id, i) for i in ids],
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    count = conn.execute(
        "SELECT COUNT(*) FROM view_members WHERE view_id = ?", (view_id,)
    ).fetchone()[0]
    return {"view_id": view_id, "name": name, "observations": count}


def members(lib: Library, name: str) -> list[dict]:
    rows = lib.conn.execute(
        "SELECT c.*, f.source_url, f.original_name, q.query_json "
        "FROM views v JOIN view_members m ON m.view_id = v.id "
        "JOIN current_files c ON c.observation_id = m.observation_id "
        "JOIN files f ON f.id = c.file_id LEFT JOIN queries q ON q.id = f.query_id "
        "WHERE v.name = ? ORDER BY c.label, c.date_obs", (name,)
    ).fetchall()
    return [dict(r) for r in rows]


def _link(target: Path, link: Path, modes: tuple[str, ...]) -> str:
    """Make one link, trying each mode in turn. Returns the mode that worked."""
    last: Exception | None = None
    for mode in modes:
        try:
            if mode == "symlink":
                os.symlink(os.path.relpath(target, link.parent), link)
            elif mode == "hardlink":
                os.link(target, link)
            else:
                shutil.copy2(target, link)
            return mode
        except (OSError, NotImplementedError) as exc:
            last = exc
            link.unlink(missing_ok=True)
    raise OSError(f"could not link {link} -> {target}: {last}")


def materialize(lib: Library, name: str, *, modes: tuple[str, ...] = LINK_MODES) -> dict:
    """Write views/<name>/ : manifest.csv plus one folder of links per dataset.

    Built in a temporary folder and swapped in, so a reader never sees half a view.
    Only folders Heliad made (marked with .heliad-view) are ever replaced.
    """
    rows = members(lib, name)
    final = lib.cfg.views_dir / slug(name)
    if final.exists() and not (final / MARKER).exists():
        raise FileExistsError(f"{final} exists and was not made by Heliad; not touching it")
    build = final.with_name(f".{final.name}.building")
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    used: dict[str, int] = {}
    fields = ["dataset", "date_obs", "version", "path", "sha256", "size",
              "source_url", "query_json", "link"]
    with open(build / "manifest.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            folder = build / slug(r["label"])
            folder.mkdir(exist_ok=True)
            link = folder / r["original_name"]
            if link.exists() or link.is_symlink():
                link = folder / f"{r['sha256'][:12]}_{r['original_name']}"
            mode = _link(lib.abspath(r["relpath"]), link, modes)
            used[mode] = used.get(mode, 0) + 1
            writer.writerow({
                "dataset": r["label"], "date_obs": r["date_obs"], "version": r["version"] or "",
                "path": r["relpath"], "sha256": r["sha256"], "size": r["size"],
                "source_url": r["source_url"] or "", "query_json": r["query_json"] or "",
                "link": link.relative_to(build).as_posix(),
            })
    (build / MARKER).write_text(f"Made by Heliad at {now()}. Rebuild with: heliad view rebuild\n")
    if final.exists():
        old = final.with_name(f".{final.name}.old")
        if old.exists():
            shutil.rmtree(old)
        final.rename(old)
        build.rename(final)
        shutil.rmtree(old)
    else:
        build.rename(final)
    return {"name": name, "path": str(final), "files": len(rows), "link_modes": used}


def rebuild_all(lib: Library, *, modes: tuple[str, ...] = LINK_MODES) -> list[dict]:
    names = [r["name"] for r in lib.conn.execute("SELECT name FROM views ORDER BY name")]
    return [materialize(lib, n, modes=modes) for n in names]
