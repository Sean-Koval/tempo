"""24-week macro timeline dashboard: phases, weekly TSS targets, current position."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Any

from .. import plans
from ..db import connect, init_schema
from .common import fmt_num, fmt_signed, page

_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"


def render_macro(
    plan_id: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> str:
    """Render the macro timeline. Returns the full HTML doc.

    Reads ``plans/<plan_id>/plan.yaml`` for structure; the DB for "today's
    actual CTL" so the current-position card can show drift vs target.
    """
    today = today or date.today()
    resolved_plan_id, plan_doc = _resolve_plan(plan_id, root=root)
    if plan_doc is None:
        return _missing_plan_html(plan_id)

    body_parts: list[str] = [_header(resolved_plan_id, plan_doc)]

    debt_card = _calibration_debt_card(resolved_plan_id, conn=conn, root=root)
    if debt_card:
        body_parts.append(debt_card)

    gantt = _gantt(plan_doc, today=today)
    if gantt:
        body_parts.append(gantt)

    body_parts.append(_current_position_card(plan_doc, conn=conn, today=today))
    body_parts.append(_weekly_tss_table(plan_doc, conn=conn, today=today))
    body_parts.append(_race_markers_card(plan_doc))

    body_parts.append(_mermaid_init_script())

    return page(
        f"Macro · {resolved_plan_id}",
        "\n".join(body_parts),
        footer_note=(
            "coach dashboard macro — Mermaid loaded from CDN "
            "(needs network on first view)"
        ),
    )


def _resolve_plan(
    plan_id: str | None, *, root: Path | None
) -> tuple[str | None, dict[str, Any] | None]:
    if plan_id is not None:
        doc = plans.read_plan_yaml(plan_id, root=root)
        return (plan_id, doc) if doc is not None else (plan_id, None)
    try:
        found = plans.find_single_plan(root=root)
    except plans.MultiplePlansError as e:
        return None, {"_error": f"multiple plans found — pass --plan-id: {e.plan_ids}"}
    if found is None:
        return None, None
    return found


def _missing_plan_html(plan_id: str | None) -> str:
    msg = (
        f"Plan {escape(plan_id)!r} not found under plans/."
        if plan_id
        else "No plan found under plans/. Run /bootstrap-plan first."
    )
    body = (
        "<header class='page'><h1>Macro</h1></header>"
        f"<p class='notice'>{msg}</p>"
    )
    return page("Macro · (missing plan)", body)


def _header(plan_id: str | None, plan_doc: dict[str, Any]) -> str:
    if plan_doc.get("_error"):
        return (
            "<header class='page'><h1>Macro</h1>"
            f"<p class='notice'>{escape(str(plan_doc['_error']))}</p></header>"
        )
    template = plan_doc.get("template") or "(custom)"
    start = _yaml_date(plan_doc.get("start_date"))
    target = _yaml_date(plan_doc.get("target_date"))
    total = plan_doc.get("total_weeks") or _sum_phase_weeks(plan_doc)
    hours = plan_doc.get("weekly_hours_budget")

    return (
        "<header class='page'>"
        f"<h1>{escape(plan_id or '(plan)')}</h1>"
        f"<div class='subtitle'>template: {escape(str(template))}</div>"
        "<div class='card'><dl class='kv'>"
        f"<dt>Start</dt><dd>{escape(start or '—')}</dd>"
        f"<dt>Target</dt><dd>{escape(target or '—')}</dd>"
        f"<dt>Total weeks</dt><dd>{escape(str(total) if total else '—')}</dd>"
        f"<dt>Weekly hours budget</dt><dd>{escape(str(hours) if hours else '—')}</dd>"
        "</dl></div>"
        "</header>"
    )


def _gantt(plan_doc: dict[str, Any], *, today: date) -> str:
    phases = plan_doc.get("phases") or []
    if not phases:
        return ""

    title = escape(str(plan_doc.get("plan_id") or "plan"))
    lines: list[str] = [
        "gantt",
        f"  title {title}",
        "  dateFormat YYYY-MM-DD",
        "  todayMarker stroke-width:2px,stroke:#dc2626,opacity:0.8",
        "  axisFormat %b %d",
    ]

    current_phase_id: str | None = None
    for phase in phases:
        phase_id = str(phase.get("id") or "phase")
        start_week = phase.get("start_week")
        weeks = phase.get("weeks")
        if not start_week or not weeks:
            continue
        try:
            start_d = plans.week_start(str(start_week))
        except (ValueError, AttributeError):
            continue
        end_d = start_d + timedelta(weeks=int(weeks))
        is_active = start_d <= today < end_d
        if is_active:
            current_phase_id = phase_id
        marker = "active, " if is_active else ""
        # Mermaid Gantt syntax: section + task per phase keeps each as its own row.
        lines.append(f"  section {_safe_mermaid(phase_id)}")
        lines.append(
            f"    {_safe_mermaid(phase_id)} :{marker}{start_d.isoformat()}, "
            f"{int(weeks) * 7}d"
        )

    # Render race markers as milestones (zero-width) attached to a final section.
    race_markers = plan_doc.get("race_markers") or []
    if race_markers:
        lines.append("  section races")
        for rm in race_markers:
            wid = rm.get("week_id")
            if not wid:
                continue
            try:
                rd = plans.week_start(str(wid))
            except (ValueError, AttributeError):
                continue
            kind = rm.get("kind") or "?"
            label = f"race-{kind}"
            lines.append(f"    {_safe_mermaid(label)} :milestone, {rd.isoformat()}, 0d")

    body = "\n".join(lines)
    note = (
        f"<div class='subtitle'>current phase: <strong>{escape(current_phase_id)}</strong></div>"
        if current_phase_id
        else ""
    )
    return (
        "<h2>Timeline</h2>"
        f"{note}"
        f"<div class='card'><pre class='mermaid'>{escape(body)}</pre></div>"
    )


def _safe_mermaid(text: str) -> str:
    """Mermaid task names can't contain ``:`` — replace defensively."""
    return text.replace(":", "-").replace("\n", " ")


def _current_position_card(
    plan_doc: dict[str, Any],
    *,
    conn: sqlite3.Connection | None,
    today: date,
) -> str:
    week_id = plans.week_id_for(today)
    phase = plans.phase_for_week(plan_doc, week_id)
    week_of_phase = plans.week_index_in_phase(phase, week_id) if phase else None
    target_mid = _phase_target_mid(phase) if phase else None

    actual_ctl = _latest_ctl(conn, today)
    target_ss_ctl = (target_mid / 7.0) if target_mid else None
    delta_ctl = (
        (actual_ctl - target_ss_ctl)
        if (actual_ctl is not None and target_ss_ctl is not None)
        else None
    )

    cumulative_planned = _cumulative_planned_tss(plan_doc, today=today)
    cumulative_actual = _cumulative_actual_tss(conn, plan_doc, today=today)

    phase_label = (
        f"{escape(str(phase.get('id') or '?'))} (week {week_of_phase}/{phase.get('weeks', '?')})"
        if phase
        else "no phase mapped"
    )
    return (
        "<h2>Current position</h2>"
        "<div class='card'><dl class='kv'>"
        f"<dt>Week</dt><dd>{escape(week_id)}</dd>"
        f"<dt>Phase</dt><dd>{phase_label}</dd>"
        f"<dt>Target weekly TSS (mid)</dt><dd>{fmt_num(target_mid, precision=0)}</dd>"
        f"<dt>Target steady-state CTL</dt><dd>{fmt_num(target_ss_ctl)}</dd>"
        f"<dt>Actual CTL (latest)</dt><dd>{fmt_num(actual_ctl)} "
        f"({fmt_signed(delta_ctl)})</dd>"
        f"<dt>Cumulative planned TSS</dt><dd>{fmt_num(cumulative_planned, precision=0)}</dd>"
        f"<dt>Cumulative actual TSS</dt><dd>{fmt_num(cumulative_actual, precision=0)}</dd>"
        "</dl></div>"
    )


def _weekly_tss_table(
    plan_doc: dict[str, Any],
    *,
    conn: sqlite3.Connection | None,
    today: date,
) -> str:
    phases = plan_doc.get("phases") or []
    if not phases:
        return ""
    rows: list[str] = []
    actuals = _weekly_actual_tss(conn, plan_doc) if conn is not None else {}
    today_wid = plans.week_id_for(today)
    for phase in phases:
        target = _phase_target_mid(phase)
        sw = phase.get("start_week")
        weeks = phase.get("weeks")
        if not sw or not weeks:
            continue
        for w in range(int(weeks)):
            wid = plans.shift_week(str(sw), weeks=w)
            actual = actuals.get(wid)
            is_now = wid == today_wid
            row_class = " class='completed'" if is_now else ""
            rows.append(
                f"<tr{row_class}>"
                f"<td>{escape(wid)}{' ←' if is_now else ''}</td>"
                f"<td>{escape(str(phase.get('id') or '—'))}</td>"
                f"<td>{fmt_num(target, precision=0)}</td>"
                f"<td>{fmt_num(actual, precision=0)}</td>"
                "</tr>"
            )
    return (
        "<h2>Weekly TSS</h2>"
        "<table><thead><tr>"
        "<th>Week</th><th>Phase</th><th>Target (mid)</th><th>Actual</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _race_markers_card(plan_doc: dict[str, Any]) -> str:
    markers = plan_doc.get("race_markers") or []
    if not markers:
        return ""
    items: list[str] = []
    for m in markers:
        wid = m.get("week_id")
        kind = m.get("kind") or "?"
        note = m.get("note") or ""
        items.append(
            f"<li><span class='chip'>{escape(str(kind))}</span> "
            f"{escape(str(wid or '—'))} — {escape(str(note))}</li>"
        )
    return "<h2>Race markers</h2><div class='card'><ul>" + "".join(items) + "</ul></div>"


def _mermaid_init_script() -> str:
    return (
        "<script type='module'>\n"
        f"import mermaid from '{_MERMAID_CDN}';\n"
        "mermaid.initialize({ startOnLoad: true, theme: 'default' });\n"
        "</script>"
    )


def _yaml_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _sum_phase_weeks(plan_doc: dict[str, Any]) -> int | None:
    phases = plan_doc.get("phases") or []
    total = 0
    for p in phases:
        try:
            total += int(p.get("weeks") or 0)
        except (TypeError, ValueError):
            continue
    return total or None


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


def _latest_ctl(conn: sqlite3.Connection | None, today: date) -> float | None:
    owns = conn is None
    if conn is None:
        try:
            conn = connect()
            init_schema(conn)
        except Exception:
            return None
    try:
        row = conn.execute(
            "SELECT ctl FROM load_daily WHERE date <= ? ORDER BY date DESC LIMIT 1",
            (today.isoformat(),),
        ).fetchone()
        return float(row["ctl"]) if row and row["ctl"] is not None else None
    finally:
        if owns:
            conn.close()


def _cumulative_planned_tss(plan_doc: dict[str, Any], *, today: date) -> float | None:
    """Sum target_mid TSS for every plan week that started on or before today."""
    total = 0.0
    seen_any = False
    for phase in plan_doc.get("phases") or []:
        target = _phase_target_mid(phase)
        sw = phase.get("start_week")
        weeks = phase.get("weeks")
        if target is None or not sw or not weeks:
            continue
        try:
            phase_start = plans.week_start(str(sw))
        except (ValueError, AttributeError):
            continue
        for w in range(int(weeks)):
            week_d = phase_start + timedelta(weeks=w)
            if week_d > today:
                break
            total += float(target)
            seen_any = True
    return total if seen_any else None


def _cumulative_actual_tss(
    conn: sqlite3.Connection | None,
    plan_doc: dict[str, Any],
    *,
    today: date,
) -> float | None:
    """Sum activities.tss between plan start and today."""
    start = _yaml_date(plan_doc.get("start_date"))
    if not start:
        return None
    owns = conn is None
    if conn is None:
        try:
            conn = connect()
            init_schema(conn)
        except Exception:
            return None
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(tss), 0.0) AS total FROM activities "
            "WHERE start_date BETWEEN ? AND ?",
            (start, today.isoformat() + "T23:59:59"),
        ).fetchone()
        return float(row["total"] or 0.0)
    finally:
        if owns:
            conn.close()


def _weekly_actual_tss(
    conn: sqlite3.Connection,
    plan_doc: dict[str, Any],
) -> dict[str, float]:
    """For every week in the plan, sum activities.tss falling in that week."""
    out: dict[str, float] = {}
    for phase in plan_doc.get("phases") or []:
        sw = phase.get("start_week")
        weeks = phase.get("weeks")
        if not sw or not weeks:
            continue
        for w in range(int(weeks)):
            wid = plans.shift_week(str(sw), weeks=w)
            try:
                w_start = plans.week_start(wid).isoformat()
                w_end = plans.week_end(wid).isoformat() + "T23:59:59"
            except (ValueError, AttributeError):
                continue
            row = conn.execute(
                "SELECT COALESCE(SUM(tss), 0.0) AS total FROM activities "
                "WHERE start_date BETWEEN ? AND ?",
                (w_start, w_end),
            ).fetchone()
            total = float(row["total"] or 0.0)
            if total > 0:
                out[wid] = total
    return out


def _calibration_debt_card(
    plan_id: str | None,
    *,
    conn: sqlite3.Connection | None,
    root: Path | None,
) -> str:
    """Render outstanding calibration debt as a card; returns '' when none."""
    from ..calibration import calibration_debt

    if plan_id is None:
        return ""

    debts = calibration_debt(plan_id, root=root, conn=conn)
    if not debts:
        return ""

    rows: list[str] = []
    for debt in debts:
        sev_class = "sev-fail" if debt.severity == "fail" else "sev-warn"
        blocks_html = (
            f"<div class='blocks'>blocks: {escape(', '.join(debt.blocks))}</div>"
            if debt.blocks
            else ""
        )
        rows.append(
            "<tr>"
            f"<td><span class='badge {sev_class}'>{escape(debt.severity)}</span></td>"
            f"<td><code>{escape(debt.field)}</code></td>"
            "<td>"
            f"<div>{escape(debt.message)}</div>"
            f"<div class='fix'>→ {escape(debt.suggested_fix)}</div>"
            f"{blocks_html}"
            "</td>"
            "</tr>"
        )

    return (
        "<section class='card debt-card'>"
        "<h2>Calibration debt</h2>"
        "<p class='subtitle'>Inputs the plan still treats as placeholders.</p>"
        "<table class='debt'><thead>"
        "<tr><th>Severity</th><th>Field</th><th>Detail</th></tr>"
        "</thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
        "<style>"
        ".debt-card .badge{padding:.1rem .5rem;border-radius:.25rem;font-size:.85em;font-weight:600;}"
        ".debt-card .sev-fail{background:#fee;color:#900;}"
        ".debt-card .sev-warn{background:#fef6e6;color:#7a4a00;}"
        ".debt-card table.debt{width:100%;border-collapse:collapse;}"
        ".debt-card table.debt td,.debt-card table.debt th{padding:.5rem;border-bottom:1px solid #eee;vertical-align:top;text-align:left;}"
        ".debt-card .fix{color:#666;font-size:.9em;margin-top:.2rem;}"
        ".debt-card .blocks{color:#888;font-size:.85em;margin-top:.2rem;font-style:italic;}"
        "</style>"
        "</section>"
    )


__all__ = ["render_macro"]
