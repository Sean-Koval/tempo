"""Read-only queries over ``data/coach.db``.

Pure functions: take a ``sqlite3.Connection`` + keyword filters, return plain
dataclasses. Three surfaces consume these:

- ``tempo.cli`` / ``tempo.display`` — formats for the ``coach status`` output.
- ``coach_db_mcp.sql`` — wraps each call in Pydantic models for the MCP surface.
- ``.claude/skills/*/preflight.py`` — imports directly to assemble JSON briefs.

Any drift between those surfaces means the wrong one is reimplementing a query.
Keep the single source here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class ActivityRow:
    id: str
    start_date: str
    sport: str
    duration_s: int | None = None
    distance_m: float | None = None
    tss: float | None = None
    np: float | None = None
    intensity_factor: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    decoupling: float | None = None
    elevation_gain_m: float | None = None


@dataclass
class LoadPointRow:
    date: str
    ctl: float | None = None
    atl: float | None = None
    tsb: float | None = None
    ramp_7d: float | None = None
    ctl_bike: float | None = None
    ctl_run: float | None = None
    ctl_swim: float | None = None


@dataclass
class ReadinessRow:
    as_of: str
    sleep_h_latest: float | None = None
    sleep_h_7d_mean: float | None = None
    hrv_latest: float | None = None
    hrv_7d_mean: float | None = None
    hrv_trend_delta: float | None = None
    rhr_latest: int | None = None
    rhr_7d_mean: float | None = None
    readiness_latest: int | None = None
    notes_latest: str | None = None
    samples: int = 0


@dataclass
class AdherenceItemRow:
    planned_session_id: str
    date: str | None = None
    sport: str | None = None
    library_ref: str | None = None
    activity_id: str | None = None
    completed: bool | None = None
    tss_delta: float | None = None
    duration_delta_s: int | None = None
    reason: str | None = None


@dataclass
class AdherenceReportRow:
    week_id: str
    planned_count: int
    completed_count: int
    skipped_count: int
    moved_count: int
    completion_pct: float
    total_planned_tss: float
    total_actual_tss: float
    items: list[AdherenceItemRow] = field(default_factory=list)


@dataclass
class DeltaRow:
    planned_session_id: str
    date: str | None = None
    sport: str | None = None
    library_ref: str | None = None
    purpose: str | None = None
    planned_tss: float | None = None
    actual_tss: float | None = None
    tss_delta: float | None = None
    planned_duration_s: int | None = None
    actual_duration_s: int | None = None
    duration_delta_s: int | None = None
    reason: str | None = None
    activity_id: str | None = None


def query_activities(
    conn: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    sport: str | None = None,
    min_duration_s: int | None = None,
    max_decoupling_pct: float | None = None,
    min_tss: float | None = None,
    max_tss: float | None = None,
    limit: int = 50,
) -> list[ActivityRow]:
    """Filtered read of the activities table. All filters are optional."""
    clauses: list[str] = []
    params: list[object] = []
    if start is not None:
        clauses.append("start_date >= ?")
        params.append(start)
    if end is not None:
        # Bare dates act as inclusive end-of-day; 'T' already means a full timestamp.
        end_bound = end if "T" in end else f"{end}T23:59:59"
        clauses.append("start_date <= ?")
        params.append(end_bound)
    if sport is not None:
        clauses.append("sport = ?")
        params.append(sport)
    if min_duration_s is not None:
        clauses.append("duration_s >= ?")
        params.append(min_duration_s)
    if max_decoupling_pct is not None:
        clauses.append("decoupling IS NOT NULL AND decoupling <= ?")
        params.append(max_decoupling_pct)
    if min_tss is not None:
        clauses.append("tss >= ?")
        params.append(min_tss)
    if max_tss is not None:
        clauses.append("tss <= ?")
        params.append(max_tss)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT id, start_date, sport, duration_s, distance_m, tss, np, "
        "intensity_factor, avg_hr, max_hr, decoupling, elevation_gain_m "
        f"FROM activities {where} ORDER BY start_date DESC LIMIT ?"
    )
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    return [ActivityRow(**dict(r)) for r in rows]


def get_load_curve(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    sport: str | None = None,
) -> list[LoadPointRow]:
    """Return ``load_daily`` rows in ``[start_date, end_date]``, inclusive.

    ``sport`` is informational for the caller — all per-sport CTL columns are
    returned for every point; the agent/display layer picks which one to read.
    """
    rows = conn.execute(
        "SELECT date, ctl, atl, tsb, ramp_7d, ctl_bike, ctl_run, ctl_swim "
        "FROM load_daily WHERE date BETWEEN ? AND ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    _ = sport  # retained in signature for future per-sport filtering.
    return [LoadPointRow(**dict(r)) for r in rows]


def get_readiness(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    window_days: int = 14,
) -> ReadinessRow:
    """Aggregate ``wellness_daily`` over the window ending at ``as_of``.

    Returns latest values plus 7d means and the HRV trend delta (7d mean minus
    prior-7d mean — positive = improving).
    """
    as_of_d = date.fromisoformat(as_of)
    window_start = (as_of_d - timedelta(days=window_days - 1)).isoformat()
    rows = conn.execute(
        "SELECT date, sleep_h, hrv, rhr, readiness, notes "
        "FROM wellness_daily WHERE date BETWEEN ? AND ? ORDER BY date DESC",
        (window_start, as_of),
    ).fetchall()

    snap = ReadinessRow(as_of=as_of, samples=len(rows))
    if not rows:
        return snap

    latest = rows[0]
    snap.sleep_h_latest = latest["sleep_h"]
    snap.hrv_latest = latest["hrv"]
    snap.rhr_latest = latest["rhr"]
    snap.readiness_latest = latest["readiness"]
    snap.notes_latest = latest["notes"]

    def _mean(values: list[float]) -> float | None:
        vs = [v for v in values if v is not None]
        return sum(vs) / len(vs) if vs else None

    recent = [r for r in rows if _within(r["date"], as_of_d, 7)]
    snap.sleep_h_7d_mean = _mean([r["sleep_h"] for r in recent])
    snap.hrv_7d_mean = _mean([r["hrv"] for r in recent])
    rhr_mean = _mean([float(r["rhr"]) for r in recent if r["rhr"] is not None])
    snap.rhr_7d_mean = rhr_mean

    prior_start = (as_of_d - timedelta(days=14)).isoformat()
    prior_end = (as_of_d - timedelta(days=8)).isoformat()
    prior_rows = conn.execute(
        "SELECT hrv FROM wellness_daily WHERE date BETWEEN ? AND ?",
        (prior_start, prior_end),
    ).fetchall()
    prior_hrv_mean = _mean([r["hrv"] for r in prior_rows])
    if snap.hrv_7d_mean is not None and prior_hrv_mean is not None:
        snap.hrv_trend_delta = snap.hrv_7d_mean - prior_hrv_mean
    return snap


def _within(d_str: str, as_of: date, days: int) -> bool:
    d = date.fromisoformat(d_str)
    return (as_of - d).days < days


def get_adherence(conn: sqlite3.Connection, *, week_id: str) -> AdherenceReportRow:
    rows = conn.execute(
        """
        SELECT sp.id AS planned_session_id,
               sp.date, sp.sport, sp.library_ref, sp.target_tss,
               ad.activity_id, ad.completed, ad.tss_delta,
               ad.duration_delta_s, ad.reason,
               a.tss AS actual_tss
        FROM sessions_planned sp
        LEFT JOIN adherence ad ON ad.planned_session_id = sp.id
        LEFT JOIN activities a ON a.id = ad.activity_id
        WHERE sp.week_id = ?
        ORDER BY sp.date
        """,
        (week_id,),
    ).fetchall()

    items: list[AdherenceItemRow] = []
    planned = len(rows)
    completed = 0
    skipped = 0
    moved = 0
    total_planned_tss = 0.0
    total_actual_tss = 0.0

    for r in rows:
        target = r["target_tss"] or 0.0
        actual = r["actual_tss"] or 0.0
        total_planned_tss += target
        total_actual_tss += actual
        is_completed = bool(r["completed"]) if r["completed"] is not None else False
        if is_completed:
            completed += 1
        reason = r["reason"] or ""
        if reason.startswith("skipped"):
            skipped += 1
        elif reason.startswith("moved"):
            moved += 1
        items.append(
            AdherenceItemRow(
                planned_session_id=r["planned_session_id"],
                date=r["date"],
                sport=r["sport"],
                library_ref=r["library_ref"],
                activity_id=r["activity_id"],
                completed=is_completed if r["completed"] is not None else None,
                tss_delta=r["tss_delta"],
                duration_delta_s=r["duration_delta_s"],
                reason=r["reason"],
            )
        )

    completion_pct = round(100.0 * completed / planned, 1) if planned else 0.0
    return AdherenceReportRow(
        week_id=week_id,
        planned_count=planned,
        completed_count=completed,
        skipped_count=skipped,
        moved_count=moved,
        completion_pct=completion_pct,
        total_planned_tss=round(total_planned_tss, 1),
        total_actual_tss=round(total_actual_tss, 1),
        items=items,
    )


def compare_plan_to_actual(
    conn: sqlite3.Connection, *, week_id: str
) -> list[DeltaRow]:
    rows = conn.execute(
        """
        SELECT sp.id AS planned_session_id,
               sp.date, sp.sport, sp.library_ref, sp.purpose,
               sp.target_tss, sp.target_duration_s,
               ad.activity_id, ad.reason,
               a.tss AS actual_tss, a.duration_s AS actual_duration_s
        FROM sessions_planned sp
        LEFT JOIN adherence ad ON ad.planned_session_id = sp.id
        LEFT JOIN activities a ON a.id = ad.activity_id
        WHERE sp.week_id = ?
        ORDER BY sp.date
        """,
        (week_id,),
    ).fetchall()

    deltas: list[DeltaRow] = []
    for r in rows:
        planned_tss = r["target_tss"]
        actual_tss = r["actual_tss"]
        tss_delta = (
            None
            if planned_tss is None or actual_tss is None
            else actual_tss - planned_tss
        )
        planned_dur = r["target_duration_s"]
        actual_dur = r["actual_duration_s"]
        dur_delta = (
            None
            if planned_dur is None or actual_dur is None
            else actual_dur - planned_dur
        )
        deltas.append(
            DeltaRow(
                planned_session_id=r["planned_session_id"],
                date=r["date"],
                sport=r["sport"],
                library_ref=r["library_ref"],
                purpose=r["purpose"],
                planned_tss=planned_tss,
                actual_tss=actual_tss,
                tss_delta=tss_delta,
                planned_duration_s=planned_dur,
                actual_duration_s=actual_dur,
                duration_delta_s=dur_delta,
                reason=r["reason"],
                activity_id=r["activity_id"],
            )
        )
    return deltas


__all__ = [
    "ActivityRow",
    "AdherenceItemRow",
    "AdherenceReportRow",
    "DeltaRow",
    "LoadPointRow",
    "ReadinessRow",
    "compare_plan_to_actual",
    "get_adherence",
    "get_load_curve",
    "get_readiness",
    "query_activities",
]
