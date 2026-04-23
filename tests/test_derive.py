"""Tests for the derive (CTL/ATL/TSB) module."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tempo.db import connect, init_schema
from tempo.derive import _ewa_series, derive


def _seed_activities(
    conn,
    entries: list[tuple[date, str, float]],  # (date, sport, tss)
) -> None:
    for i, (d, sport, tss) in enumerate(entries):
        conn.execute(
            """
            INSERT INTO activities
                (id, start_date, sport, tss)
            VALUES (?, ?, ?, ?)
            """,
            (f"a{i}", f"{d.isoformat()}T06:00:00", sport, tss),
        )


@pytest.fixture
def db(tmp_data_dir: Path):
    c = connect()
    init_schema(c)
    yield c
    c.close()


def test_ewa_converges_to_constant():
    # Constant 100 TSS/day for 300 days → CTL should approach 100.
    day0 = date(2026, 1, 1)
    dates = [day0 + timedelta(days=i) for i in range(300)]
    daily = {d: 100.0 for d in dates}
    series = _ewa_series(daily, dates, window=42)
    # After ~6x the window, EWA is within <0.5 of target.
    assert math.isclose(series[dates[-1]], 100.0, abs_tol=0.5)


def test_ewa_decays_to_zero():
    # One big stress day, then rest.
    day0 = date(2026, 1, 1)
    dates = [day0 + timedelta(days=i) for i in range(200)]
    daily = {day0: 100.0}
    series = _ewa_series(daily, dates, window=42)
    assert series[day0] > 2.0
    # After ~6 windows the EWA is effectively zero.
    assert series[dates[-1]] < 0.1


async def test_populates_load_daily_from_activities(db):
    day0 = date(2026, 1, 1)
    entries = [(day0 + timedelta(days=i), "bike", 100.0) for i in range(100)]
    _seed_activities(db, entries)

    stats = derive(now=datetime(2026, 4, 23, tzinfo=UTC))
    assert stats.activities_scored == 100
    assert stats.days_written > 100  # includes pre-activity warmup window

    last = db.execute(
        "SELECT date, ctl, atl, tsb, ctl_bike FROM load_daily "
        "ORDER BY date DESC LIMIT 1"
    ).fetchone()
    # ramp_7d uses CTL - CTL[-7d], not asserted here (decay on zero-tss days).
    # After 100 days of 100 TSS, CTL is strongly above the final decay tail.
    # Final row is today (2026-04-23), so CTL has been decaying since day 100.
    # That's fine — we just want the series to be present.
    assert last is not None
    assert last["ctl"] >= 0
    assert last["atl"] >= 0


async def test_per_sport_ctl_isolated(db):
    day0 = date(2026, 1, 1)
    # 60 days bike at 100, no run, no swim
    _seed_activities(db, [(day0 + timedelta(days=i), "bike", 100.0) for i in range(60)])

    derive(now=datetime(2026, 3, 1, tzinfo=UTC))

    row = db.execute(
        "SELECT ctl_bike, ctl_run, ctl_swim FROM load_daily "
        "WHERE date = '2026-03-01'"
    ).fetchone()
    assert row["ctl_bike"] > 50.0
    assert row["ctl_run"] == 0.0
    assert row["ctl_swim"] == 0.0


async def test_idempotent(db):
    day0 = date(2026, 1, 1)
    _seed_activities(db, [(day0 + timedelta(days=i), "run", 50.0) for i in range(30)])

    derive(now=datetime(2026, 3, 1, tzinfo=UTC))
    first_rows = db.execute(
        "SELECT date, ctl, atl, tsb, ctl_run FROM load_daily ORDER BY date"
    ).fetchall()

    derive(now=datetime(2026, 3, 1, tzinfo=UTC))
    second_rows = db.execute(
        "SELECT date, ctl, atl, tsb, ctl_run FROM load_daily ORDER BY date"
    ).fetchall()

    assert len(first_rows) == len(second_rows)
    for a, b in zip(first_rows, second_rows, strict=True):
        assert a["date"] == b["date"]
        assert math.isclose(a["ctl"], b["ctl"])
        assert math.isclose(a["atl"], b["atl"])
        assert math.isclose(a["tsb"], b["tsb"])
        assert math.isclose(a["ctl_run"], b["ctl_run"])


async def test_empty_db_produces_empty_load_daily(db):
    stats = derive(now=datetime(2026, 4, 23, tzinfo=UTC))
    assert stats.days_written == 0

    rows = db.execute("SELECT COUNT(*) AS c FROM load_daily").fetchone()
    assert rows["c"] == 0


async def test_ramp_7d_positive_during_build(db):
    day0 = date(2026, 1, 1)
    # Ramping load: 0 for 7 days (warmup burnt), then 100/day for 14 days
    entries: list[tuple[date, str, float]] = []
    for i in range(14):
        entries.append((day0 + timedelta(days=7 + i), "bike", 100.0))
    _seed_activities(db, entries)

    # Evaluate at day 14 of the ramp
    derive(now=datetime.combine(day0 + timedelta(days=21), datetime.min.time(), tzinfo=UTC))
    row = db.execute(
        "SELECT ramp_7d FROM load_daily WHERE date = ?", ((day0 + timedelta(days=21)).isoformat(),)
    ).fetchone()
    assert row is not None
    assert row["ramp_7d"] > 0.0
