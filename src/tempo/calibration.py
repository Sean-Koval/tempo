"""Programmatic detection of a plan's outstanding calibration TODOs.

When a plan is bootstrapped with empty inputs (no FTP, no preferences, a
placeholder race date), the agent silently substitutes assumptions —
perceived-effort scaling, generic 8h/wk template defaults. Without a UI
cue, those assumptions never get revisited.

:func:`calibration_debt` walks the athlete + plan + db state and returns
a structured list of debts so the CLI ``coach doctor`` / ``coach status``
and the macro dashboard can surface "what's still placeholder" at a glance.

Severity:
- ``warn``  — plan still drafts, but with assumed numbers; revisit when data lands.
- ``fail``  — load-bearing input that should not run as-is (e.g. placeholder race date).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import athlete, plans
from .db import connect, init_schema
from .paths import coach_db_path, repo_root

Severity = Literal["warn", "fail"]


@dataclass
class DebtItem:
    """One outstanding calibration debt against a plan."""

    field: str
    severity: Severity
    message: str
    suggested_fix: str
    blocks: list[str] = field(default_factory=list)


_PLACEHOLDER_PROFILE_VALUES: frozenset[Any] = frozenset({None, "", "TODO", "TBD"})


def _is_blank(value: Any) -> bool:
    """Treat None, empty string, and TODO/TBD strings as not-yet-filled."""
    if value in _PLACEHOLDER_PROFILE_VALUES:
        return True
    if isinstance(value, str) and value.strip().upper() in {"TODO", "TBD", ""}:
        return True
    return False


def _profile_debts(root: Path | None) -> list[DebtItem]:
    profile = athlete.load_profile(root)
    thresholds = profile.get("thresholds") or {}
    athlete_blk = profile.get("athlete") or {}

    out: list[DebtItem] = []

    if _is_blank(thresholds.get("ftp_w")):
        out.append(
            DebtItem(
                field="athlete.profile.thresholds.ftp_w",
                severity="warn",
                message="FTP not set — bike TSS targets are scaled from generic assumptions.",
                suggested_fix=(
                    "After a 20-minute or ramp test, set thresholds.ftp_w in "
                    "athlete/profile.yaml and recalibrate weekly_tss_target."
                ),
                blocks=["plan-training-week (numerically-real bike TSS)"],
            )
        )

    if _is_blank(thresholds.get("lthr_bpm")) and _is_blank(thresholds.get("max_hr")):
        out.append(
            DebtItem(
                field="athlete.profile.thresholds.lthr_bpm",
                severity="warn",
                message="LTHR / max HR not set — HR-zone targets fall back to RPE.",
                suggested_fix=(
                    "Run a 30/20 test or use a recent threshold race; populate "
                    "thresholds.lthr_bpm in athlete/profile.yaml."
                ),
                blocks=["plan-training-week (HR-zone session targeting)"],
            )
        )

    if _is_blank(athlete_blk.get("weight_kg")):
        out.append(
            DebtItem(
                field="athlete.profile.athlete.weight_kg",
                severity="warn",
                message="Body weight not set — power-to-weight and fueling math use defaults.",
                suggested_fix="Set athlete.weight_kg in athlete/profile.yaml.",
                blocks=["plan-training-week (W/kg interpretation)", "race nutrition kcal targets"],
            )
        )

    return out


def _preferences_debts(root: Path | None) -> list[DebtItem]:
    out: list[DebtItem] = []
    path = athlete.athlete_dir(root) / "preferences.md"
    if not path.is_file():
        return out

    text = path.read_text(encoding="utf-8")

    # Heuristic match: the template seeds these as "Field: # TODO" or "# e.g. ...".
    indicators = [
        ("Typical weekly training hours", "athlete.preferences.weekly_hours"),
        ("Available days for long sessions", "athlete.preferences.long_day_pattern"),
        ("Preferred hard-day pattern", "athlete.preferences.hard_day_pattern"),
    ]
    for label, field_id in indicators:
        for line in text.splitlines():
            if line.startswith(f"- {label}"):
                value_part = line.partition(":")[2].strip()
                if value_part.startswith("#") or _is_blank(value_part.lstrip("# ")):
                    out.append(
                        DebtItem(
                            field=field_id,
                            severity="warn",
                            message=f"{label} not specified in athlete/preferences.md.",
                            suggested_fix=(
                                f"Replace the placeholder for '{label}' with a concrete value "
                                "(e.g., '8' or 'Sat + Sun')."
                            ),
                            blocks=["plan-training-week (volume/day-of-week placement)"],
                        )
                    )
                break

    return out


def _race_debts(plan_doc: dict[str, Any], root: Path | None) -> list[DebtItem]:
    out: list[DebtItem] = []
    goal_id = plan_doc.get("goal_id") or plan_doc.get("plan_id")
    if not goal_id:
        return out

    match = athlete.find_goal(goal_id, root=root)
    if match is None or match.kind != "race":
        return out

    race = match.data
    if str(race.get("location") or "").strip().upper() == "TBD":
        out.append(
            DebtItem(
                field=f"athlete.race-calendar[{goal_id}].location",
                severity="fail",
                message="Race location is 'TBD' — venue affects taper logistics and conditions.",
                suggested_fix=(
                    "Pick the actual race in athlete/race-calendar.yaml; update id, "
                    "date, location, and expected_conditions; re-run /bootstrap-plan."
                ),
                blocks=["draft-race-plan", "plan-training-week (final taper)"],
            )
        )
    elif "TBD" in str(race.get("name") or ""):
        out.append(
            DebtItem(
                field=f"athlete.race-calendar[{goal_id}].name",
                severity="fail",
                message="Race name is still a placeholder.",
                suggested_fix="Replace 'TBD venue' in athlete/race-calendar.yaml with the actual event.",
                blocks=["draft-race-plan"],
            )
        )

    finish = (race.get("goals") or {}).get("finish_time")
    if isinstance(finish, str) and finish.strip().upper() == "TBD":
        out.append(
            DebtItem(
                field=f"athlete.race-calendar[{goal_id}].goals.finish_time",
                severity="warn",
                message="No finish-time goal — pacing targets default to 'finish strong'.",
                suggested_fix="Set goals.finish_time in athlete/race-calendar.yaml once a target is committed.",
                blocks=["draft-race-plan (pacing math)"],
            )
        )

    return out


def _load_history_debts(*, conn: sqlite3.Connection | None, min_days: int = 28) -> list[DebtItem]:
    db_path = coach_db_path()
    if conn is None and not db_path.is_file():
        return [
            DebtItem(
                field="coach.db.load_daily",
                severity="warn",
                message="coach.db not present — no actuals available for CTL recalibration.",
                suggested_fix="Run `coach sync` to pull activities and derive load.",
                blocks=["plan recalibration (CTL trajectory)"],
            )
        ]

    owns_conn = False
    if conn is None:
        conn = connect()
        owns_conn = True
    try:
        init_schema(conn)
        row = conn.execute("SELECT COUNT(*) AS n FROM load_daily").fetchone()
        days = int(row["n"]) if row else 0
    except Exception:
        return [
            DebtItem(
                field="coach.db.load_daily",
                severity="warn",
                message="coach.db unreachable — falling back to plan defaults.",
                suggested_fix="Run `coach doctor` for the underlying error, then `coach sync`.",
                blocks=["plan recalibration (CTL trajectory)"],
            )
        ]
    finally:
        if owns_conn:
            conn.close()

    if days < min_days:
        return [
            DebtItem(
                field="coach.db.load_daily",
                severity="warn",
                message=(
                    f"Only {days} day(s) of derived load history; need ≥ {min_days} "
                    "before TSS targets can be recalibrated against actuals."
                ),
                suggested_fix=f"Keep training and run `coach sync` daily — recheck after {min_days} days.",
                blocks=["plan recalibration (CTL trajectory)"],
            )
        ]
    return []


def _injury_knowledge_debts(root: Path | None) -> list[DebtItem]:
    flags = athlete.active_injury_flags(root)
    if not flags:
        return []

    research_dir = (root or repo_root()) / "knowledge" / "research"
    has_research = research_dir.is_dir() and any(research_dir.rglob("*.md"))

    if has_research:
        return []

    return [
        DebtItem(
            field="knowledge.research.injury",
            severity="warn",
            message=(
                f"Active injury flags ({len(flags)}) but knowledge/research/ is empty — "
                "return-to-sport guidance is unsourced."
            ),
            suggested_fix=(
                "Run /ingest-research on a peer-reviewed paper that covers the injury "
                "type (e.g., Warden 2014 BJSM for tibial bone-stress)."
            ),
            blocks=["plan-training-week (return-to-sport progression)"],
        )
    ]


def calibration_debt(
    plan_id: str | None = None,
    *,
    root: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[DebtItem]:
    """Return all outstanding calibration debts against the active plan.

    If ``plan_id`` is None, auto-detects via :func:`plans.find_single_plan`.
    Returns an empty list if no plan is found — there is nothing to calibrate
    against without one.
    """
    if plan_id is None:
        try:
            found = plans.find_single_plan(root=root)
        except plans.MultiplePlansError:
            return []
        if found is None:
            return []
        plan_id, plan_doc = found
    else:
        plan_doc = plans.read_plan_yaml(plan_id, root=root) or {}

    debts: list[DebtItem] = []
    debts.extend(_profile_debts(root))
    debts.extend(_preferences_debts(root))
    debts.extend(_race_debts(plan_doc, root))
    debts.extend(_load_history_debts(conn=conn))
    debts.extend(_injury_knowledge_debts(root))
    return debts


__all__ = ["DebtItem", "Severity", "calibration_debt"]
