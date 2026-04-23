"""Tests for the SQLite schema module."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempo.db import SCHEMA_VERSION, connect, current_schema_version, init_schema


@pytest.fixture
def conn(tmp_data_dir: Path):
    c = connect(tmp_data_dir / "coach.db")
    init_schema(c)
    yield c
    c.close()


EXPECTED_TABLES = {
    "activities",
    "wellness_daily",
    "load_daily",
    "sessions_planned",
    "adherence",
    "decisions",
    "_schema_migrations",
}


def test_creates_all_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_creates_expected_indexes(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert {
        "idx_activities_start",
        "idx_activities_sport_start",
        "idx_sp_week",
        "idx_decisions_scope",
    } <= names


def test_wal_mode_enabled(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
    assert mode.lower() == "wal"


def test_foreign_keys_enforced(conn):
    conn.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport) "
        "VALUES ('s1', 'p1', '2026-W17', '2026-04-27', 'bike')"
    )
    # adherence with valid FK works
    conn.execute(
        "INSERT INTO adherence(planned_session_id, completed) VALUES ('s1', 1)"
    )
    # adherence with unknown planned_session_id fails
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO adherence(planned_session_id, completed) VALUES ('nope', 1)"
        )


def test_init_schema_is_idempotent(tmp_data_dir: Path):
    c = connect(tmp_data_dir / "coach.db")
    init_schema(c)
    init_schema(c)  # second run must not raise or error
    init_schema(c)
    # Still exactly one row in migrations table
    rows = c.execute("SELECT version FROM _schema_migrations").fetchall()
    assert [r["version"] for r in rows] == [SCHEMA_VERSION]


def test_schema_version_reported(conn):
    assert current_schema_version(conn) == SCHEMA_VERSION


def test_connect_defaults_to_data_dir(tmp_data_dir: Path):
    c = connect()
    try:
        init_schema(c)
        assert (tmp_data_dir / "coach.db").exists()
    finally:
        c.close()


def test_decisions_autoincrement(conn):
    conn.execute(
        "INSERT INTO decisions(timestamp, scope, kind, rationale) "
        "VALUES ('2026-04-23T12:00:00', 'week:2026-W17', 'plan', 'why')"
    )
    conn.execute(
        "INSERT INTO decisions(timestamp, scope, kind, rationale) "
        "VALUES ('2026-04-23T13:00:00', 'session:x', 'adjust', 'why2')"
    )
    rows = conn.execute("SELECT id FROM decisions ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == [1, 2]
