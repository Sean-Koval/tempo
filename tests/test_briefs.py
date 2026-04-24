"""Integration tests for tempo.briefs — the brief composers Phase 4 skills call."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempo import briefs


def _seed_repo(tmp_path: Path, *, race: bool = True) -> None:
    """Stand up a minimal athlete/ + knowledge/methodology/ tree."""
    (tmp_path / "athlete").mkdir()
    (tmp_path / "athlete" / "profile.yaml").write_text(
        "athlete:\n  name: Sean\n  weight_kg: 75\n"
        "thresholds:\n  ftp_w: 265\n  lthr_bpm: 168\n"
        "strengths: [aerobic]\nlimiters: [run durability]\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n_No active flags._\n\n## Resolved\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Hard constraints\n\n"
        "- Respect active injury flags.\n"
        "- Long-run progression max +10%/wk.\n",
        encoding="utf-8",
    )
    if race:
        (tmp_path / "athlete" / "race-calendar.yaml").write_text(
            "races:\n"
            "  - id: 2026-im-lake-placid\n"
            "    name: Ironman Lake Placid\n"
            "    date: 2099-12-01\n"  # far future so weeks_until stays positive regardless of today
            "    distance: ironman\n"
            "    priority: A\n"
            "    location: Lake Placid, NY\n",
            encoding="utf-8",
        )
        (tmp_path / "athlete" / "goals.yaml").write_text("goals: []\n", encoding="utf-8")
    else:
        (tmp_path / "athlete" / "race-calendar.yaml").write_text(
            "races: []\n", encoding="utf-8"
        )
        (tmp_path / "athlete" / "goals.yaml").write_text(
            "goals:\n"
            "  - id: 2099-open-base\n"
            "    title: Build year-round base\n",
            encoding="utf-8",
        )

    methodology = tmp_path / "knowledge" / "methodology"
    methodology.mkdir(parents=True)
    (methodology / "phases.yaml").write_text(
        "ironman_full_24wk:\n"
        "  total_weeks: 24\n"
        "  phases:\n"
        "    - id: base\n      weeks: 8\n"
        "rolling_base_block_12wk:\n"
        "  total_weeks: 12\n",
        encoding="utf-8",
    )


def _patch_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tempo.athlete.repo_root", lambda: tmp_path)
    monkeypatch.setattr("tempo.plans.repo_root", lambda: tmp_path)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))


def test_bootstrap_brief_race_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path, race=True)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2026-im-lake-placid")
    assert brief["goal"]["kind"] == "race"
    assert brief["goal"]["distance"] == "ironman"
    assert brief["goal"]["target_date"] == "2099-12-01"
    assert brief["applicable_phase_template"]["key"] == "ironman_full_24wk"
    assert brief["athlete_state"]["ftp_w"] == 265
    assert brief["athlete_state"]["limiters"] == ["run durability"]
    assert brief["active_injuries"] == []
    assert len(brief["hard_constraints"]) == 2
    assert brief["existing_plan"] is False
    # No load history seeded — recent_load should say so gracefully.
    assert brief["recent_load"]["samples_days"] == 0


def test_bootstrap_brief_non_race_rolling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=False)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2099-open-base")
    assert brief["goal"]["kind"] == "non_race"
    assert brief["goal"]["target_date"] is None
    assert brief["weeks_until_target"] is None
    # No target date → rolling template chosen.
    assert brief["applicable_phase_template"]["key"] == "rolling_base_block_12wk"


def test_bootstrap_brief_unknown_goal_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.UnknownGoalError) as excinfo:
        briefs.bootstrap_plan_brief("nonsense")
    assert "2026-im-lake-placid" in excinfo.value.known


def test_bootstrap_brief_active_injury_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)
    # Overwrite injury-log with an active flag.
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n"
        "### 2026-04-15 — calf strain — 3\n"
        "- Status: active\n- Constraints: no >Z3 run\n\n"
        "## Resolved\n",
        encoding="utf-8",
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2026-im-lake-placid")
    assert len(brief["active_injuries"]) == 1
    assert "calf strain" in brief["active_injuries"][0]


def test_bootstrap_brief_existing_plan_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)
    plan_dir = tmp_path / "plans" / "2026-im-lake-placid"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text("plan_id: 2026-im-lake-placid\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2026-im-lake-placid")
    assert brief["existing_plan"] is True


# --- plan_week_brief --------------------------------------------------------

_PLAN_WITH_PHASES = """\
plan_id: 2026-im-lake-placid
goal_ref: 2026-im-lake-placid
template: ironman_full_24wk
start_date: 2026-03-02
target_date: 2026-07-26
total_weeks: 24
phases:
  - id: base
    start_week: 2026-W10
    weeks: 4
    weekly_tss_target: [350, 450]
    intensity_distribution: { z1_z2: 85, z3: 10, z4_plus: 5 }
    key_sessions: [long_ride_z2, long_run_z2]
  - id: build
    start_week: 2026-W14
    weeks: 6
    weekly_tss_target: [600, 750]
    intensity_distribution: { z1_z2: 75, z3: 15, z4_plus: 10 }
    key_sessions: [race_pace_bike, threshold_run]
"""


def _seed_week_plan(tmp_path: Path) -> None:
    """Seed athlete/ + a single plan with phases for plan_week_brief tests."""
    _seed_repo(tmp_path, race=True)
    plan_dir = tmp_path / "plans" / "2026-im-lake-placid"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text(_PLAN_WITH_PHASES, encoding="utf-8")


def test_plan_week_brief_resolves_single_plan_and_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W15")
    assert brief["week_id"] == "2026-W15"
    assert brief["week_start"] == "2026-04-06"
    assert brief["week_end"] == "2026-04-12"
    assert brief["plan"]["plan_id"] == "2026-im-lake-placid"
    assert brief["plan"]["phase"]["id"] == "build"
    assert brief["plan"]["week_of_phase"] == 2
    assert brief["plan"]["weeks_remaining_in_phase"] == 4
    assert brief["plan"]["weekly_tss_target_mid"] == 675


def test_plan_week_brief_default_week_is_one_week_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    from datetime import date, timedelta

    from tempo.plans import week_id_for

    expected = week_id_for(date.today() + timedelta(days=7))
    brief = briefs.plan_week_brief()
    assert brief["week_id"] == expected


def test_plan_week_brief_no_plan_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)  # no plans/ dir seeded
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.NoActivePlanError):
        briefs.plan_week_brief(week_id="2026-W15")


def test_plan_week_brief_multiple_plans_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    # Second plan under plans/
    other = tmp_path / "plans" / "2027-other"
    other.mkdir(parents=True)
    (other / "plan.yaml").write_text("plan_id: 2027-other\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    from tempo.plans import MultiplePlansError

    with pytest.raises(MultiplePlansError):
        briefs.plan_week_brief(week_id="2026-W15")


def test_plan_week_brief_explicit_plan_id_bypasses_autodetect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    # Ambiguous plans/ on purpose — explicit plan_id resolves it.
    other = tmp_path / "plans" / "2027-other"
    other.mkdir(parents=True)
    (other / "plan.yaml").write_text("plan_id: 2027-other\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(
        week_id="2026-W15", plan_id="2026-im-lake-placid"
    )
    assert brief["plan"]["plan_id"] == "2026-im-lake-placid"


def test_plan_week_brief_week_outside_any_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W05")  # before base starts
    assert brief["plan"]["phase"] is None
    assert brief["plan"]["week_of_phase"] is None
    assert brief["plan"]["weekly_tss_target_mid"] is None


def test_plan_week_brief_flags_existing_week_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    weeks = tmp_path / "plans" / "2026-im-lake-placid" / "weeks"
    weeks.mkdir(parents=True)
    (weeks / "2026-W15.md").write_text("# drafted\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W15")
    assert brief["week_already_drafted"] is True


def test_plan_week_brief_surfaces_active_injuries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n"
        "### 2026-04-15 — calf strain — 3\n"
        "- Status: active\n- Constraints: no >Z3 run\n\n"
        "## Resolved\n",
        encoding="utf-8",
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W15")
    assert len(brief["active_injuries"]) == 1
    assert "calf strain" in brief["active_injuries"][0]
