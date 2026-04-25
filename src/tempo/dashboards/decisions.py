"""Decision-trace dashboard: scope-filtered timeline of plan adjustments."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .. import plans, queries
from ..db import connect, init_schema
from .common import page

_DEFAULT_SINCE_DAYS = 28


def render_decisions(
    scope: str | None = None,
    since: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> str:
    """Render a decision-trace dashboard. Returns the full HTML doc."""
    today = today or date.today()
    since_iso = since or (today - timedelta(days=_DEFAULT_SINCE_DAYS)).isoformat()

    owns_conn = conn is None
    if conn is None:
        conn = connect()
        init_schema(conn)
    try:
        rows = _fetch_decisions(conn, scope=scope, since=since_iso)
        cards = [_card(conn, row) for row in rows]
    finally:
        if owns_conn:
            conn.close()

    body_parts: list[str] = [_header(scope, since_iso, len(rows))]
    if not cards:
        body_parts.append(
            "<p class='notice'>No decisions in this window. Either none were "
            "logged or all are filtered out.</p>"
        )
    else:
        body_parts.append("\n".join(cards))

    return page(
        "Decisions",
        "\n".join(body_parts),
        footer_note=(
            f"coach dashboard decisions — scope={escape(scope) if scope else 'all'} "
            f"since={escape(since_iso)}"
        ),
    )


def _header(scope: str | None, since_iso: str, count: int) -> str:
    chips = (
        f"<span class='chip'>scope: {escape(scope)}</span>"
        if scope
        else "<span class='chip'>scope: all</span>"
    )
    chips += f" <span class='chip'>since: {escape(since_iso)}</span>"
    chips += f" <span class='chip'>{count} decision{'s' if count != 1 else ''}</span>"
    return (
        "<header class='page'>"
        "<h1>Decisions</h1>"
        f"<div class='subtitle'>{chips}</div>"
        "</header>"
    )


def _fetch_decisions(
    conn: sqlite3.Connection,
    *,
    scope: str | None,
    since: str,
) -> list[sqlite3.Row]:
    clauses: list[str] = ["timestamp >= ?"]
    params: list[object] = [since]
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    sql = (
        "SELECT id, timestamp, scope, kind, rationale, changed_files "
        f"FROM decisions WHERE {' AND '.join(clauses)} "
        "ORDER BY timestamp DESC, id DESC"
    )
    return conn.execute(sql, params).fetchall()


def _card(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """Render one decision card with collapsible evidence."""
    scope = str(row["scope"] or "")
    kind = str(row["kind"] or "")
    ts = str(row["timestamp"] or "")
    rationale = str(row["rationale"] or "")
    changed_raw = row["changed_files"]

    changed_files = _parse_changed_files(changed_raw)
    week_id = _week_id_from_scope(scope)
    wellness = _wellness_at(conn, ts) if ts else None
    adherence = _adherence_for(conn, week_id) if week_id else None

    evidence_parts: list[str] = []
    if wellness is not None:
        evidence_parts.append(_wellness_evidence(wellness))
    if adherence is not None:
        evidence_parts.append(_adherence_evidence(adherence))
    if changed_files:
        evidence_parts.append(_files_evidence(changed_files))

    evidence_html = (
        "<details><summary>Evidence</summary>"
        + "<ul>" + "".join(f"<li>{p}</li>" for p in evidence_parts) + "</ul>"
        + "</details>"
        if evidence_parts
        else ""
    )

    fingerprint = _row_fingerprint(row)
    rationale_html = _rationale_html(rationale)

    return (
        "<article class='card'>"
        "<header>"
        f"<span class='chip'>{escape(scope or '—')}</span>"
        f"<span class='chip'>{escape(kind or '—')}</span>"
        f"<span class='chip'>{escape(_fmt_timestamp(ts))}</span>"
        "</header>"
        f"<div>{rationale_html}</div>"
        f"{evidence_html}"
        f"<footer><span class='chip'>id #{row['id']} · {fingerprint}</span></footer>"
        "</article>"
    )


def _parse_changed_files(raw: object) -> list[str]:
    """``changed_files`` is JSON in the column. Tolerate raw strings or NULL."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return [text]
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return [str(parsed)]
    return [str(raw)]


def _week_id_from_scope(scope: str) -> str | None:
    """Extract YYYY-Www from scopes like ``week:2026-W17``."""
    if not scope.startswith("week:"):
        return None
    candidate = scope[len("week:"):]
    try:
        plans.parse_week_id(candidate)
    except (ValueError, AttributeError):
        return None
    return candidate


def _wellness_at(
    conn: sqlite3.Connection, ts: str
) -> queries.ReadinessRow | None:
    """Best-effort readiness snapshot as of the decision timestamp."""
    try:
        as_of = ts[:10]
        date.fromisoformat(as_of)
    except ValueError:
        return None
    try:
        return queries.get_readiness(conn, as_of=as_of, window_days=14)
    except sqlite3.Error:
        return None


def _adherence_for(
    conn: sqlite3.Connection, week_id: str
) -> queries.AdherenceReportRow | None:
    try:
        report = queries.get_adherence(conn, week_id=week_id)
    except sqlite3.Error:
        return None
    if report.planned_count == 0:
        return None
    return report


def _wellness_evidence(snap: queries.ReadinessRow) -> str:
    bits: list[str] = []
    if snap.sleep_h_latest is not None:
        bits.append(f"sleep {snap.sleep_h_latest:.1f}h")
    if snap.hrv_latest is not None:
        bits.append(f"HRV {snap.hrv_latest:.0f}")
    if snap.rhr_latest is not None:
        bits.append(f"RHR {snap.rhr_latest}")
    if snap.readiness_latest is not None:
        bits.append(f"readiness {snap.readiness_latest}")
    if snap.hrv_trend_delta is not None:
        bits.append(f"HRV 7d Δ {snap.hrv_trend_delta:+.1f}")
    if not bits:
        return "<strong>Wellness:</strong> no samples in window."
    return "<strong>Wellness:</strong> " + escape(" · ".join(bits))


def _adherence_evidence(report: queries.AdherenceReportRow) -> str:
    return (
        "<strong>Adherence:</strong> "
        f"{report.completed_count}/{report.planned_count} completed "
        f"({report.completion_pct:.0f}%) · "
        f"planned TSS {report.total_planned_tss:.0f} · "
        f"actual TSS {report.total_actual_tss:.0f}"
    )


def _files_evidence(files: list[str]) -> str:
    items = "".join(f"<li>{escape(f)}</li>" for f in files)
    return f"<strong>Changed files:</strong><ul>{items}</ul>"


def _rationale_html(text: str) -> str:
    """Escape and preserve line breaks. No markdown rendering — keep deps minimal."""
    safe = escape(text)
    return f"<pre class='changelog'>{safe}</pre>"


def _fmt_timestamp(ts: str) -> str:
    """Best-effort: keep ISO timestamps readable; pass through anything else."""
    if not ts:
        return "—"
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def _row_fingerprint(row: sqlite3.Row) -> str:
    """Stable hash of the decision row for citation in future reviews."""
    payload = json.dumps(
        {k: row[k] for k in row.keys()}, default=str, sort_keys=True
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


__all__ = ["render_decisions"]
