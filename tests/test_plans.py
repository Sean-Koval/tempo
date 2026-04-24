"""Tests for tempo.plans — phase-template lookup and plan.yaml reads."""

from __future__ import annotations

from pathlib import Path

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
