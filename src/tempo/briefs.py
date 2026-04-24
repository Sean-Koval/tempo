"""Brief composers for Phase 4 skill preflights.

Preflight scripts in ``.claude/skills/*/`` are thin argparse + JSON shims;
the actual assembly lives here so it's importable and testable.

Each composer reads from ``tempo.athlete``, ``tempo.plans``, and
``tempo.queries`` — the three read surfaces — and returns a plain dict ready
for ``json.dump``. Keep output under a few KB per brief.
"""

from __future__ import annotations

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


__all__ = ["UnknownGoalError", "bootstrap_plan_brief"]
