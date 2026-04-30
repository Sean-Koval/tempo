"""Tests for tempo.patterns — the adherence pattern miner."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from tempo import patterns
from tempo.db import connect, init_schema


def _open_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    conn = connect()
    init_schema(conn)
    return conn


def _insert_session(
    conn: sqlite3.Connection,
    *,
    sid: str,
    week_id: str,
    d: str,
    sport: str = "ride",
    library_ref: str | None = None,
    purpose: str | None = None,
    target_tss: float = 60.0,
    completed: bool = True,
    reason: str = "completed",
) -> None:
    conn.execute(
        "INSERT INTO sessions_planned "
        "(id, plan_id, week_id, date, sport, library_ref, purpose, target_tss) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, "p", week_id, d, sport, library_ref, purpose, target_tss),
    )
    conn.execute(
        "INSERT INTO adherence (planned_session_id, activity_id, completed, reason) "
        "VALUES (?, ?, ?, ?)",
        (sid, None, 1 if completed else 0, reason),
    )


def _seed_8wk_uniform(
    conn: sqlite3.Connection, *, end_date: date, completion_each_day: int = 1
) -> None:
    """8 weeks ending the Sunday before ``end_date``. Each weekday has one
    planned ride. By default all complete."""
    week_end_sunday = end_date - timedelta(days=end_date.weekday() + 1)  # last Sunday
    for w in range(8):
        sunday = week_end_sunday - timedelta(weeks=w)
        monday = sunday - timedelta(days=6)
        for d_off in range(7):
            day = monday + timedelta(days=d_off)
            sid = f"s-{day.isoformat()}"
            _insert_session(
                conn,
                sid=sid,
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                completed=bool(completion_each_day),
            )


def test_insufficient_data_below_window(tmp_path, monkeypatch):
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    # Only 3 weeks of data.
    for w in range(3):
        sunday = end - timedelta(weeks=w + 1)
        monday = sunday - timedelta(days=6)
        for d_off in range(3):
            day = monday + timedelta(days=d_off)
            _insert_session(
                conn,
                sid=f"s-{day.isoformat()}",
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
            )

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    assert out["status"] == "insufficient_data"
    assert out["weeks_observed"] == 3
    assert out["weeks_required"] == 8
    assert out["signals"] == []
    assert "only 3 weeks" in out["reason"]


def test_uniform_8wk_no_signals(tmp_path, monkeypatch):
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    _seed_8wk_uniform(conn, end_date=end, completion_each_day=1)

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    assert out["status"] == "ok"
    assert out["weeks_observed"] >= 8
    assert out["baseline_completion_rate"] == 1.0
    # All buckets are degenerate (rate=baseline=1.0) → no signals.
    assert out["signals"] == []


def test_thursday_dropoff_flagged(tmp_path, monkeypatch):
    """Acceptance: 8 weeks, Thu 4/8 vs others 7/8 → flag Thu as warn."""
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)  # Sunday
    week_end_sunday = end
    # Build: 8 weeks, 1 session/day, Thu completes 50%, others ~88%.
    for w in range(8):
        sunday = week_end_sunday - timedelta(weeks=w)
        monday = sunday - timedelta(days=6)
        for d_off in range(7):
            day = monday + timedelta(days=d_off)
            wd = day.weekday()  # 0=Mon..6=Sun
            sid = f"s-{day.isoformat()}"
            if wd == 3:  # Thursday
                completed = w % 2 == 0  # 4 of 8 Thursdays complete
                reason = "completed" if completed else "skipped: schedule"
            else:
                completed = w != 0  # 7/8 days complete (skip first week's day)
                reason = "completed" if completed else "skipped: misc"
            _insert_session(
                conn,
                sid=sid,
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                completed=completed,
                reason=reason,
            )

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    assert out["status"] == "ok"
    weekday_signals = [s for s in out["signals"] if s["dimension"] == "weekday"]
    thu = next((s for s in weekday_signals if s["value"] == "Thu"), None)
    assert thu is not None, f"Thu not flagged: {weekday_signals}"
    assert thu["samples"] == 8
    assert thu["completion_rate"] == 0.5
    assert thu["z_score"] <= -1.5
    assert thu["severity"] == "warn"
    assert "Thu" in thu["message"]
    # Weekday baseline is high, so no other weekday should flag.
    other_weekdays = [s for s in weekday_signals if s["value"] != "Thu"]
    assert other_weekdays == []


def test_sport_dropoff_flagged(tmp_path, monkeypatch):
    """Swim 2/8 vs ride 8/8, run 8/8 → flag swim."""
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    week_end_sunday = end
    for w in range(8):
        sunday = week_end_sunday - timedelta(weeks=w)
        monday = sunday - timedelta(days=6)
        # Mon ride, Wed run, Fri swim — one of each per week.
        rides = [(monday, "ride", "ride")]
        runs = [(monday + timedelta(days=2), "run", "run")]
        swims = [(monday + timedelta(days=4), "swim", "swim")]
        for day, sport, _ in rides + runs:
            sid = f"{sport}-{day.isoformat()}"
            _insert_session(
                conn,
                sid=sid,
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                sport=sport,
                completed=True,
            )
        for day, sport, _ in swims:
            sid = f"{sport}-{day.isoformat()}"
            _insert_session(
                conn,
                sid=sid,
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                sport=sport,
                completed=w < 2,  # 2 of 8 swims complete
            )

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    sport_signals = [s for s in out["signals"] if s["dimension"] == "sport"]
    swim = next((s for s in sport_signals if s["value"] == "swim"), None)
    assert swim is not None
    assert swim["samples"] == 8
    assert swim["completion_rate"] == 0.25
    assert swim["z_score"] <= -1.5


def test_session_type_dropoff_uses_purpose(tmp_path, monkeypatch):
    """Long-ride miss rate vs other rides — purpose-bucketed."""
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    week_end_sunday = end
    for w in range(8):
        sunday = week_end_sunday - timedelta(weeks=w)
        monday = sunday - timedelta(days=6)
        # 1 long ride (Sat) + 4 z2 rides (Mon-Thu).
        long_day = monday + timedelta(days=5)
        _insert_session(
            conn,
            sid=f"long-{long_day.isoformat()}",
            week_id=f"{long_day.isocalendar()[0]:04d}-W{long_day.isocalendar()[1]:02d}",
            d=long_day.isoformat(),
            sport="ride",
            purpose="long_ride_endurance",
            completed=w < 2,  # 2/8 long rides complete
        )
        for d_off in range(4):
            day = monday + timedelta(days=d_off)
            _insert_session(
                conn,
                sid=f"z2-{day.isoformat()}",
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                sport="ride",
                purpose="z2_endurance",
                completed=True,
            )

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    type_signals = [s for s in out["signals"] if s["dimension"] == "session_type"]
    long_ride = next(
        (s for s in type_signals if s["value"] == "long_ride_endurance"), None
    )
    assert long_ride is not None
    assert long_ride["samples"] == 8
    assert long_ride["completion_rate"] == 0.25


def test_travel_week_context_via_journal(tmp_path, monkeypatch):
    """Journal text ("travel" keyword) tags weeks, miss rate flagged in those."""
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    week_end_sunday = end

    # 8 weeks, 5 sessions/wk, all complete EXCEPT in 2 travel weeks where 4/5 miss.
    travel_week_indexes = {2, 5}  # weeks-ago indexes
    travel_dates: list[date] = []
    for w in range(8):
        sunday = week_end_sunday - timedelta(weeks=w)
        monday = sunday - timedelta(days=6)
        is_travel = w in travel_week_indexes
        if is_travel:
            travel_dates.append(monday + timedelta(days=2))
        for d_off in range(5):
            day = monday + timedelta(days=d_off)
            completed = (not is_travel) or (d_off == 0)
            _insert_session(
                conn,
                sid=f"s-{day.isoformat()}",
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                completed=completed,
                reason="completed" if completed else "skipped: out of office",
            )

    journal = tmp_path / "journal"
    journal.mkdir()
    for d in travel_dates:
        (journal / f"{d.isoformat()}.md").write_text(
            f"# {d.isoformat()}\n\nOn the road for a work trip — minimal training window.\n",
            encoding="utf-8",
        )

    out = patterns.adherence_patterns(
        conn, window_weeks=8, end_date=end, journal_dir=journal
    )
    ctx_signals = [s for s in out["signals"] if s["dimension"] == "context"]
    travel = next((s for s in ctx_signals if s["value"] == "travel_week"), None)
    assert travel is not None
    assert travel["samples"] == 10  # 2 weeks × 5 sessions
    assert travel["completion_rate"] == 0.2
    assert "travel_week" in travel["message"]
    # The home_week bucket — even at 100% — should never be emitted.
    assert all(s["value"] != "home_week" for s in ctx_signals)
    # And travel_weeks list is exposed for the brief consumer.
    assert len(out["travel_weeks"]) == 2


def test_travel_week_context_via_adherence_reason(tmp_path, monkeypatch):
    """Adherence reason "travel" alone is enough to tag a week — no journal needed."""
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    week_end_sunday = end

    for w in range(8):
        sunday = week_end_sunday - timedelta(weeks=w)
        monday = sunday - timedelta(days=6)
        is_travel = w in {1, 4}
        for d_off in range(5):
            day = monday + timedelta(days=d_off)
            if is_travel and d_off > 0:
                completed = False
                reason = "skipped: travel for client visit"
            else:
                completed = True
                reason = "completed"
            _insert_session(
                conn,
                sid=f"s-{day.isoformat()}",
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                completed=completed,
                reason=reason,
            )

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    travel = next(
        (s for s in out["signals"] if s["dimension"] == "context" and s["value"] == "travel_week"),
        None,
    )
    assert travel is not None
    assert travel["samples"] == 10
    assert travel["completion_rate"] == 0.2


def test_min_samples_filter(tmp_path, monkeypatch):
    """A bucket with <min_samples sessions cannot be flagged even if 0% complete."""
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    _seed_8wk_uniform(conn, end_date=end, completion_each_day=1)

    # Add 2 yoga sessions, both missed — only 2 samples, below default min=4.
    yoga_day = end - timedelta(days=14)
    for i in range(2):
        d = yoga_day + timedelta(days=i)
        _insert_session(
            conn,
            sid=f"yoga-{d.isoformat()}",
            week_id=f"{d.isocalendar()[0]:04d}-W{d.isocalendar()[1]:02d}",
            d=d.isoformat(),
            sport="yoga",
            completed=False,
            reason="skipped: schedule",
        )

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    sport_signals = [s for s in out["signals"] if s["dimension"] == "sport"]
    yoga = [s for s in sport_signals if s["value"] == "yoga"]
    assert yoga == [], "yoga should not flag — only 2 samples"


def test_default_end_date_is_today(tmp_path, monkeypatch):
    """end_date=None defaults to today and walks back window_weeks."""
    conn = _open_db(tmp_path, monkeypatch)
    today = date.today()
    _seed_8wk_uniform(conn, end_date=today, completion_each_day=1)

    out = patterns.adherence_patterns(conn, window_weeks=8)
    assert out["status"] == "ok"
    assert out["weeks_observed"] >= 8


def test_signal_dataclass_serialization(tmp_path, monkeypatch):
    """Signals are returned as dicts (JSON-friendly), not PatternSignal instances."""
    conn = _open_db(tmp_path, monkeypatch)
    end = date(2026, 4, 26)
    # Force one weekday signal.
    week_end_sunday = end
    for w in range(8):
        sunday = week_end_sunday - timedelta(weeks=w)
        monday = sunday - timedelta(days=6)
        for d_off in range(7):
            day = monday + timedelta(days=d_off)
            wd = day.weekday()
            completed = wd != 3 or w == 0  # Thu only completes 1/8
            _insert_session(
                conn,
                sid=f"s-{day.isoformat()}",
                week_id=f"{day.isocalendar()[0]:04d}-W{day.isocalendar()[1]:02d}",
                d=day.isoformat(),
                completed=completed,
            )

    out = patterns.adherence_patterns(conn, window_weeks=8, end_date=end)
    assert out["signals"], "expected at least one signal"
    s = out["signals"][0]
    # Shape is a plain dict — preflight scripts dump it as JSON.
    assert isinstance(s, dict)
    for key in (
        "dimension",
        "value",
        "completion_rate",
        "baseline",
        "samples",
        "z_score",
        "severity",
        "message",
    ):
        assert key in s
