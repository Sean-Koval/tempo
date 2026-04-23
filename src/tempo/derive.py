"""Deterministic derivations: CTL/ATL/TSB + per-sport CTL + ramp rate.

Reads ``activities.tss`` + ``activities.start_date`` + ``activities.sport``,
writes a row per day into ``load_daily`` covering the full history from
``first_activity_date`` to ``today``. Idempotent.

Formulas (Coggan / TrainingPeaks standard):

    CTL_t = CTL_{t-1} + (tss_t - CTL_{t-1}) / 42   # 42-day EWA
    ATL_t = ATL_{t-1} + (tss_t - ATL_{t-1}) / 7    # 7-day EWA
    TSB_t = CTL_t - ATL_t

Per-sport CTL (bike, run, swim) uses the same formula over that sport's
TSS series only. Days with zero training contribute decay only.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .db import connect, init_schema
from .events import log_event

CTL_WINDOW = 42
ATL_WINDOW = 7
_PER_SPORT = ("bike", "run", "swim")


@dataclass(slots=True)
class DeriveStats:
    days_written: int = 0
    activities_scored: int = 0
    duration_ms: int = 0


def _daterange(start: date, end: date) -> list[date]:
    span = (end - start).days
    return [start + timedelta(days=i) for i in range(span + 1)]


def _daily_tss_by_sport(
    conn: sqlite3.Connection,
) -> tuple[dict[date, float], dict[str, dict[date, float]]]:
    """Sum tss per day (total and per canonical sport)."""
    rows = conn.execute(
        "SELECT date(start_date) AS d, sport, tss "
        "FROM activities "
        "WHERE tss IS NOT NULL AND start_date IS NOT NULL"
    ).fetchall()

    total: dict[date, float] = {}
    by_sport: dict[str, dict[date, float]] = {s: {} for s in _PER_SPORT}

    for r in rows:
        d = r["d"]
        if isinstance(d, str):
            d = date.fromisoformat(d)
        tss = float(r["tss"])
        total[d] = total.get(d, 0.0) + tss
        sport = r["sport"]
        if sport in by_sport:
            by_sport[sport][d] = by_sport[sport].get(d, 0.0) + tss

    return total, by_sport


def _ewa_series(daily: dict[date, float], dates: list[date], window: int) -> dict[date, float]:
    """Walk ``dates`` in order applying the EWA update; zero days decay only."""
    out: dict[date, float] = {}
    prev = 0.0
    for d in dates:
        tss = daily.get(d, 0.0)
        prev = prev + (tss - prev) / window
        out[d] = prev
    return out


def derive(now: datetime | None = None) -> DeriveStats:
    """Rebuild ``load_daily`` from ``activities``. Idempotent."""
    start = time.monotonic()
    ts_now = now or datetime.now(UTC)
    today = ts_now.date()

    stats = DeriveStats()
    conn = connect()
    try:
        init_schema(conn)

        earliest_row = conn.execute(
            "SELECT MIN(date(start_date)) AS d FROM activities"
        ).fetchone()
        earliest = earliest_row["d"] if earliest_row else None
        if not earliest:
            # Nothing to derive. Clear any stale load_daily and return.
            with conn:
                conn.execute("DELETE FROM load_daily")
            stats.duration_ms = int((time.monotonic() - start) * 1000)
            return stats

        first_date = (
            date.fromisoformat(earliest) if isinstance(earliest, str) else earliest
        )
        # Reach back CTL_WINDOW days to burn-in the EWA before the first activity.
        first_date = first_date - timedelta(days=CTL_WINDOW)
        dates = _daterange(first_date, today)

        total_daily, by_sport_daily = _daily_tss_by_sport(conn)

        ctl_series = _ewa_series(total_daily, dates, CTL_WINDOW)
        atl_series = _ewa_series(total_daily, dates, ATL_WINDOW)
        per_sport_ctl = {
            sport: _ewa_series(by_sport_daily[sport], dates, CTL_WINDOW)
            for sport in _PER_SPORT
        }

        count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM activities WHERE tss IS NOT NULL"
        ).fetchone()
        stats.activities_scored = int(count_row["c"]) if count_row else 0

        with conn:
            conn.execute("DELETE FROM load_daily")
            for d in dates:
                ctl = ctl_series[d]
                atl = atl_series[d]
                prior_ctl = ctl_series.get(d - timedelta(days=7), 0.0)
                row = (
                    d.isoformat(),
                    ctl,
                    atl,
                    ctl - atl,
                    per_sport_ctl["bike"][d],
                    per_sport_ctl["run"][d],
                    per_sport_ctl["swim"][d],
                    ctl - prior_ctl,
                )
                conn.execute(
                    """
                    INSERT INTO load_daily
                        (date, ctl, atl, tsb, ctl_bike, ctl_run, ctl_swim, ramp_7d)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            stats.days_written = len(dates)
    finally:
        conn.close()

    stats.duration_ms = int((time.monotonic() - start) * 1000)
    log_event(
        "derive",
        {
            "days_written": stats.days_written,
            "activities_scored": stats.activities_scored,
            "duration_ms": stats.duration_ms,
        },
        now=ts_now,
    )
    return stats
