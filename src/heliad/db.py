"""The SQLite index: the single source of truth for what the library holds.

One process writes (the Heliad core, through one connection). Everyone else
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
from pathlib import Path

SCHEMA_VERSION = 3
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
    query_json  TEXT NOT NULL,      -- canonical JSON, see heliad.query
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
    header_json     TEXT NOT NULL           -- the keywords Heliad indexes
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

MIGRATIONS: dict[int, str] = {1: SCHEMA_V1, 2: SCHEMA_V2, 3: SCHEMA_V3}


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
    migrate(conn)
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


def migrate(conn: sqlite3.Connection) -> None:
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"index has schema {current}, newer than this Heliad ({SCHEMA_VERSION}); update Heliad"
        )
    if current == SCHEMA_VERSION:
        return
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
