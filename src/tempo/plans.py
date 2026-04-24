"""Read helpers for ``plans/`` and ``knowledge/methodology/``.

Thin — just enough for Phase 4 preflights to locate the phase template for a
goal and read an existing plan.yaml. Writing plans is the agent's job (via
Write/Edit tools on the markdown/YAML files directly); this module is read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .paths import repo_root


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


__all__ = [
    "load_phase_templates",
    "methodology_dir",
    "phase_template_for",
    "plan_dir",
    "plans_root",
    "read_plan_yaml",
]
