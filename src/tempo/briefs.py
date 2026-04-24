"""Brief composers for Phase 4 skill preflights.

Preflight scripts in ``.claude/skills/*/`` are thin argparse + JSON shims;
the actual assembly lives here so it's importable and testable.

Each composer reads from ``tempo.athlete``, ``tempo.plans``, and
``tempo.queries`` — the three read surfaces — and returns a plain dict ready
for ``json.dump``. Keep output under a few KB per brief.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from . import athlete, plans, queries
from .db import connect, init_schema


def _weeks_between(from_d: date, to_d: date) -> int:
    return max(0, (to_d - from_d).days // 7)


def _parse_target_date(raw: Any) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _recent_load_summary(days_back: int = 56) -> dict[str, Any]:
    """Latest CTL/ATL/TSB + per-sport CTL across the last ``days_back`` days."""
    today = date.today()
    start = today - timedelta(days=days_back)
    try:
        conn = connect()
    except Exception as e:  # pragma: no cover — defensive
        return {"error": f"could not open coach.db: {e}", "samples_days": 0}
    try:
        init_schema(conn)
        points = queries.get_load_curve(
            conn, start_date=start.isoformat(), end_date=today.isoformat()
        )
    finally:
        conn.close()

    if not points:
        return {
            "samples_days": 0,
            "note": "no load history — coach sync may not have run",
        }

    latest = points[-1]
    return {
        "samples_days": len(points),
        "latest_date": latest.date,
        "ctl": latest.ctl,
        "atl": latest.atl,
        "tsb": latest.tsb,
        "ctl_bike": latest.ctl_bike,
        "ctl_run": latest.ctl_run,
        "ctl_swim": latest.ctl_swim,
        "ramp_7d": latest.ramp_7d,
    }


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Shrink profile.yaml to the fields the agent actually reasons over."""
    thresholds = profile.get("thresholds") or {}
    athlete_info = profile.get("athlete") or {}
    return {
        "name": athlete_info.get("name"),
        "weight_kg": athlete_info.get("weight_kg"),
        "ftp_w": thresholds.get("ftp_w"),
        "lthr_bpm": thresholds.get("lthr_bpm"),
        "run_threshold_pace": thresholds.get("run_threshold_pace"),
        "swim_css_pace": thresholds.get("swim_css_pace"),
        "max_hr": thresholds.get("max_hr"),
        "resting_hr": thresholds.get("resting_hr"),
        "strengths": profile.get("strengths") or [],
        "limiters": profile.get("limiters") or [],
    }


class UnknownGoalError(ValueError):
    """Raised when a goal_id isn't present in goals.yaml or race-calendar.yaml."""

    def __init__(self, goal_id: str, known: list[str]) -> None:
        self.goal_id = goal_id
        self.known = known
        super().__init__(
            f"unknown goal id {goal_id!r}. "
            f"Known: {known or '(none declared yet)'}"
        )


def bootstrap_plan_brief(goal_id: str) -> dict[str, Any]:
    """Assemble the brief dict for the bootstrap-plan skill.

    Raises :class:`UnknownGoalError` if the goal_id isn't declared anywhere.
    """
    match = athlete.find_goal(goal_id)
    if not match:
        raise UnknownGoalError(goal_id, athlete.all_goal_ids())

    data = match.data
    target_d = _parse_target_date(data.get("target_date") or data.get("date"))
    weeks_until = _weeks_between(date.today(), target_d) if target_d else None

    distance = data.get("distance")
    template = plans.phase_template_for(
        distance=distance, has_target_date=target_d is not None
    )
    template_section: dict[str, Any] = {}
    if template:
        key, doc = template
        template_section = {"key": key, "doc": doc}

    profile = athlete.load_profile()

    return {
        "goal": {
            "id": goal_id,
            "kind": match.kind,
            "title": data.get("title") or data.get("name"),
            "target_date": target_d.isoformat() if target_d else None,
            "distance": distance,
            "priority": data.get("priority"),
            "location": data.get("location"),
            "expected_conditions": data.get("expected_conditions"),
            "constraints": data.get("constraints"),
            "notes": data.get("notes"),
            "goals": data.get("goals"),
        },
        "today": date.today().isoformat(),
        "weeks_until_target": weeks_until,
        "applicable_phase_template": template_section,
        "athlete_state": _profile_summary(profile),
        "recent_load": _recent_load_summary(),
        "active_injuries": athlete.active_injury_flags(),
        "hard_constraints": athlete.hard_constraints(),
        "existing_plan": plans.read_plan_yaml(goal_id) is not None,
    }


class NoActivePlanError(RuntimeError):
    """Raised when plan_week_brief cannot resolve a plan to draft into."""


def _phase_tss_target(phase: dict[str, Any]) -> tuple[int, int, int] | None:
    """Return ``(low, high, mid)`` for a phase's weekly_tss_target, or ``None``.

    Accepts a ``[low, high]`` list, a single scalar, or a dict with ``low``/``high``.
    """
    raw = phase.get("weekly_tss_target")
    if raw is None:
        return None
    if isinstance(raw, list) and len(raw) == 2:
        lo, hi = raw
        return int(lo), int(hi), (int(lo) + int(hi)) // 2
    if isinstance(raw, dict):
        lo = raw.get("low")
        hi = raw.get("high")
        if lo is not None and hi is not None:
            return int(lo), int(hi), (int(lo) + int(hi)) // 2
    if isinstance(raw, (int, float)):
        v = int(raw)
        return v, v, v
    return None


def _sum_week_tss(conn: sqlite3.Connection, week_id: str) -> float:
    """Sum ``activities.tss`` for a given ISO week (actuals)."""
    start = plans.week_start(week_id).isoformat()
    end = plans.week_end(week_id).isoformat() + "T23:59:59"
    row = conn.execute(
        "SELECT COALESCE(SUM(tss), 0.0) AS total "
        "FROM activities WHERE start_date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()
    return float(row["total"] or 0.0)


def _adherence_summary(report: queries.AdherenceReportRow) -> dict[str, Any]:
    """Drop full item list into a compact dict — keep brief under size budget."""
    return {
        "week_id": report.week_id,
        "planned_count": report.planned_count,
        "completed_count": report.completed_count,
        "skipped_count": report.skipped_count,
        "moved_count": report.moved_count,
        "completion_pct": report.completion_pct,
        "total_planned_tss": report.total_planned_tss,
        "total_actual_tss": report.total_actual_tss,
        "items": [asdict(i) for i in report.items],
    }


def _load_curve_rows(
    conn: sqlite3.Connection, *, start: str, end: str
) -> list[dict[str, Any]]:
    return [asdict(p) for p in queries.get_load_curve(conn, start_date=start, end_date=end)]


def plan_week_brief(
    week_id: str | None = None,
    *,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the plan-training-week brief.

    Args:
        week_id: ISO week id (``YYYY-Www``). Defaults to the ISO week containing
            seven days from today — i.e., "next week" for a typical Sunday draft.
        plan_id: Explicit plan id. Defaults to auto-detection (errors on multi).

    Raises:
        NoActivePlanError: when no plan.yaml is found.
        plans.MultiplePlansError: when auto-detection hits multiple plans.
    """
    today = date.today()
    if week_id is None:
        week_id = plans.week_id_for(today + timedelta(days=7))

    if plan_id is not None:
        plan_doc = plans.read_plan_yaml(plan_id)
        if plan_doc is None:
            raise NoActivePlanError(f"plan {plan_id!r} not found under plans/")
        resolved_plan_id = plan_id
    else:
        found = plans.find_single_plan()
        if found is None:
            raise NoActivePlanError(
                "no plan found under plans/ — run /bootstrap-plan first"
            )
        resolved_plan_id, plan_doc = found

    phase = plans.phase_for_week(plan_doc, week_id)
    week_of_phase = plans.week_index_in_phase(phase, week_id) if phase else None
    weeks_remaining = None
    if phase and week_of_phase is not None:
        total = int(phase.get("weeks") or 0)
        weeks_remaining = max(0, total - week_of_phase)

    tss_target = _phase_tss_target(phase) if phase else None
    target_mid = tss_target[2] if tss_target else None

    prev1_id = plans.shift_week(week_id, weeks=-1)
    prev2_id = plans.shift_week(week_id, weeks=-2)
    start_14d = (plans.week_start(week_id) - timedelta(days=14)).isoformat()
    end_14d = (plans.week_start(week_id) - timedelta(days=1)).isoformat()

    recent_load: list[dict[str, Any]] = []
    latest_load: dict[str, Any] | None = None
    readiness_snap: dict[str, Any] = {"as_of": end_14d, "samples": 0}
    recent_adherence: dict[str, Any] = {"week_id": prev1_id, "planned_count": 0}
    prior_adherence: dict[str, Any] = {"week_id": prev2_id, "planned_count": 0}
    recent_week_tss = {prev1_id: 0.0, prev2_id: 0.0}
    delta_ctl_vs_plan: float | None = None
    db_error: str | None = None

    try:
        conn = connect()
    except Exception as e:  # pragma: no cover — defensive
        db_error = f"could not open coach.db: {e}"
        conn = None

    if conn is not None:
        try:
            init_schema(conn)
            recent_load = _load_curve_rows(conn, start=start_14d, end=end_14d)
            if recent_load:
                latest_load = recent_load[-1]
            readiness_snap = asdict(
                queries.get_readiness(conn, as_of=end_14d, window_days=14)
            )
            recent_adherence = _adherence_summary(
                queries.get_adherence(conn, week_id=prev1_id)
            )
            prior_adherence = _adherence_summary(
                queries.get_adherence(conn, week_id=prev2_id)
            )
            recent_week_tss = {
                prev1_id: _sum_week_tss(conn, prev1_id),
                prev2_id: _sum_week_tss(conn, prev2_id),
            }
            if target_mid is not None and latest_load and latest_load.get("ctl") is not None:
                target_ss_ctl = target_mid / 7.0
                delta_ctl_vs_plan = float(latest_load["ctl"]) - target_ss_ctl
        finally:
            conn.close()

    profile = athlete.load_profile()
    week_already_drafted = plans.week_file(resolved_plan_id, week_id).is_file()

    raw_target = plan_doc.get("target_date")
    if isinstance(raw_target, date):
        target_date_out: str | None = raw_target.isoformat()
    elif raw_target is None:
        target_date_out = None
    else:
        target_date_out = str(raw_target)

    return {
        "week_id": week_id,
        "week_start": plans.week_start(week_id).isoformat(),
        "week_end": plans.week_end(week_id).isoformat(),
        "today": today.isoformat(),
        "plan": {
            "plan_id": resolved_plan_id,
            "template": plan_doc.get("template"),
            "target_date": target_date_out,
            "phase": phase,
            "week_of_phase": week_of_phase,
            "weeks_remaining_in_phase": weeks_remaining,
            "weekly_tss_target_mid": target_mid,
        },
        "ctl_drift": {
            "actual_ctl_latest": latest_load.get("ctl") if latest_load else None,
            "target_steady_state_ctl": (
                round(target_mid / 7.0, 1) if target_mid is not None else None
            ),
            "delta_ctl_vs_plan": (
                round(delta_ctl_vs_plan, 1) if delta_ctl_vs_plan is not None else None
            ),
        },
        "recent_load_14d": recent_load,
        "readiness": readiness_snap,
        "recent_adherence": recent_adherence,
        "prior_adherence": prior_adherence,
        "recent_weekly_tss": recent_week_tss,
        "active_injuries": athlete.active_injury_flags(),
        "hard_constraints": athlete.hard_constraints(),
        "athlete_state": _profile_summary(profile),
        "week_already_drafted": week_already_drafted,
        "db_error": db_error,
    }


__all__ = [
    "NoActivePlanError",
    "UnknownGoalError",
    "bootstrap_plan_brief",
    "plan_week_brief",
]
