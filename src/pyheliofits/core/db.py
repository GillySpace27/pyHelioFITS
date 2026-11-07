"""The SQLite index: the single source of truth for what the library holds.

One process writes (HelioFITS Core, through one connection). Everyone else
(the GUI, HelioFITS Studio, scripts) opens the file read-only. WAL mode means
those readers never block the writer and never see a half-written change.

The schema is the contract with those readers, so it is versioned: bump
SCHEMA_VERSION and add a step to MIGRATIONS for every change. meta also holds
reader_compat, the oldest schema a reader may know and still read this index
safely. Additive changes (new tables, new columns) leave it alone; a change that
would mislead an older reader raises it. Readers check reader_compat, never
schema_version, so adding a table does not lock Studio out.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 7
READER_COMPAT = 1  # see the module docstring

SCHEMA_V1 = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per kind of data: an instrument, channel and product level.
CREATE TABLE datasets (
    id           INTEGER PRIMARY KEY,
    dataset_key  TEXT NOT NULL UNIQUE,
    observatory  TEXT,
    instrument   TEXT,
    detector     TEXT,
    product      TEXT,
    level        TEXT,
    wavelength   REAL,          -- Angstrom; NULL when not a single passband
    label        TEXT NOT NULL
);

-- One row per observation. Reprocessed versions of the same observation
-- (FILEVRSN 0k, 0l, ...) are several files pointing at one observation;
-- current_file_id is the newest, which is what readers get by default.
CREATE TABLE observations (
    id              INTEGER PRIMARY KEY,
    obs_key         TEXT NOT NULL UNIQUE,
    dataset_id      INTEGER NOT NULL REFERENCES datasets(id),
    date_obs        TEXT NOT NULL,   -- ISO 8601, UTC, as written in the header
    current_file_id INTEGER REFERENCES files(id)
);
CREATE INDEX observations_dataset_time ON observations(dataset_id, date_obs);

-- Where a file came from: the structured query that found it, re-runnable.
CREATE TABLE queries (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,      -- e.g. "fido", "local"
    query_json  TEXT NOT NULL,      -- canonical JSON, see pyheliofits.core.query
    created_at  TEXT NOT NULL
);

CREATE TABLE files (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT NOT NULL UNIQUE,
    relpath         TEXT NOT NULL UNIQUE,   -- relative to the library root
    size            INTEGER NOT NULL,
    observation_id  INTEGER NOT NULL REFERENCES observations(id),
    version         TEXT,                   -- FILEVRSN or the _vNN in the name
    original_name   TEXT NOT NULL,
    source_url      TEXT,
    query_id        INTEGER REFERENCES queries(id),
    ingested_at     TEXT NOT NULL,
    header_json     TEXT NOT NULL           -- the keywords HelioFITS Core indexes
);
CREATE INDEX files_observation ON files(observation_id);

-- Header and WCS problems found on arrival.
CREATE TABLE checks (
    id        INTEGER PRIMARY KEY,
    file_id   INTEGER NOT NULL REFERENCES files(id),
    code      TEXT NOT NULL,
    severity  TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    message   TEXT NOT NULL
);
CREATE INDEX checks_file ON checks(file_id);

-- Download jobs. A job survives restarts: anything not 'done' is retried.
CREATE TABLE downloads (
    id           INTEGER PRIMARY KEY,
    query_id     INTEGER REFERENCES queries(id),
    url          TEXT,
    record_json  TEXT NOT NULL,     -- what the source needs to fetch it again
    status       TEXT NOT NULL CHECK (status IN ('queued', 'active', 'done', 'failed')),
    bytes_total  INTEGER,
    bytes_done   INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    file_id      INTEGER REFERENCES files(id),
    updated_at   TEXT NOT NULL
);
CREATE INDEX downloads_status ON downloads(status);

-- Views: named selections of observations, e.g. an event.
CREATE TABLE views (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL DEFAULT 'event',
    description  TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE view_members (
    view_id         INTEGER NOT NULL REFERENCES views(id) ON DELETE CASCADE,
    observation_id  INTEGER NOT NULL REFERENCES observations(id),
    PRIMARY KEY (view_id, observation_id)
);

-- What most readers want: the newest version of every observation.
CREATE VIEW current_files AS
SELECT f.id AS file_id, f.relpath, f.sha256, f.size, f.version,
       o.id AS observation_id, o.date_obs,
       d.id AS dataset_id, d.dataset_key, d.label, d.observatory, d.instrument,
       d.detector, d.product, d.level, d.wavelength
FROM observations o
JOIN files f    ON f.id = o.current_file_id
JOIN datasets d ON d.id = o.dataset_id;
"""

# Version 2: download jobs. A job is one query run against one source; the
# downloads rows it writes are the files it fetched, good or rejected.
SCHEMA_V2 = """
CREATE TABLE jobs (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,      -- fido | jsoc
    query_id     INTEGER NOT NULL REFERENCES queries(id),
    status       TEXT NOT NULL CHECK (status IN ('queued', 'active', 'done', 'failed', 'cancelled')),
    phase        TEXT,               -- searching | fetching | ingesting, while active
    files_total  INTEGER,
    files_done   INTEGER NOT NULL DEFAULT 0,
    result_json  TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX jobs_status ON jobs(status);
ALTER TABLE downloads ADD COLUMN job_id INTEGER REFERENCES jobs(id);
"""

# Version 3: a job can end partial (some files stored, some not). SQLite cannot
# change a CHECK constraint in place, so the table is rebuilt (migrate turns
# foreign keys off around it and checks them before committing).
SCHEMA_V3 = """
CREATE TABLE jobs_v3 (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,      -- fido | jsoc
    query_id     INTEGER NOT NULL REFERENCES queries(id),
    status       TEXT NOT NULL CHECK (status IN ('queued', 'active', 'done', 'partial', 'failed', 'cancelled')),
    phase        TEXT,               -- searching | fetching | ingesting, while active
    files_total  INTEGER,
    files_done   INTEGER NOT NULL DEFAULT 0,
    result_json  TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
INSERT INTO jobs_v3 SELECT id, source, query_id, status, phase, files_total, files_done,
    result_json, error, created_at, updated_at FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_v3 RENAME TO jobs;
CREATE INDEX jobs_status ON jobs(status);
"""

# Version 4: the cache editor. A rejected observation (a bad frame someone set
# aside) leaves current_files, so readers stop seeing it; its files stay in the
# store and unrejecting brings it back. current_files keeps exactly its columns,
# so reader_compat stays 1. A job can carry options (an upgrade's missing_only).
SCHEMA_V4 = """
CREATE TABLE rejections (
    observation_id  INTEGER PRIMARY KEY REFERENCES observations(id),
    reason          TEXT NOT NULL,
    rejected_at     TEXT NOT NULL
);
ALTER TABLE jobs ADD COLUMN options_json TEXT;
DROP VIEW current_files;
CREATE VIEW current_files AS
SELECT f.id AS file_id, f.relpath, f.sha256, f.size, f.version,
       o.id AS observation_id, o.date_obs,
       d.id AS dataset_id, d.dataset_key, d.label, d.observatory, d.instrument,
       d.detector, d.product, d.level, d.wavelength
FROM observations o
JOIN files f    ON f.id = o.current_file_id
JOIN datasets d ON d.id = o.dataset_id
WHERE NOT EXISTS (SELECT 1 FROM rejections r WHERE r.observation_id = o.id);
"""

# Version 5: stable ids. dataset_key is derived from header rules, so a rules change
# (PUNCH 530 -> 5300) renames a dataset; dataset_uid does not change with it. reindex
# carries the uid to the renamed dataset and records the old key in dataset_aliases,
# so a link or a Studio session that saved the old key still finds the dataset.
# file_aliases does the same for a stored file whose relpath changed. Existing rows
# get a uid in the Python step below (SQLite has no UUID function). current_files
# gains dataset_uid as its last column; every earlier column keeps its name and
# meaning, so reader_compat stays 1.
SCHEMA_V5 = """
ALTER TABLE datasets ADD COLUMN dataset_uid TEXT;
ALTER TABLE views ADD COLUMN view_uid TEXT;
CREATE TABLE dataset_aliases (
    old_key      TEXT PRIMARY KEY,   -- a dataset_key this dataset had before
    dataset_uid  TEXT NOT NULL,
    recorded_at  TEXT
);
CREATE TABLE file_aliases (
    old_relpath  TEXT PRIMARY KEY,   -- where the file was stored before
    file_id      INTEGER NOT NULL REFERENCES files(id),
    sha256       TEXT NOT NULL,
    recorded_at  TEXT
);
DROP VIEW current_files;
CREATE VIEW current_files AS
SELECT f.id AS file_id, f.relpath, f.sha256, f.size, f.version,
       o.id AS observation_id, o.date_obs,
       d.id AS dataset_id, d.dataset_key, d.label, d.observatory, d.instrument,
       d.detector, d.product, d.level, d.wavelength, d.dataset_uid
FROM observations o
JOIN files f    ON f.id = o.current_file_id
JOIN datasets d ON d.id = o.dataset_id
WHERE NOT EXISTS (SELECT 1 FROM rejections r WHERE r.observation_id = o.id);
"""


def new_uid() -> str:
    """A dataset_uid or view_uid: a random UUID (version 4) as lowercase text."""
    return str(uuid.uuid4())


def _mint_uids(conn: sqlite3.Connection) -> None:
    """Schema 5, after its SQL: a uid for every dataset and view, then the unique indexes."""
    for table, column in (("datasets", "dataset_uid"), ("views", "view_uid")):
        ids = [r[0] for r in conn.execute(f"SELECT id FROM {table} WHERE {column} IS NULL")]
        conn.executemany(f"UPDATE {table} SET {column} = ? WHERE id = ?", [(new_uid(), i) for i in ids])
        conn.execute(f"CREATE UNIQUE INDEX {table}_{column} ON {table}({column})")


# Version 6: every URL a stored file came from. files.source_url keeps the first one only, and
# a second download of the same bytes (another mirror, a re-fetch) is a duplicate that used to
# leave no trace, so a program that looks files up by URL (Studio, once the library is its one
# store) would fetch it again forever. file_sources maps each URL to the file it delivered; a URL
# that later delivers other bytes points at the newer file. Additive: reader_compat stays 1.
SCHEMA_V6 = """
CREATE TABLE file_sources (
    url          TEXT PRIMARY KEY,
    file_id      INTEGER NOT NULL REFERENCES files(id),
    recorded_at  TEXT
);
CREATE INDEX file_sources_file ON file_sources(file_id);
INSERT OR IGNORE INTO file_sources(url, file_id, recorded_at)
    SELECT source_url, id, ingested_at FROM files WHERE source_url IS NOT NULL AND source_url != '';
"""

# Version 7: the per-frame bodies tag (pyheliofits.core.bodies): which solar-system bodies each
# file's own WCS puts in its field, so "a date without the Moon" is a query. A file without a row
# is untagged, not body-free. Additive: reader_compat stays 1.
SCHEMA_V7 = """
CREATE TABLE file_bodies (
    file_id      INTEGER PRIMARY KEY REFERENCES files(id),
    bodies       TEXT,            -- JSON list of the bodies in the field; NULL: no solar sky WCS
    detail_json  TEXT NOT NULL,   -- every body's pixel, elongation, in_field and hidden
    method       TEXT NOT NULL,   -- bodies.METHOD when tagged
    tagged_at    TEXT NOT NULL
);
"""


MIGRATIONS: dict[int, str] = {1: SCHEMA_V1, 2: SCHEMA_V2, 3: SCHEMA_V3, 4: SCHEMA_V4, 5: SCHEMA_V5, 6: SCHEMA_V6,
                              7: SCHEMA_V7}
# Steps SQL cannot express, run after a version's SQL in the same transaction.
MIGRATION_STEPS: dict[int, Callable[[sqlite3.Connection], None]] = {5: _mint_uids}


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")


def connect_writer(path: Path) -> sqlite3.Connection:
    """The one read-write connection. Creates and migrates the database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    _configure(conn)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn, backup_to=path)
    return conn


def connect_reader(path: Path) -> sqlite3.Connection:
    """A read-only connection, the way every other program should open the index."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    _configure(conn)
    return conn


def meta_value(conn: sqlite3.Connection, key: str) -> str | None:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if not has_meta:
        return None
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)),
    )


def schema_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    """Whether the index has this table: a read-only Library may hold an older schema."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Whether a table has a column: a read-only Library may hold an older schema."""
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def migrate(conn: sqlite3.Connection, backup_to: Path | None = None) -> None:
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"index has schema {current}, newer than this HelioFITS Core ({SCHEMA_VERSION}); update HelioFITS Core"
        )
    if current == SCHEMA_VERSION:
        return
    if current > 0 and backup_to is not None:
        # The index as it was before this HelioFITS Core changed it, so a migration that goes
        # wrong costs nothing: hfcore.sqlite.schema4.bak, say. An older backup is kept.
        target = backup_to.with_name(f"{backup_to.name}.schema{current}.bak")
        if not target.exists():
            copy = sqlite3.connect(target)
            try:
                conn.backup(copy)
            finally:
                copy.close()
    # Rebuilding a table that others reference needs foreign keys off (it has no
    # effect inside a transaction); each step checks them itself before committing.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for version in range(current + 1, SCHEMA_VERSION + 1):
            _migrate_to(conn, version)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_to(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in _split(MIGRATIONS[version]):
            conn.execute(statement)
        if version in MIGRATION_STEPS:
            MIGRATION_STEPS[version](conn)
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(f"migration to schema {version} broke {len(broken)} references")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('reader_compat', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(READER_COMPAT),),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _split(script: str) -> list[str]:
    """Split a schema script into statements (executescript would commit on its own)."""
    statements, buf = [], []
    for line in script.splitlines():
        stripped = line.split("--", 1)[0]
        buf.append(stripped)
        if stripped.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
