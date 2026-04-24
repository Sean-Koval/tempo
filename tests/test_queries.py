"""Tests for tempo.queries — the shared read layer.

Mirrors coach-db's test_sql.py in structure: seed an in-memory DB, call the
pure function, assert on dataclass fields. Keeps both test suites green across
the extraction.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from tempo import queries
from tempo.db import init_schema


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _seed_activity(
    conn: sqlite3.Connection,
    *,
    id_: str,
    start: str,
    sport: str,
    duration_s: int,
    tss: float,
    decoupling: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO activities (id, start_date, sport, duration_s, tss, decoupling) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id_, start, sport, duration_s, tss, decoupling),
    )


def test_query_activities_filters_all_params(conn: sqlite3.Connection) -> None:
    _seed_activity(conn, id_="a1", start="2026-04-01T10:00:00", sport="ride",
                   duration_s=12000, tss=220.0, decoupling=3.5)
    _seed_activity(conn, id_="a2", start="2026-04-02T08:00:00", sport="ride",
                   duration_s=5400, tss=80.0, decoupling=8.1)
    _seed_activity(conn, id_="a3", start="2026-04-03T06:00:00", sport="run",
                   duration_s=3600, tss=60.0)

    got = queries.query_activities(conn, sport="ride")
    assert [a.id for a in got] == ["a2", "a1"]  # newest first

    got = queries.query_activities(
        conn, sport="ride", min_duration_s=10000, max_decoupling_pct=5.0
    )
    assert [a.id for a in got] == ["a1"]

    # Bare-date end acts inclusive (T23:59:59 pad).
    got = queries.query_activities(conn, start="2026-04-02", end="2026-04-03")
    assert {a.id for a in got} == {"a2", "a3"}


def test_query_activities_limit(conn: sqlite3.Connection) -> None:
    for i in range(5):
        _seed_activity(conn, id_=f"x{i}", start=f"2026-03-0{i+1}T10:00:00",
                       sport="ride", duration_s=3600, tss=50.0)
    got = queries.query_activities(conn, limit=2)
    assert len(got) == 2


def test_query_activities_tss_bounds(conn: sqlite3.Connection) -> None:
    _seed_activity(conn, id_="low", start="2026-04-01T10:00:00", sport="ride",
                   duration_s=3600, tss=40.0)
    _seed_activity(conn, id_="mid", start="2026-04-02T10:00:00", sport="ride",
                   duration_s=3600, tss=80.0)
    _seed_activity(conn, id_="hi", start="2026-04-03T10:00:00", sport="ride",
                   duration_s=3600, tss=200.0)
    got = queries.query_activities(conn, min_tss=60.0, max_tss=150.0)
    assert [a.id for a in got] == ["mid"]


def test_get_load_curve_returns_inclusive_range(conn: sqlite3.Connection) -> None:
    for i, d in enumerate(["2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02"]):
        conn.execute(
            "INSERT INTO load_daily (date, ctl, atl, tsb, ramp_7d) VALUES (?, ?, ?, ?, ?)",
            (d, 50.0 + i, 45.0 + i, 5.0, 0.5),
        )

    pts = queries.get_load_curve(
        conn, start_date="2026-03-31", end_date="2026-04-01"
    )
    assert [p.date for p in pts] == ["2026-03-31", "2026-04-01"]
    assert pts[0].ctl == pytest.approx(51.0)


def test_get_readiness_computes_trend(conn: sqlite3.Connection) -> None:
    as_of = date(2026, 4, 15)
    for i in range(7):
        d = (as_of - timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO wellness_daily (date, sleep_h, hrv, rhr, readiness, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (d, 7.5, 70.0, 52, 8, "feeling good" if i == 0 else None),
        )
    for i in range(7, 14):
        d = (as_of - timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO wellness_daily (date, sleep_h, hrv, rhr, readiness) "
            "VALUES (?, ?, ?, ?, ?)",
            (d, 7.0, 60.0, 55, 7),
        )

    snap = queries.get_readiness(conn, as_of=as_of.isoformat(), window_days=14)
    assert snap.samples == 14
    assert snap.hrv_7d_mean == pytest.approx(70.0)
    assert snap.hrv_trend_delta == pytest.approx(10.0)
    assert snap.notes_latest == "feeling good"
    assert snap.sleep_h_latest == pytest.approx(7.5)


def test_get_readiness_empty_window(conn: sqlite3.Connection) -> None:
    snap = queries.get_readiness(conn, as_of="2026-04-15", window_days=7)
    assert snap.samples == 0
    assert snap.hrv_latest is None
    assert snap.hrv_trend_delta is None


def test_get_wellness_range_returns_daily_rows_ascending(
    conn: sqlite3.Connection,
) -> None:
    for i, (sleep, hrv, rhr, read) in enumerate(
        [(7.5, 70.0, 52, 8), (6.5, 65.0, 55, 6), (8.0, 72.0, 50, 9)]
    ):
        d = (date(2026, 4, 20) + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO wellness_daily (date, sleep_h, hrv, rhr, readiness) "
            "VALUES (?, ?, ?, ?, ?)",
            (d, sleep, hrv, rhr, read),
        )
    rows = queries.get_wellness_range(
        conn, start_date="2026-04-20", end_date="2026-04-22"
    )
    assert [r.date for r in rows] == ["2026-04-20", "2026-04-21", "2026-04-22"]
    assert rows[0].sleep_h == pytest.approx(7.5)
    assert rows[2].readiness == 9


def test_get_wellness_range_empty(conn: sqlite3.Connection) -> None:
    assert queries.get_wellness_range(
        conn, start_date="2026-04-20", end_date="2026-04-22"
    ) == []


def test_get_adherence_counts_and_totals(conn: sqlite3.Connection) -> None:
    rows = [
        ("sp1", "2026-04-20", "ride", "long_ride_z2", 200.0),
        ("sp2", "2026-04-22", "run", "threshold_run", 85.0),
        ("sp3", "2026-04-23", "swim", "aerobic_swim_set", 55.0),
    ]
    for sid, d, sport, lib, tss in rows:
        conn.execute(
            "INSERT INTO sessions_planned (id, week_id, date, sport, library_ref, target_tss) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "2026-W17", d, sport, lib, tss),
        )
    _seed_activity(conn, id_="act1", start="2026-04-20T07:00:00", sport="ride",
                   duration_s=10800, tss=210.0)
    conn.execute(
        "INSERT INTO adherence (planned_session_id, activity_id, completed, "
        "tss_delta, duration_delta_s, reason) VALUES (?, ?, ?, ?, ?, ?)",
        ("sp1", "act1", 1, 10.0, 0, "completed"),
    )
    conn.execute(
        "INSERT INTO adherence (planned_session_id, completed, reason) VALUES (?, ?, ?)",
        ("sp2", 0, "skipped:illness"),
    )

    rep = queries.get_adherence(conn, week_id="2026-W17")
    assert rep.planned_count == 3
    assert rep.completed_count == 1
    assert rep.skipped_count == 1
    assert rep.completion_pct == pytest.approx(33.3, abs=0.1)
    assert rep.total_planned_tss == pytest.approx(340.0)
    assert rep.total_actual_tss == pytest.approx(210.0)
    assert len(rep.items) == 3


def test_compare_plan_to_actual_missing_session_surfaces(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO sessions_planned (id, week_id, date, sport, library_ref, target_tss, "
        "target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp1", "2026-W17", "2026-04-20", "ride", "long_ride_z2", 200.0, 10800, "aerobic_base"),
    )
    deltas = queries.compare_plan_to_actual(conn, week_id="2026-W17")
    assert len(deltas) == 1
    d = deltas[0]
    assert d.planned_session_id == "sp1"
    assert d.activity_id is None
    assert d.actual_tss is None
    assert d.tss_delta is None  # can't compute delta without actual


def test_compare_plan_to_actual_computes_deltas(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO sessions_planned (id, week_id, date, sport, library_ref, target_tss, "
        "target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp1", "2026-W17", "2026-04-20", "ride", "long_ride_z2", 200.0, 10800, "aerobic_base"),
    )
    _seed_activity(conn, id_="act1", start="2026-04-20T07:00:00", sport="ride",
                   duration_s=11400, tss=215.0)
    conn.execute(
        "INSERT INTO adherence (planned_session_id, activity_id, completed, reason) "
        "VALUES (?, ?, ?, ?)",
        ("sp1", "act1", 1, "completed"),
    )
    deltas = queries.compare_plan_to_actual(conn, week_id="2026-W17")
    assert deltas[0].tss_delta == pytest.approx(15.0)
    assert deltas[0].duration_delta_s == 600
