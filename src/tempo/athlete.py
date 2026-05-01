"""Read helpers for ``athlete/*`` — the who-is-Sean-right-now layer.

These feed every Phase 4 preflight. Single source: both bootstrap-plan and
plan-training-week read profile/goals/races/injuries through here so they
don't drift.

All helpers accept an optional ``root`` to support test isolation. In normal
use, ``root`` defaults to the repo root; tests pass a tmp path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .paths import repo_root

RaceStatus = Literal["confirmed", "tentative", "cancelled"]
RacePriority = Literal["A", "B", "C"]
_VALID_STATUSES: frozenset[str] = frozenset({"confirmed", "tentative", "cancelled"})
_VALID_PRIORITIES: frozenset[str] = frozenset({"A", "B", "C"})


class RaceCalendarError(ValueError):
    """Raised when a race-calendar.yaml entry is structurally invalid.

    The status / priority / cancelled_reason invariants are HARD: the composer
    can't reason correctly if a cancelled race lacks a reason or a status
    is misspelled. Surface the offending race id and field in the message.
    """

_ACTIVE_HEADING = re.compile(r"^##\s+active\b", re.IGNORECASE | re.MULTILINE)
_HARD_HEADING = re.compile(r"^##\s+hard constraints\b", re.IGNORECASE | re.MULTILINE)
_INJURY_ENTRY = re.compile(r"^### ", re.MULTILINE)


@dataclass
class GoalMatch:
    """Either a race (from race-calendar.yaml) or a non-race goal (from goals.yaml)."""
    kind: str  # "race" | "non_race"
    data: dict[str, Any]


def athlete_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "athlete"


def load_profile(root: Path | None = None) -> dict[str, Any]:
    """Return profile.yaml contents, or {} if missing."""
    path = athlete_dir(root) / "profile.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_goals(root: Path | None = None) -> list[dict[str, Any]]:
    """Return the goals list from athlete/goals.yaml (may be empty)."""
    path = athlete_dir(root) / "goals.yaml"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("goals") or []


def load_races(root: Path | None = None) -> list[dict[str, Any]]:
    """Return the races list from athlete/race-calendar.yaml (may be empty).

    Each race dict is normalized: ``status`` defaults to ``"confirmed"`` when
    absent (back-compat for entries authored before tempo-wk7 added the
    field), and the schema is validated. Cancelled races without a
    ``cancelled_reason`` raise :class:`RaceCalendarError` — the composer
    silently skipping a race for an unstated reason is worse than failing
    loud.
    """
    path = athlete_dir(root) / "race-calendar.yaml"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    raw = doc.get("races") or []
    return [_normalize_race(r) for r in raw]


def _normalize_race(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply schema defaults + validate. Returns a shallow-copied dict."""
    if not isinstance(raw, dict):
        raise RaceCalendarError(f"race entry is not a mapping: {raw!r}")
    out = dict(raw)
    rid = out.get("id") or "<unnamed>"

    status = out.get("status")
    if status is None:
        status = "confirmed"
    elif status not in _VALID_STATUSES:
        raise RaceCalendarError(
            f"race {rid!r}: status={status!r} not in {sorted(_VALID_STATUSES)}"
        )
    out["status"] = status

    priority = out.get("priority")
    if priority is not None and priority not in _VALID_PRIORITIES:
        raise RaceCalendarError(
            f"race {rid!r}: priority={priority!r} not in {sorted(_VALID_PRIORITIES)}"
        )

    if status == "cancelled" and not out.get("cancelled_reason"):
        raise RaceCalendarError(
            f"race {rid!r}: status=cancelled requires cancelled_reason"
        )
    return out


def selectable_races(root: Path | None = None) -> list[dict[str, Any]]:
    """Races eligible to anchor a plan — i.e. anything not cancelled.

    Tentative races are included: they may still happen, and the agent may
    choose to plan against them with eyes open. Cancelled ones are filtered
    out so /bootstrap-plan and amend.switch_target never select them.
    """
    return [r for r in load_races(root) if r.get("status") != "cancelled"]


def find_goal(goal_id: str, *, root: Path | None = None) -> GoalMatch | None:
    """Look up a goal_id first in races, then in non-race goals.

    Returns the match with its kind, or None if not found. Race IDs and goal
    IDs share a namespace — collisions are the athlete's problem; races win
    by convention (they're the more common case).
    """
    for race in load_races(root):
        if race.get("id") == goal_id:
            return GoalMatch(kind="race", data=race)
    for goal in load_goals(root):
        if goal.get("id") == goal_id:
            return GoalMatch(kind="non_race", data=goal)
    return None


def all_goal_ids(root: Path | None = None) -> list[str]:
    """Every declared goal_id across races and goals — for error messages."""
    ids: list[str] = []
    for race in load_races(root):
        gid = race.get("id")
        if gid:
            ids.append(gid)
    for goal in load_goals(root):
        gid = goal.get("id")
        if gid:
            ids.append(gid)
    return ids


def active_injury_flags(root: Path | None = None) -> list[str]:
    """Return ``### ...`` injury headings from the Active section of injury-log.md.

    Empty list if the file's missing, has no Active section, or that section
    contains no ``###`` entries (i.e., 'no active flags' placeholder).
    """
    path = athlete_dir(root) / "injury-log.md"
    if not path.is_file():
        return []

    text = path.read_text(encoding="utf-8")
    m = _ACTIVE_HEADING.search(text)
    if not m:
        return []

    tail = text[m.end():]
    next_h = re.search(r"^##\s+", tail, re.MULTILINE)
    section = tail[: next_h.start()] if next_h else tail
    section = re.sub(r"<!--.*?-->", "", section, flags=re.DOTALL)

    return [ln[4:].strip() for ln in section.splitlines() if ln.startswith("### ")]


def hard_constraints(root: Path | None = None) -> list[str]:
    """Return bullet items under the 'Hard constraints' section of preferences.md.

    Each item is the bullet text with leading '- ' stripped. Empty list if the
    file or section is missing.
    """
    path = athlete_dir(root) / "preferences.md"
    if not path.is_file():
        return []

    text = path.read_text(encoding="utf-8")
    m = _HARD_HEADING.search(text)
    if not m:
        return []

    tail = text[m.end():]
    next_h = re.search(r"^##\s+", tail, re.MULTILINE)
    section = tail[: next_h.start()] if next_h else tail

    return [
        ln.lstrip("- ").strip()
        for ln in section.splitlines()
        if ln.startswith("- ") and ln.strip() != "-"
    ]


__all__ = [
    "GoalMatch",
    "RaceCalendarError",
    "RacePriority",
    "RaceStatus",
    "active_injury_flags",
    "all_goal_ids",
    "athlete_dir",
    "find_goal",
    "hard_constraints",
    "load_goals",
    "load_profile",
    "load_races",
    "selectable_races",
]
