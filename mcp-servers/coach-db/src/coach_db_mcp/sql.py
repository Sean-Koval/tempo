"""Pydantic adapter over ``tempo.queries``.

This module is the MCP-serialization boundary: it calls the pure dataclass
functions in ``tempo.queries`` and wraps the results in Pydantic models from
``coach_db_mcp.models`` so the FastMCP tool registration gets the JSON schema
it needs.

All actual query logic lives in ``tempo.queries``. Do not add SQL here — if a
new read is needed, add it in ``tempo.queries`` first and wrap it here second.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from tempo import queries

from .models import (
    ActivityOut,
    AdherenceItem,
    AdherenceReport,
    Delta,
    LoadPoint,
    ReadinessSnapshot,
)


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
) -> list[ActivityOut]:
    rows = queries.query_activities(
        conn,
        start=start,
        end=end,
        sport=sport,
        min_duration_s=min_duration_s,
        max_decoupling_pct=max_decoupling_pct,
        min_tss=min_tss,
        max_tss=max_tss,
        limit=limit,
    )
    return [ActivityOut(**asdict(r)) for r in rows]


def get_load_curve(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    sport: str | None = None,
) -> list[LoadPoint]:
    rows = queries.get_load_curve(
        conn, start_date=start_date, end_date=end_date, sport=sport
    )
    return [LoadPoint(**asdict(r)) for r in rows]


def get_readiness(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    window_days: int = 14,
) -> ReadinessSnapshot:
    row = queries.get_readiness(conn, as_of=as_of, window_days=window_days)
    return ReadinessSnapshot(**asdict(row))


def get_adherence(conn: sqlite3.Connection, *, week_id: str) -> AdherenceReport:
    report = queries.get_adherence(conn, week_id=week_id)
    return AdherenceReport(
        week_id=report.week_id,
        planned_count=report.planned_count,
        completed_count=report.completed_count,
        skipped_count=report.skipped_count,
        moved_count=report.moved_count,
        completion_pct=report.completion_pct,
        total_planned_tss=report.total_planned_tss,
        total_actual_tss=report.total_actual_tss,
        items=[AdherenceItem(**asdict(i)) for i in report.items],
    )


def compare_plan_to_actual(
    conn: sqlite3.Connection, *, week_id: str
) -> list[Delta]:
    deltas = queries.compare_plan_to_actual(conn, week_id=week_id)
    return [Delta(**asdict(d)) for d in deltas]


__all__ = [
    "compare_plan_to_actual",
    "get_adherence",
    "get_load_curve",
    "get_readiness",
    "query_activities",
]
