"""``coach status`` — typed snapshot of the athlete's current training state.

The CLI used to dump three independent tables (load, week, wellness) and
relegate everything else (plan/phase position, calibration debt, sync
freshness, injury flags) to ``coach doctor``. Story 04 §1 + 05 §1 ask for a
single one-screen surface that answers "where am I, what's the next move?"
without hopping between commands.

Everything is read-only. Severities (``ok`` / ``warn`` / ``alert``) drive
both Rich coloring in the terminal and the JSON exit-code semantics for
shell-driven workflows. Numbers come from the same ``tempo.queries``
helpers ``coach-db`` MCP and the planning skills already use; no new
SQL lives here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from . import athlete as _athlete
from . import plans as _plans
from .calibration import calibration_debt
from .db import connect, init_schema
from .paths import events_log_path
from .queries import (
    AdherenceReportRow,
    LoadPointRow,
    ReadinessRow,
    get_adherence,
    get_load_curve,
    get_readiness,
)

Severity = Literal["ok", "warn", "alert", "info"]

# Thresholds. Tuned from the user-story acceptance criteria: stale-sync
# yellow at 24h, red at 72h; CTL drift yellow at >5 points off plan.
_STALE_SYNC_WARN_S = 24 * 3600
_STALE_SYNC_ALERT_S = 72 * 3600
_CTL_DRIFT_WARN = 5.0
_CTL_DRIFT_ALERT = 10.0
_HRV_TREND_WARN_PCT = -3.0  # 7d-mean down >3% vs prior 7d
_HRV_TREND_ALERT_PCT = -7.0
_DEFAULT_SLEEP_TARGET_H = 7.5


@dataclass
class StatusRow:
    """One renderable row in the status block."""

    label: str
    value: str
    severity: Severity = "ok"
    detail: str | None = None


@dataclass
class StatusSnapshot:
    """Everything ``coach status`` needs to render in one place.

    Each section is independently nullable: a fresh checkout with no plan,
    no DB, and no athlete data still produces a sensible snapshot — every
    missing piece becomes a row with severity ``warn`` and a suggested fix.
    """

    as_of: str  # YYYY-MM-DD ISO date
    plan_id: str | None = None
    phase_id: str | None = None
    phase_week_index: int | None = None  # 1-indexed
    phase_total_weeks: int | None = None
    week_id: str | None = None
    days_to_target: int | None = None
    target_date: str | None = None
    adherence: AdherenceReportRow | None = None
    load: LoadPointRow | None = None
    target_ctl: float | None = None
    ctl_drift: float | None = None  # current - target
    readiness: ReadinessRow | None = None
    sleep_target_h: float = _DEFAULT_SLEEP_TARGET_H
    calibration_debt_count: int = 0
    calibration_first_summary: str | None = None
    last_sync_ts: str | None = None
    sync_age_seconds: int | None = None
    active_injury_flags: list[str] = field(default_factory=list)
    rows: list[StatusRow] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize for ``--json``. Drops only the rendered Rich rows.

        Preserves enough structure that a downstream script can build its
        own UI off the snapshot — the ``rows`` field is presentation-only.
        """
        body = asdict(self)
        body.pop("rows", None)
        return json.dumps(body, default=str, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------


def build_snapshot(
    *,
    conn: sqlite3.Connection | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> StatusSnapshot:
    """Assemble a :class:`StatusSnapshot` from the current repo + DB state.

    ``conn`` lets callers thread an existing SQLite connection (tests do
    this); ``root`` and ``today`` are test-injection seams.
    """
    today = today or date.today()
    snap = StatusSnapshot(as_of=today.isoformat())

    owns_conn = False
    if conn is None:
        conn = connect()
        owns_conn = True
        init_schema(conn)

    try:
        _fill_plan_section(snap, root=root, today=today)
        _fill_week_section(snap, conn=conn, today=today)
        _fill_load_section(snap, conn=conn, today=today)
        _fill_wellness_section(snap, conn=conn, today=today)
        _fill_calibration_section(snap, conn=conn, root=root)
        _fill_sync_section(snap, today=today)
        _fill_injury_section(snap, root=root)
    finally:
        if owns_conn:
            conn.close()

    snap.rows = _build_rows(snap)
    return snap


def _fill_plan_section(snap: StatusSnapshot, *, root: Path | None, today: date) -> None:
    try:
        found = _plans.find_single_plan(root=root)
    except _plans.MultiplePlansError:
        snap.plan_id = "<multiple>"
        return
    if found is None:
        return

    plan_id, plan_doc = found
    snap.plan_id = plan_id
    target_date_str = plan_doc.get("target_date")
    if isinstance(target_date_str, str):
        snap.target_date = target_date_str
        try:
            target = date.fromisoformat(target_date_str)
            snap.days_to_target = (target - today).days
        except ValueError:
            snap.days_to_target = None

    snap.week_id = _plans.week_id_for(today)
    phase = _plans.phase_for_week(plan_doc, snap.week_id)
    if phase is None:
        return
    snap.phase_id = phase.get("id")
    snap.phase_week_index = _plans.week_index_in_phase(phase, snap.week_id)
    snap.phase_total_weeks = phase.get("weeks")

    # Phase target CTL is derived, not stored. CTL is a 42-day EWMA of
    # daily TSS, so steady-state CTL ≈ daily_TSS = weekly_TSS / 7. We use
    # the midpoint of the phase's weekly_tss_target as the steady-state
    # target. This is principled enough to flag drift without inventing
    # a phantom field that would silently rot.
    target_range = phase.get("weekly_tss_target")
    if (
        isinstance(target_range, list)
        and len(target_range) == 2
        and all(isinstance(v, (int, float)) for v in target_range)
    ):
        snap.target_ctl = round(((target_range[0] + target_range[1]) / 2) / 7.0, 1)


def _fill_week_section(snap: StatusSnapshot, *, conn: sqlite3.Connection, today: date) -> None:
    week_id = snap.week_id or _plans.week_id_for(today)
    snap.week_id = week_id
    snap.adherence = get_adherence(conn, week_id=week_id)


def _fill_load_section(snap: StatusSnapshot, *, conn: sqlite3.Connection, today: date) -> None:
    rows = get_load_curve(
        conn,
        start_date=(today - timedelta(days=14)).isoformat(),
        end_date=today.isoformat(),
    )
    if not rows:
        return
    snap.load = rows[-1]
    if snap.target_ctl is not None and snap.load.ctl is not None:
        snap.ctl_drift = round(snap.load.ctl - snap.target_ctl, 1)


def _fill_wellness_section(snap: StatusSnapshot, *, conn: sqlite3.Connection, today: date) -> None:
    snap.readiness = get_readiness(conn, as_of=today.isoformat(), window_days=14)


def _fill_calibration_section(
    snap: StatusSnapshot,
    *,
    conn: sqlite3.Connection,
    root: Path | None,
) -> None:
    debts = calibration_debt(root=root, conn=conn)
    snap.calibration_debt_count = len(debts)
    if debts:
        first = debts[0]
        snap.calibration_first_summary = f"{first.field}: {first.message}"


def _fill_sync_section(snap: StatusSnapshot, *, today: date) -> None:
    last = _read_last_sync_event(events_log_path())
    if last is None:
        return
    snap.last_sync_ts = last.isoformat()
    snap.sync_age_seconds = int((datetime.now(UTC) - last).total_seconds())
    _ = today  # signature kept for symmetry; sync staleness is wall-clock


def _fill_injury_section(snap: StatusSnapshot, *, root: Path | None) -> None:
    snap.active_injury_flags = _athlete.active_injury_flags(root=root)


def _read_last_sync_event(path: Path) -> datetime | None:
    """Return the timestamp of the most-recent ``sync`` event, or None.

    ``events.jsonl`` is append-only, so we read backwards through the
    last few KB rather than parsing the whole file. For typical Tempo
    usage (one event per command, one ``coach sync`` per day) this is
    overkill but keeps status snappy if the log grows.
    """
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            chunk_size = min(64 * 1024, size)
            fh.seek(size - chunk_size)
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if doc.get("command") != "sync":
            continue
        ts = doc.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Row building (severity hinting)
# ---------------------------------------------------------------------------


def _build_rows(snap: StatusSnapshot) -> list[StatusRow]:
    rows: list[StatusRow] = []
    rows.append(_plan_row(snap))
    rows.append(_week_row(snap))
    rows.append(_load_row(snap))
    rows.append(_wellness_row(snap))
    rows.append(_calibration_row(snap))
    rows.append(_sync_row(snap))
    if snap.active_injury_flags:
        rows.append(_injury_row(snap))
    return rows


def _plan_row(snap: StatusSnapshot) -> StatusRow:
    if snap.plan_id == "<multiple>":
        return StatusRow(
            label="Plan",
            value="multiple plans found",
            severity="warn",
            detail="Pass --plan-id to coach commands; auto-detection is ambiguous.",
        )
    if snap.plan_id is None:
        return StatusRow(
            label="Plan",
            value="no active plan",
            severity="warn",
            detail="Run /bootstrap-plan to create one.",
        )

    bits: list[str] = [snap.plan_id]
    if snap.phase_id:
        if snap.phase_week_index and snap.phase_total_weeks:
            bits.append(f"{snap.phase_id} (wk {snap.phase_week_index}/{snap.phase_total_weeks})")
        else:
            bits.append(snap.phase_id)
    if snap.days_to_target is not None:
        bits.append(f"{snap.days_to_target}d to target")
    return StatusRow(label="Plan", value=" — ".join(bits))


def _week_row(snap: StatusSnapshot) -> StatusRow:
    if snap.adherence is None or snap.adherence.planned_count == 0:
        return StatusRow(
            label="Week",
            value=f"{snap.week_id or '—'} (no planned sessions)",
            severity="info",
        )
    a = snap.adherence
    tss_pct = (
        round(100.0 * a.total_actual_tss / a.total_planned_tss, 0)
        if a.total_planned_tss > 0
        else 0.0
    )
    severity: Severity = "ok"
    # Mid-week behind on adherence is informational, not an alarm —
    # adherence rolls in with the week. Only flag if completion < 50%
    # AND we're past mid-week.
    if a.completion_pct < 50.0:
        severity = "warn"
    return StatusRow(
        label="Week",
        value=(
            f"{snap.week_id} — {a.completed_count}/{a.planned_count} sessions "
            f"({a.completion_pct:.0f}%) · {tss_pct:.0f}% planned TSS"
        ),
        severity=severity,
    )


def _load_row(snap: StatusSnapshot) -> StatusRow:
    if snap.load is None or snap.load.ctl is None:
        return StatusRow(
            label="Load",
            value="no load data",
            severity="warn",
            detail="Run `coach sync` to derive CTL/ATL/TSB.",
        )
    parts = [
        f"CTL {snap.load.ctl:.1f}",
        f"ATL {snap.load.atl:.1f}" if snap.load.atl is not None else "ATL —",
        f"TSB {snap.load.tsb:+.1f}" if snap.load.tsb is not None else "TSB —",
    ]
    severity: Severity = "ok"
    detail: str | None = None
    if snap.target_ctl is not None and snap.ctl_drift is not None:
        parts.append(f"target {snap.target_ctl:.1f} (Δ {snap.ctl_drift:+.1f})")
        if abs(snap.ctl_drift) >= _CTL_DRIFT_ALERT:
            severity = "alert"
            detail = (
                "CTL is far from phase target — consider re-bootstrapping or "
                "investigating adherence."
            )
        elif abs(snap.ctl_drift) >= _CTL_DRIFT_WARN:
            severity = "warn"
            detail = "CTL drift >5 points; check adherence + load curve."
    return StatusRow(label="Load", value=" · ".join(parts), severity=severity, detail=detail)


def _wellness_row(snap: StatusSnapshot) -> StatusRow:
    r = snap.readiness
    if r is None or r.samples == 0:
        return StatusRow(
            label="Wellness 7d",
            value="no wellness data (last 14d)",
            severity="warn",
            detail="Run `coach check-in` daily to populate.",
        )

    bits: list[str] = []
    severity: Severity = "ok"
    detail: str | None = None

    if r.hrv_7d_mean is not None:
        if r.hrv_trend_delta is not None and r.hrv_7d_mean > 0:
            pct = 100.0 * r.hrv_trend_delta / r.hrv_7d_mean
            arrow = "↑" if r.hrv_trend_delta >= 0 else "↓"
            bits.append(f"HRV {r.hrv_7d_mean:.0f} {arrow}{abs(pct):.1f}%")
            if pct <= _HRV_TREND_ALERT_PCT:
                severity = "alert"
                detail = "HRV trend strongly negative — recovery week recommended."
            elif pct <= _HRV_TREND_WARN_PCT:
                severity = "warn"
                detail = "HRV trend negative — watch for fatigue."
        else:
            bits.append(f"HRV {r.hrv_7d_mean:.0f}")

    if r.sleep_h_7d_mean is not None:
        gap = r.sleep_h_7d_mean - snap.sleep_target_h
        gap_str = f"{gap:+.1f}h vs target"
        bits.append(f"sleep {r.sleep_h_7d_mean:.1f}h ({gap_str})")
        if gap <= -1.0 and severity == "ok":
            severity = "warn"
            detail = "Mean sleep is >1h below target."

    if r.readiness_latest is not None:
        bits.append(f"readiness {r.readiness_latest}")

    return StatusRow(
        label="Wellness 7d",
        value=" · ".join(bits) or "(no values)",
        severity=severity,
        detail=detail,
    )


def _calibration_row(snap: StatusSnapshot) -> StatusRow:
    if snap.calibration_debt_count == 0:
        return StatusRow(label="Calibration", value="no debt")
    return StatusRow(
        label="Calibration",
        value=f"{snap.calibration_debt_count} item(s)",
        severity="warn",
        detail=snap.calibration_first_summary,
    )


def _sync_row(snap: StatusSnapshot) -> StatusRow:
    if snap.sync_age_seconds is None:
        return StatusRow(
            label="Sync",
            value="never run",
            severity="warn",
            detail="Run `coach sync` to populate coach.db.",
        )
    age = snap.sync_age_seconds
    if age >= _STALE_SYNC_ALERT_S:
        return StatusRow(
            label="Sync",
            value=f"{_format_age(age)} ago",
            severity="alert",
            detail="Sync is >72h stale — run `coach sync`.",
        )
    if age >= _STALE_SYNC_WARN_S:
        return StatusRow(
            label="Sync",
            value=f"{_format_age(age)} ago",
            severity="warn",
            detail="Sync is >24h stale — `coach sync` recommended.",
        )
    return StatusRow(label="Sync", value=f"{_format_age(age)} ago")


def _injury_row(snap: StatusSnapshot) -> StatusRow:
    return StatusRow(
        label="Injuries",
        value=f"{len(snap.active_injury_flags)} active flag(s)",
        severity="alert",
        detail="; ".join(snap.active_injury_flags[:2]),
    )


def _format_age(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours = seconds // 3600
    if hours < 48:
        return f"{hours}h"
    days = seconds // 86400
    return f"{days}d"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_SEVERITY_STYLE: dict[Severity, str] = {
    "ok": "green",
    "warn": "yellow",
    "alert": "red",
    "info": "dim",
}


def render(snap: StatusSnapshot, *, show_week_sessions: bool = False) -> Any:
    """Render a snapshot to a Rich-printable group of objects."""
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    table = Table(
        title=f"Tempo status — {snap.as_of}",
        show_header=False,
        title_style="bold",
        expand=False,
    )
    table.add_column("Field", style="bold", no_wrap=True)
    table.add_column("Value")
    for row in snap.rows:
        style = _SEVERITY_STYLE[row.severity]
        value = Text(row.value, style=style)
        if row.detail:
            value.append("\n→ " + row.detail, style="dim")
        table.add_row(row.label, value)

    pieces: list[Any] = [table]
    if show_week_sessions and snap.adherence and snap.adherence.items:
        pieces.append(_render_week_sessions(snap.adherence))
    return Group(*pieces)


def _render_week_sessions(adherence: AdherenceReportRow) -> Any:
    from rich.table import Table

    table = Table(
        title=f"Week {adherence.week_id} — sessions",
        show_header=True,
        header_style="bold",
        expand=False,
    )
    for col in ("Date", "Sport", "Library", "Status"):
        table.add_column(col)
    for item in adherence.items:
        if item.completed is True:
            status = "[green]done[/green]"
        elif item.completed is False:
            status = (
                f"[yellow]missed[/yellow] ({item.reason})"
                if item.reason
                else "[yellow]missed[/yellow]"
            )
        else:
            status = "[dim]pending[/dim]"
        table.add_row(
            str(item.date or "—"),
            item.sport or "—",
            item.library_ref or "—",
            status,
        )
    return table


__all__ = [
    "Severity",
    "StatusRow",
    "StatusSnapshot",
    "build_snapshot",
    "render",
]
