"""SQLite schema for coach.db — the rebuildable training-data cache.

Invariant: every table here is derivable from ``data/raw/`` + ``plans/``.
Nothing lives only in coach.db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .paths import coach_db_path

SCHEMA_VERSION = 1

_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS activities (
        id                  TEXT PRIMARY KEY,
        start_date          TIMESTAMP NOT NULL,
        sport               TEXT NOT NULL,
        duration_s          INTEGER,
        distance_m          REAL,
        tss                 REAL,
        np                  REAL,
        intensity_factor    REAL,
        avg_hr              INTEGER,
        max_hr              INTEGER,
        decoupling          REAL,
        elevation_gain_m    REAL,
        planned_session_id  TEXT,
        plan_id             TEXT,
        raw_json_path       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wellness_daily (
        date            DATE PRIMARY KEY,
        sleep_h         REAL,
        sleep_score     INTEGER,
        hrv             REAL,
        rhr             INTEGER,
        readiness       INTEGER,
        body_weight_kg  REAL,
        soreness        TEXT,
        notes           TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS load_daily (
        date        DATE PRIMARY KEY,
        ctl         REAL,
        atl         REAL,
        tsb         REAL,
        ctl_bike    REAL,
        ctl_run     REAL,
        ctl_swim    REAL,
        ramp_7d     REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions_planned (
        id                      TEXT PRIMARY KEY,
        plan_id                 TEXT,
        week_id                 TEXT,
        date                    DATE,
        sport                   TEXT,
        library_ref             TEXT,
        target_tss              REAL,
        target_duration_s       INTEGER,
        purpose                 TEXT,
        notes                   TEXT,
        pushed_to_intervals     INTEGER NOT NULL DEFAULT 0,
        intervals_event_id      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS adherence (
        planned_session_id  TEXT PRIMARY KEY REFERENCES sessions_planned(id),
        activity_id         TEXT REFERENCES activities(id),
        completed           INTEGER,
        tss_delta           REAL,
        duration_delta_s    INTEGER,
        reason              TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp       TIMESTAMP NOT NULL,
        scope           TEXT NOT NULL,
        kind            TEXT NOT NULL,
        rationale       TEXT NOT NULL,
        changed_files   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS _schema_migrations (
        version     INTEGER PRIMARY KEY,
        applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
)

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_date)",
    "CREATE INDEX IF NOT EXISTS idx_activities_sport_start ON activities(sport, start_date)",
    "CREATE INDEX IF NOT EXISTS idx_sp_week ON sessions_planned(week_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_scope ON decisions(scope)",
)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with WAL mode + foreign keys on.

    Passing ``None`` uses the default ``data/coach.db`` path.
    """
    db_path = Path(path) if path is not None else coach_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        db_path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        isolation_level=None,  # autocommit; use explicit transactions
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply DDL idempotently and record the schema version."""
    with conn:  # transaction
        for ddl in _TABLES:
            conn.execute(ddl)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.execute(
            "INSERT OR IGNORE INTO _schema_migrations(version) VALUES (?)",
            (SCHEMA_VERSION,),
        )


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MAX(version) AS v FROM _schema_migrations"
    ).fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0
