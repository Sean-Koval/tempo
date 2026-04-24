"""Tests for tempo.plans — phase-template lookup and plan.yaml reads."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tempo import plans

_PHASES = """
ironman_full_24wk:
  total_weeks: 24
  phases:
    - id: base
      weeks: 8
ironman_half_16wk:
  total_weeks: 16
  phases:
    - id: base
      weeks: 6
rolling_base_block_12wk:
  total_weeks: 12
"""


def _seed_phases(tmp_path: Path) -> None:
    d = tmp_path / "knowledge" / "methodology"
    d.mkdir(parents=True, exist_ok=True)
    (d / "phases.yaml").write_text(_PHASES, encoding="utf-8")


def test_load_phase_templates_returns_all(tmp_path: Path) -> None:
    _seed_phases(tmp_path)
    got = plans.load_phase_templates(root=tmp_path)
    assert set(got.keys()) == {
        "ironman_full_24wk",
        "ironman_half_16wk",
        "rolling_base_block_12wk",
    }


def test_phase_template_for_ironman(tmp_path: Path) -> None:
    _seed_phases(tmp_path)
    result = plans.phase_template_for(
        distance="ironman", has_target_date=True, root=tmp_path
    )
    assert result is not None
    key, doc = result
    assert key == "ironman_full_24wk"
    assert doc["total_weeks"] == 24


def test_phase_template_for_half_ironman(tmp_path: Path) -> None:
    _seed_phases(tmp_path)
    result = plans.phase_template_for(
        distance="half_ironman", has_target_date=True, root=tmp_path
    )
    assert result is not None
    assert result[0] == "ironman_half_16wk"


def test_phase_template_for_no_target_date_rolling(tmp_path: Path) -> None:
    _seed_phases(tmp_path)
    result = plans.phase_template_for(has_target_date=False, root=tmp_path)
    assert result is not None
    assert result[0] == "rolling_base_block_12wk"


def test_phase_template_for_unknown_distance_returns_none(tmp_path: Path) -> None:
    _seed_phases(tmp_path)
    result = plans.phase_template_for(
        distance="marathon", has_target_date=True, root=tmp_path
    )
    assert result is None


def test_phase_template_for_missing_file(tmp_path: Path) -> None:
    # No knowledge/methodology/phases.yaml at all.
    result = plans.phase_template_for(
        distance="ironman", has_target_date=True, root=tmp_path
    )
    assert result is None


def test_read_plan_yaml_missing_returns_none(tmp_path: Path) -> None:
    assert plans.read_plan_yaml("no-such-plan", root=tmp_path) is None


def test_read_plan_yaml_parses(tmp_path: Path) -> None:
    d = tmp_path / "plans" / "2026-foo"
    d.mkdir(parents=True)
    (d / "plan.yaml").write_text("plan_id: 2026-foo\ntotal_weeks: 12\n", encoding="utf-8")
    got = plans.read_plan_yaml("2026-foo", root=tmp_path)
    assert got is not None
    assert got["plan_id"] == "2026-foo"
    assert got["total_weeks"] == 12


# --- Week id math -----------------------------------------------------------

def test_week_id_for_monday() -> None:
    # 2026-04-27 is the Monday of ISO week 2026-W18.
    assert plans.week_id_for(date(2026, 4, 27)) == "2026-W18"


def test_week_start_and_end_are_monday_sunday() -> None:
    assert plans.week_start("2026-W18") == date(2026, 4, 27)
    assert plans.week_end("2026-W18") == date(2026, 5, 3)


def test_shift_week_forward_and_back() -> None:
    assert plans.shift_week("2026-W18", weeks=1) == "2026-W19"
    assert plans.shift_week("2026-W18", weeks=-2) == "2026-W16"


# --- find_single_plan -------------------------------------------------------

def _seed_plan(tmp_path: Path, plan_id: str, body: str = "plan_id: x\n") -> Path:
    d = tmp_path / "plans" / plan_id
    d.mkdir(parents=True)
    (d / "plan.yaml").write_text(body, encoding="utf-8")
    return d


def test_find_single_plan_none_when_no_plans_dir(tmp_path: Path) -> None:
    assert plans.find_single_plan(root=tmp_path) is None


def test_find_single_plan_returns_only_plan(tmp_path: Path) -> None:
    _seed_plan(tmp_path, "2026-foo", "plan_id: 2026-foo\n")
    got = plans.find_single_plan(root=tmp_path)
    assert got is not None
    pid, doc = got
    assert pid == "2026-foo"
    assert doc["plan_id"] == "2026-foo"


def test_find_single_plan_raises_on_multiple(tmp_path: Path) -> None:
    _seed_plan(tmp_path, "2026-foo")
    _seed_plan(tmp_path, "2026-bar")
    with pytest.raises(plans.MultiplePlansError):
        plans.find_single_plan(root=tmp_path)


# --- phase_for_week ---------------------------------------------------------

_PLAN_DOC = {
    "plan_id": "2026-test",
    "phases": [
        {"id": "base", "start_week": "2026-W10", "weeks": 4},
        {"id": "build", "start_week": "2026-W14", "weeks": 6},
        {"id": "peak", "start_week": "2026-W20", "weeks": 2},
    ],
}


def test_phase_for_week_inside_phase() -> None:
    phase = plans.phase_for_week(_PLAN_DOC, "2026-W12")
    assert phase is not None and phase["id"] == "base"


def test_phase_for_week_boundary_is_inclusive_start_exclusive_end() -> None:
    assert plans.phase_for_week(_PLAN_DOC, "2026-W10")["id"] == "base"
    assert plans.phase_for_week(_PLAN_DOC, "2026-W13")["id"] == "base"
    # W14 falls in build, not base (base covers W10..W13)
    assert plans.phase_for_week(_PLAN_DOC, "2026-W14")["id"] == "build"


def test_phase_for_week_outside_any_phase() -> None:
    assert plans.phase_for_week(_PLAN_DOC, "2026-W05") is None
    assert plans.phase_for_week(_PLAN_DOC, "2026-W30") is None


def test_phase_for_week_empty_plan() -> None:
    assert plans.phase_for_week({}, "2026-W10") is None


def test_week_index_in_phase() -> None:
    base = _PLAN_DOC["phases"][0]
    assert plans.week_index_in_phase(base, "2026-W10") == 1
    assert plans.week_index_in_phase(base, "2026-W12") == 3
    # Earlier than start → None
    assert plans.week_index_in_phase(base, "2026-W09") is None
