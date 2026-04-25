"""Single-week dashboard: planned vs actual sessions, wellness, load, changelog."""

from __future__ import annotations

import re
import sqlite3
from html import escape
from pathlib import Path
from typing import Any

from .. import plans, queries
from ..db import connect, init_schema
from .common import fmt_num, fmt_signed, page


def render_week(
    week_id: str | None = None,
    *,
    plan_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    root: Path | None = None,
) -> str:
    """Render a single-week HTML dashboard. Returns the full HTML doc."""
    resolved_week_id = week_id or _default_week_id()
    plan_block = _resolve_plan(plan_id, root=root)
    resolved_plan_id = plan_block["plan_id"]
    plan_doc = plan_block["plan_doc"]
    phase = plan_block["phase"](resolved_week_id) if plan_doc else None
    week_of_phase = (
        plans.week_index_in_phase(phase, resolved_week_id) if phase else None
    )

    owns_conn = conn is None
    if conn is None:
        conn = connect()
        init_schema(conn)
    try:
        deltas = queries.compare_plan_to_actual(conn, week_id=resolved_week_id)
        adherence = queries.get_adherence(conn, week_id=resolved_week_id)
        w_start = plans.week_start(resolved_week_id).isoformat()
        w_end = plans.week_end(resolved_week_id).isoformat()
        wellness = queries.get_wellness_range(
            conn, start_date=w_start, end_date=w_end
        )
        load = queries.get_load_curve(conn, start_date=w_start, end_date=w_end)
    finally:
        if owns_conn:
            conn.close()

    body_parts: list[str] = [
        _header(resolved_week_id, resolved_plan_id, phase, week_of_phase, adherence),
    ]

    if not deltas and not wellness and not load:
        body_parts.append(
            "<p class='notice'>No planned sessions, wellness, or load data for "
            f"{escape(resolved_week_id)} yet.</p>"
        )
    else:
        body_parts.append(_sessions_table(deltas))
        body_parts.append(_wellness_section(wellness))
        body_parts.append(_load_section(load))

    if resolved_plan_id:
        cl_html = _changelog_section(resolved_plan_id, resolved_week_id, root=root)
        if cl_html:
            body_parts.append(cl_html)

    return page(
        f"Week {resolved_week_id}",
        "\n".join(body_parts),
        footer_note=f"coach dashboard week — plan {resolved_plan_id or '(none)'}",
    )


def _default_week_id() -> str:
    """Most recent completed week (today - 7d)."""
    from datetime import date, timedelta

    return plans.week_id_for(date.today() - timedelta(days=7))


def _resolve_plan(plan_id: str | None, *, root: Path | None) -> dict[str, Any]:
    """Look up the plan.yaml — returns ``{plan_id, plan_doc, phase(week_id)}``.

    Best-effort: if no plan exists or auto-detection finds multiple, returns
    ``plan_id=None`` so the renderer still produces a useful page.
    """
    plan_doc: dict[str, Any] | None = None
    resolved_id: str | None = None
    if plan_id is not None:
        plan_doc = plans.read_plan_yaml(plan_id, root=root)
        if plan_doc is not None:
            resolved_id = plan_id
    else:
        try:
            found = plans.find_single_plan(root=root)
        except plans.MultiplePlansError:
            found = None
        if found is not None:
            resolved_id, plan_doc = found

    return {
        "plan_id": resolved_id,
        "plan_doc": plan_doc,
        "phase": (lambda wid: plans.phase_for_week(plan_doc, wid)) if plan_doc else (lambda wid: None),
    }


def _header(
    week_id: str,
    plan_id: str | None,
    phase: dict[str, Any] | None,
    week_of_phase: int | None,
    adherence: queries.AdherenceReportRow,
) -> str:
    phase_label = (
        f"{escape(str(phase.get('id') or '?'))} (week {week_of_phase}/{phase.get('weeks', '?')})"
        if phase
        else "no phase mapped"
    )
    target_mid = _phase_target_mid(phase) if phase else None
    return (
        "<header class='page'>"
        f"<h1>Week {escape(week_id)}</h1>"
        f"<div class='subtitle'>plan: {escape(plan_id or '(none)')} · phase: {phase_label}</div>"
        "<div class='card'><dl class='kv'>"
        f"<dt>Target TSS (mid)</dt><dd>{fmt_num(target_mid, precision=0)}</dd>"
        f"<dt>Actual TSS</dt><dd>{fmt_num(adherence.total_actual_tss, precision=0)}</dd>"
        f"<dt>Planned TSS</dt><dd>{fmt_num(adherence.total_planned_tss, precision=0)}</dd>"
        f"<dt>Sessions</dt><dd>{adherence.completed_count}/{adherence.planned_count} completed "
        f"· {adherence.skipped_count} skipped · {adherence.moved_count} moved "
        f"({fmt_num(adherence.completion_pct, precision=0)}%)</dd>"
        "</dl></div>"
        "</header>"
    )


def _phase_target_mid(phase: dict[str, Any]) -> float | None:
    raw = phase.get("weekly_tss_target")
    if raw is None:
        return None
    if isinstance(raw, list) and len(raw) == 2:
        try:
            return (float(raw[0]) + float(raw[1])) / 2.0
        except (TypeError, ValueError):
            return None
    if isinstance(raw, dict):
        lo = raw.get("low")
        hi = raw.get("high")
        if lo is not None and hi is not None:
            try:
                return (float(lo) + float(hi)) / 2.0
            except (TypeError, ValueError):
                return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _sessions_table(deltas: list[queries.DeltaRow]) -> str:
    if not deltas:
        return "<h2>Sessions</h2><p class='notice'>No planned sessions for this week.</p>"
    rows: list[str] = []
    for d in deltas:
        cls, status_text = _row_status(d)
        rows.append(
            "<tr class='" + cls + "'>"
            f"<td>{escape(d.date or '—')}</td>"
            f"<td>{escape(d.sport or '—')}</td>"
            f"<td>{escape(d.library_ref or '—')}</td>"
            f"<td>{fmt_num(d.planned_tss, precision=0)}</td>"
            f"<td>{fmt_num(d.actual_tss, precision=0)}</td>"
            f"<td>{fmt_signed(d.tss_delta, precision=0)}</td>"
            f"<td class='status'>{escape(status_text)}</td>"
            f"<td>{escape(d.reason or '')}</td>"
            "</tr>"
        )
    return (
        "<h2>Sessions</h2>"
        "<table><thead><tr>"
        "<th>Date</th><th>Sport</th><th>Library</th>"
        "<th>Planned TSS</th><th>Actual TSS</th><th>Δ</th>"
        "<th>Status</th><th>Reason</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _row_status(d: queries.DeltaRow) -> tuple[str, str]:
    """Return (CSS class, status text) for a session row."""
    reason = (d.reason or "").lower()
    if reason.startswith("skipped"):
        return "skipped", "skipped"
    if reason.startswith("moved"):
        return "moved", "moved"
    if d.activity_id and d.actual_tss is not None:
        return "completed", "completed"
    return "pending", "pending"


def _wellness_section(rows: list[queries.WellnessRow]) -> str:
    if not rows:
        return "<h2>Wellness</h2><p class='notice'>No wellness data for this week.</p>"
    fields = [
        ("sleep_h", "Sleep (h)", 1),
        ("hrv", "HRV", 1),
        ("rhr", "RHR", 0),
        ("readiness", "Readiness", 0),
    ]
    parts: list[str] = ["<h2>Wellness</h2><div class='card'>"]
    for attr, label, prec in fields:
        values = [getattr(r, attr) for r in rows]
        parts.append(_sparkline_row(label, values, prec))
    parts.append("</div>")
    return "".join(parts)


def _sparkline_row(label: str, values: list[float | int | None], precision: int) -> str:
    sparkline = _sparkline_svg(values)
    latest = next((v for v in reversed(values) if v is not None), None)
    return (
        "<div class='spark'>"
        f"<span class='label'>{escape(label)}</span>"
        f"{sparkline}"
        f"<span class='value'>{fmt_num(latest, precision=precision)}</span>"
        "</div>"
    )


def _sparkline_svg(values: list[float | int | None], *, width: int = 140, height: int = 24) -> str:
    """Tiny inline SVG sparkline. Missing points are skipped, not interpolated."""
    pts = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return f"<svg width='{width}' height='{height}'></svg>"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_span = max(x_max - x_min, 1)
    y_span = max(y_max - y_min, 1e-9)
    coords: list[str] = []
    for x, y in pts:
        nx = (x - x_min) / x_span * (width - 4) + 2
        # invert Y so higher values plot upward
        ny = height - 2 - (y - y_min) / y_span * (height - 4)
        coords.append(f"{nx:.1f},{ny:.1f}")
    points_attr = " ".join(coords)
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        "xmlns='http://www.w3.org/2000/svg'>"
        f"<polyline fill='none' stroke='currentColor' stroke-width='1.5' points='{points_attr}' />"
        "</svg>"
    )


def _load_section(rows: list[queries.LoadPointRow]) -> str:
    if not rows:
        return "<h2>Load</h2><p class='notice'>No load data for this week.</p>"
    ctls = [r.ctl for r in rows if r.ctl is not None]
    atls = [r.atl for r in rows if r.atl is not None]
    tsbs = [r.tsb for r in rows if r.tsb is not None]
    start_ctl = ctls[0] if ctls else None
    end_ctl = ctls[-1] if ctls else None
    delta_ctl = (end_ctl - start_ctl) if (start_ctl is not None and end_ctl is not None) else None
    return (
        "<h2>Load</h2>"
        "<div class='card'><dl class='kv'>"
        f"<dt>Start CTL</dt><dd>{fmt_num(start_ctl)}</dd>"
        f"<dt>End CTL</dt><dd>{fmt_num(end_ctl)} ({fmt_signed(delta_ctl)})</dd>"
        f"<dt>Peak ATL</dt><dd>{fmt_num(max(atls) if atls else None)}</dd>"
        f"<dt>Low TSB</dt><dd>{fmt_num(min(tsbs) if tsbs else None)}</dd>"
        "</dl></div>"
    )


_DATED_HEADING = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _changelog_section(plan_id: str, week_id: str, *, root: Path | None) -> str:
    """Extract changelog entries dated within ``week_id``."""
    path = plans.plan_dir(plan_id, root=root) / "changelog.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    matches = list(_DATED_HEADING.finditer(text))
    if not matches:
        return ""

    from datetime import date as _date

    w_start = plans.week_start(week_id)
    w_end = plans.week_end(week_id)
    chunks: list[str] = []
    for i, m in enumerate(matches):
        try:
            entry_date = _date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if not (w_start <= entry_date <= w_end):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunks.append(text[m.start():end].rstrip())

    if not chunks:
        return ""
    body = escape("\n\n".join(chunks))
    return "<h2>Changelog</h2>" + f"<pre class='changelog'>{body}</pre>"


__all__ = ["render_week"]
