"""Read helpers for ``plans/`` and ``knowledge/methodology/``.

Thin — just enough for Phase 4 preflights to locate the phase template for a
goal and read an existing plan.yaml. Writing plans is the agent's job (via
Write/Edit tools on the markdown/YAML files directly); this module is read-only.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .paths import repo_root


class MultiplePlansError(RuntimeError):
    """Raised when auto-detection is used but more than one plan exists."""

    def __init__(self, plan_ids: list[str]) -> None:
        self.plan_ids = plan_ids
        super().__init__(
            f"multiple plans under plans/ — pass --plan-id explicitly. Found: {plan_ids}"
        )


class NoPlanFoundError(RuntimeError):
    """Raised when no plan.yaml exists and no plan_id was supplied."""


def week_id_for(d: date) -> str:
    """ISO week id ``YYYY-Www`` for a given date."""
    iso = d.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def parse_week_id(week_id: str) -> tuple[int, int]:
    year_s, week_s = week_id.split("-W")
    return int(year_s), int(week_s)


def week_start(week_id: str) -> date:
    """Monday of the given ISO week."""
    year, week = parse_week_id(week_id)
    return date.fromisocalendar(year, week, 1)


def week_end(week_id: str) -> date:
    """Sunday of the given ISO week."""
    year, week = parse_week_id(week_id)
    return date.fromisocalendar(year, week, 7)


def shift_week(week_id: str, *, weeks: int) -> str:
    """Return the ISO week id ``weeks`` away from ``week_id`` (may be negative)."""
    return week_id_for(week_start(week_id) + timedelta(weeks=weeks))


def plans_root(root: Path | None = None) -> Path:
    return (root or repo_root()) / "plans"


def methodology_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "knowledge" / "methodology"


def load_phase_templates(root: Path | None = None) -> dict[str, Any]:
    """Return the full phases.yaml doc — all templates keyed by id."""
    path = methodology_dir(root) / "phases.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def phase_template_for(
    *,
    distance: str | None = None,
    has_target_date: bool = False,
    root: Path | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Pick the right phases.yaml template for a goal.

    Heuristic:
      - distance == 'ironman'      → ironman_full_24wk
      - distance == 'half_ironman' → ironman_half_16wk
      - no target date             → rolling_base_block_12wk
      - otherwise                  → None (agent decides)

    Returns (template_key, template_doc) or None. Lets the skill fall back to
    agent judgment when the heuristic doesn't match.
    """
    templates = load_phase_templates(root)
    if not templates:
        return None

    key: str | None = None
    if distance == "ironman":
        key = "ironman_full_24wk"
    elif distance == "half_ironman":
        key = "ironman_half_16wk"
    elif not has_target_date:
        key = "rolling_base_block_12wk"

    if key and key in templates:
        return key, templates[key]
    return None


def read_plan_yaml(plan_id: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """Return plans/<plan_id>/plan.yaml as a dict, or None if it doesn't exist."""
    path = plans_root(root) / plan_id / "plan.yaml"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def plan_dir(plan_id: str, *, root: Path | None = None) -> Path:
    """Return plans/<plan_id>/ — doesn't create the directory."""
    return plans_root(root) / plan_id


def week_file(plan_id: str, week_id: str, *, root: Path | None = None) -> Path:
    """Return plans/<plan_id>/weeks/<week_id>.md — doesn't create anything."""
    return plan_dir(plan_id, root=root) / "weeks" / f"{week_id}.md"


def find_single_plan(
    *, root: Path | None = None
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(plan_id, plan.yaml doc)`` if exactly one plan exists.

    Returns ``None`` if no plan is present. Raises :class:`MultiplePlansError`
    if more than one plan.yaml is found — the caller should pass a plan_id
    explicitly.
    """
    root_dir = plans_root(root)
    if not root_dir.is_dir():
        return None
    candidates = sorted(
        d for d in root_dir.iterdir() if d.is_dir() and (d / "plan.yaml").is_file()
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        raise MultiplePlansError([c.name for c in candidates])
    pdir = candidates[0]
    return pdir.name, read_plan_yaml(pdir.name, root=root) or {}


def phase_for_week(plan: dict[str, Any], week_id: str) -> dict[str, Any] | None:
    """Return the phase dict from ``plan.yaml`` that contains ``week_id``.

    Matches by Monday-date inclusion: phase spans ``[start_week, start_week +
    weeks)``. Returns ``None`` if no phase covers the week.
    """
    try:
        target_monday = week_start(week_id)
    except (ValueError, AttributeError):
        return None
    for phase in plan.get("phases") or []:
        sw = phase.get("start_week")
        weeks = phase.get("weeks")
        if not sw or not weeks:
            continue
        try:
            start = week_start(sw)
        except (ValueError, AttributeError):
            continue
        end = start + timedelta(weeks=int(weeks))
        if start <= target_monday < end:
            return phase
    return None


def week_index_in_phase(phase: dict[str, Any], week_id: str) -> int | None:
    """Return ``week_id``'s 1-indexed position within ``phase``, or ``None``."""
    sw = phase.get("start_week")
    if not sw:
        return None
    try:
        delta = (week_start(week_id) - week_start(sw)).days
    except (ValueError, AttributeError):
        return None
    if delta < 0:
        return None
    return delta // 7 + 1


__all__ = [
    "MultiplePlansError",
    "NoPlanFoundError",
    "find_single_plan",
    "load_phase_templates",
    "methodology_dir",
    "parse_week_id",
    "phase_for_week",
    "phase_template_for",
    "plan_dir",
    "plans_root",
    "read_plan_yaml",
    "shift_week",
    "week_end",
    "week_file",
    "week_id_for",
    "week_index_in_phase",
    "week_start",
]
