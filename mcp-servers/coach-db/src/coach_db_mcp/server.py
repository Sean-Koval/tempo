"""FastMCP server entry point.

Tool modules register themselves by importing this module's ``mcp`` instance
and decorating functions with ``@mcp.tool``. Keeping registration opt-in per
module means tests can import ``mcp`` without pulling every dependency.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from fastmcp import FastMCP
from tempo.db import connect, init_schema

from . import knowledge, memory, sql
from .models import (
    ActivityOut,
    AdherenceReport,
    DecisionLogged,
    Delta,
    LoadPoint,
    MemoryHit,
    ReadinessSnapshot,
    Snippet,
)

mcp: FastMCP = FastMCP(
    name="coach-db",
    instructions=(
        "Typed tools over Tempo's SQLite (coach.db) and LanceDB substrates. "
        "Use for historical queries, adherence, load curves, knowledge search, "
        "memory recall, and logging coaching decisions."
    ),
)


@contextmanager
def _db():
    """Open coach.db with schema applied. Connection is per-call; cheap in SQLite."""
    conn = connect()
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()


@mcp.tool
def ping() -> dict[str, Any]:
    """Sentinel tool — confirms the server is wired and reachable."""
    return {"status": "ok", "server": "coach-db", "version": "0.1.0"}


@mcp.tool
def query_activities(
    start: str | None = None,
    end: str | None = None,
    sport: str | None = None,
    min_duration_s: int | None = None,
    max_decoupling_pct: float | None = None,
    min_tss: float | None = None,
    max_tss: float | None = None,
    limit: int = 50,
) -> list[ActivityOut]:
    """Filtered read of past activities. All filters optional; newest first."""
    with _db() as conn:
        return sql.query_activities(
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


@mcp.tool
def get_load_curve(
    start_date: str,
    end_date: str,
    sport: str | None = None,
) -> list[LoadPoint]:
    """CTL/ATL/TSB over a date range, plus per-sport CTL columns."""
    with _db() as conn:
        return sql.get_load_curve(
            conn, start_date=start_date, end_date=end_date, sport=sport
        )


@mcp.tool
def get_readiness(as_of: str, window_days: int = 14) -> ReadinessSnapshot:
    """Latest wellness + rolling-7d means; HRV trend vs prior 7d."""
    with _db() as conn:
        return sql.get_readiness(conn, as_of=as_of, window_days=window_days)


@mcp.tool
def get_adherence(week_id: str) -> AdherenceReport:
    """Planned vs actual sessions for a week (e.g. '2026-W17')."""
    with _db() as conn:
        return sql.get_adherence(conn, week_id=week_id)


@mcp.tool
def compare_plan_to_actual(week_id: str) -> list[Delta]:
    """Per-session deltas for a week. Raw numbers — the agent applies decision-rules."""
    with _db() as conn:
        return sql.compare_plan_to_actual(conn, week_id=week_id)


@mcp.tool
def search_memory(
    query: str,
    k: int = 5,
    since: str | None = None,
    scope: str | None = None,
    kind: str | None = None,
) -> list[MemoryHit]:
    """Semantic search over memory.lance (decisions + journals + changelogs).

    ``since`` is an ISO date (YYYY-MM-DD); hits with an earlier timestamp are
    dropped. ``scope`` is a prefix match (e.g. 'week:2026-W17', 'plan:'). ``kind``
    exact-matches decisions.kind (so it only returns decision rows).
    """
    return memory.search_memory_hits(
        query, k=k, since=since, scope=scope, kind=kind
    )


@mcp.tool
def log_decision(
    scope: str,
    kind: str,
    rationale: str,
    changed_files: list[str] | None = None,
) -> DecisionLogged:
    """Persist a coaching decision.

    Writes to the decisions table and synchronously embeds the rationale into
    memory.lance so a subsequent search_memory finds it in the same session.
    ``scope`` examples: 'week:2026-W17', 'plan:ironman-lp', 'session:abc123'.
    ``kind``: 'plan' | 'adjust' | 'review' | 'observation'.
    """
    with _db() as conn:
        return memory.log_decision(
            conn,
            scope=scope,
            kind=kind,
            rationale=rationale,
            changed_files=changed_files,
        )


@mcp.tool
def search_knowledge(
    query: str,
    k: int = 5,
    topic: str | None = None,
    credibility_min: str | None = None,
) -> list[Snippet]:
    """Semantic search over knowledge.lance (methodology + nutrition + research).

    ``credibility_min`` drops hits weaker than the given level. Levels ranked
    strongest→weakest: peer_reviewed, expert_practitioner,
    evidence_based_journalism, experiential, unvetted.
    """
    return knowledge.search_knowledge(
        query, k=k, topic=topic, credibility_min=credibility_min
    )


def main() -> None:
    """Entry point declared in pyproject scripts — stdio transport for Claude Code."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
