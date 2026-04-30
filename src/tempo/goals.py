"""Typed view of ``athlete/goals.yaml`` and ``athlete/race-calendar.yaml``.

The composer used to take ``distance`` + ``runway_weeks`` directly. That
worked for races where ``distance`` is a real concept ("ironman",
"marathon"), but non-race performance goals (FTP target, 1RM target,
swim CSS) have no distance — they have a *metric*. We need a single
typed shape that covers both shapes so callers can pass *intent*
("Lena wants ftp_w 248→280 by Aug 16") instead of having to translate
to the composer's synthetic distance string by hand.

This module owns:
- :class:`Goal` — the normalized shape.
- :func:`from_match` — convert :class:`tempo.athlete.GoalMatch` into a Goal.
- The metric → template-distance map used by the composer.
- The set of supported performance-target metrics; anything outside
  raises a clear error rather than silently picking a generic template.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from . import athlete

GoalType = Literal[
    "race",
    "performance_target",
    "maintenance",
    "streak",
    "adventure",
]

_GOAL_TYPES: frozenset[str] = frozenset(
    {"race", "performance_target", "maintenance", "streak", "adventure"}
)

# Aliases tolerated in goals.yaml so old/draft shapes still load. Maps
# what the user wrote -> the canonical type.
_TYPE_ALIASES: dict[str, GoalType] = {
    "performance_maintain": "maintenance",  # used in user-story 07 draft
}


# Performance-target metrics that the composer knows how to anchor a
# chain on. Each maps to the synthetic ``distance`` string that
# :mod:`tempo.composition` looks up in ``templates`` in phases.yaml.
#
# Order matters for error messages — surface the most common first.
SUPPORTED_PERF_METRICS: dict[str, str] = {
    "ftp_w": "ftp_target",
    "css_pace": "css_target",
    "css_pace_s_per_100m": "css_target",
    "squat_1rm_kg": "strength_peak",
    "deadlift_1rm_kg": "strength_peak",
    "1rm_squat_kg": "strength_peak",
    "1rm_deadlift_kg": "strength_peak",
}


# Maintenance goals route to a generic base-building template. Open-ended
# (no by_date) goals go to the existing ``rolling_base_block_12wk``;
# dated maintenance goals go to ``base_building_8wk``.
_BASE_BUILDING_DATED = "base_building"
_BASE_BUILDING_OPEN = "rolling_base_block"


class GoalSchemaError(ValueError):
    """Raised when a goals.yaml entry can't be normalized into a Goal.

    The ``violations`` attribute carries the structured reasons so callers
    (e.g. the bootstrap-plan skill) can surface each one separately.
    """

    def __init__(self, message: str, *, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or []


@dataclass(frozen=True)
class Goal:
    """Normalized goal — what the composer reasons about.

    For races, ``distance`` carries the event distance ("ironman",
    "marathon"). For performance targets, ``distance`` is None and
    the composer derives a synthetic distance from ``metric`` via
    :data:`SUPPORTED_PERF_METRICS`.
    """

    id: str
    type: GoalType
    title: str | None = None
    target_date: date | None = None
    distance: str | None = None
    metric: str | None = None
    current: Any | None = None
    target: Any | None = None
    priority: str | None = None
    notes: str | None = None

    # The unprocessed yaml dict — kept so callers that need fields we
    # haven't normalized (constraints, location, expected_conditions)
    # can still reach them without a second yaml read.
    raw: dict[str, Any] | None = None

    @property
    def is_race(self) -> bool:
        return self.type == "race"


def _coerce_type(raw: Any) -> GoalType:
    if raw is None:
        # Heuristic: a yaml entry without a ``type`` is interpreted as
        # ``race`` if it has a ``date``+``distance``, else as
        # ``performance_target``. The :func:`from_match` caller has the
        # ``kind`` hint and uses it; this branch only fires when the
        # yaml is malformed, in which case we default to race for
        # back-compat with pre-schema entries.
        return "race"
    s = str(raw).strip().lower()
    if s in _TYPE_ALIASES:
        return _TYPE_ALIASES[s]
    if s in _GOAL_TYPES:
        return s  # type: ignore[return-value]
    raise GoalSchemaError(
        f"unknown goal type {raw!r}. Expected one of {sorted(_GOAL_TYPES)}.",
        violations=[f"goal_type:{raw!r}"],
    )


def _parse_date(raw: Any) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def from_match(match: athlete.GoalMatch) -> Goal:
    """Convert a :class:`tempo.athlete.GoalMatch` into a typed :class:`Goal`.

    Raises :class:`GoalSchemaError` if a non-race goal is missing required
    fields for its type (e.g. ``performance_target`` without ``metric``).
    """
    data = match.data
    gid = data.get("id")
    if not gid:
        raise GoalSchemaError(
            "goal entry missing required 'id' field.",
            violations=["missing_id"],
        )

    if match.kind == "race":
        target_d = _parse_date(data.get("date") or data.get("target_date"))
        return Goal(
            id=str(gid),
            type="race",
            title=data.get("title") or data.get("name"),
            target_date=target_d,
            distance=data.get("distance"),
            priority=data.get("priority"),
            notes=data.get("notes"),
            raw=data,
        )

    # Non-race goal — type defaults from the yaml shape:
    # - metric + target present  -> performance_target
    # - otherwise                -> maintenance (open-ended base)
    # An explicit type: in the yaml always wins.
    declared = data.get("type")
    if declared is None:
        if data.get("metric") and data.get("target") is not None:
            gtype: GoalType = "performance_target"
        else:
            gtype = "maintenance"
    else:
        gtype = _coerce_type(declared)

    target_d = _parse_date(data.get("by_date") or data.get("target_date"))

    if gtype == "performance_target":
        if not data.get("metric"):
            raise GoalSchemaError(
                f"goal {gid!r} type=performance_target requires 'metric'.",
                violations=["performance_target:missing_metric"],
            )
        if data.get("target") is None:
            raise GoalSchemaError(
                f"goal {gid!r} type=performance_target requires 'target'.",
                violations=["performance_target:missing_target"],
            )

    if gtype == "maintenance" and not data.get("metric"):
        # Maintenance goals technically can be metric-free ("hold whatever
        # I have"), but specifying a metric is more useful — surface a
        # warning later if Sean wants. Don't block here.
        pass

    return Goal(
        id=str(gid),
        type=gtype,
        title=data.get("title") or data.get("name"),
        target_date=target_d,
        distance=None,
        metric=data.get("metric"),
        current=data.get("current"),
        target=data.get("target"),
        notes=data.get("notes"),
        raw=data,
    )


def template_distance_for(goal: Goal) -> str:
    """Map a goal to the template ``distance`` string the composer indexes by.

    Races: pass through ``goal.distance``.
    Performance targets: look up the metric in :data:`SUPPORTED_PERF_METRICS`.
    Maintenance: ``base_building`` if dated, ``rolling_base_block`` otherwise.

    Raises :class:`GoalSchemaError` for unsupported metrics or types.
    """
    if goal.type == "race":
        if not goal.distance:
            raise GoalSchemaError(
                f"race goal {goal.id!r} missing 'distance'.",
                violations=["race:missing_distance"],
            )
        return goal.distance

    if goal.type == "performance_target":
        metric = goal.metric or ""
        synthetic = SUPPORTED_PERF_METRICS.get(metric)
        if synthetic is None:
            raise GoalSchemaError(
                f"unsupported metric {metric!r} for performance_target. "
                f"Supported: {sorted(SUPPORTED_PERF_METRICS)}.",
                violations=[f"unsupported_metric:{metric!r}"],
            )
        return synthetic

    if goal.type == "maintenance":
        return _BASE_BUILDING_DATED if goal.target_date else _BASE_BUILDING_OPEN

    raise GoalSchemaError(
        f"composer does not support goal type {goal.type!r} yet.",
        violations=[f"unsupported_goal_type:{goal.type}"],
    )


__all__ = [
    "Goal",
    "GoalSchemaError",
    "GoalType",
    "SUPPORTED_PERF_METRICS",
    "from_match",
    "template_distance_for",
]
