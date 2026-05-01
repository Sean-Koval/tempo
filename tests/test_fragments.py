"""Tests for ``tempo.fragments`` — plan-fragment loader + validator.

Fragments are the goal-research skill's structured output. The schema
gates on lifecycle (re_evaluate_after > created_at), shape (training XOR
nutrition), and archetype existence — these tests pin every gate so the
loader can't silently accept a malformed fragment that would later
mislead the composer.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from tempo import fragments


def _write_fragment(path: Path, body: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


def _training_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "fragment_id": "stronger-legs",
        "goal": "build stronger legs for cycling, 2 lifts/week",
        "kind": "training",
        "created_at": "2026-04-30",
        "re_evaluate_after": "2026-06-25",
        "duration_weeks": 8,
        "sessions": [
            {
                "archetype": "strength_intensification_block",
                "cadence_per_week": 2,
                "slot_preference": ["tuesday", "friday"],
                "target_tss": 22,
            }
        ],
        "research_refs": ["knowledge/research/2026/04/strength-for-cycling.md"],
        "rationale": "Hypertrophy block done; move to intensification.",
    }
    body.update(overrides)
    return body


def _nutrition_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "fragment_id": "race-week-carb-load",
        "goal": "race-week carb-load for 70.3",
        "kind": "nutrition",
        "created_at": "2027-04-19",
        "re_evaluate_after": "2027-05-02",
        "duration_weeks": 2,
        "nutrition_windows": [
            {
                "label": "carb-load T-3..T-1",
                "schedule": "2027-04-28..2027-04-30",
                "macros": {"carb_g_per_kg_per_day": 10},
            }
        ],
    }
    body.update(overrides)
    return body


# --- Schema parsing --------------------------------------------------------


def test_load_training_fragment_round_trips(tmp_path: Path) -> None:
    p = _write_fragment(tmp_path / "f.yaml", _training_body())
    frag = fragments.load_fragment(p)
    assert frag.fragment_id == "stronger-legs"
    assert frag.kind == "training"
    assert frag.created_at == date(2026, 4, 30)
    assert frag.re_evaluate_after == date(2026, 6, 25)
    assert frag.duration_weeks == 8
    assert len(frag.sessions) == 1
    assert frag.sessions[0].archetype == "strength_intensification_block"
    assert frag.sessions[0].cadence_per_week == 2
    assert frag.sessions[0].slot_preference == ("tuesday", "friday")
    assert frag.sessions[0].target_tss == 22.0


def test_load_nutrition_fragment_round_trips(tmp_path: Path) -> None:
    p = _write_fragment(tmp_path / "f.yaml", _nutrition_body())
    frag = fragments.load_fragment(p)
    assert frag.kind == "nutrition"
    assert len(frag.nutrition_windows) == 1
    assert frag.nutrition_windows[0].label == "carb-load T-3..T-1"


# --- Schema gates ----------------------------------------------------------


def test_training_fragment_with_no_sessions_rejected(tmp_path: Path) -> None:
    body = _training_body(sessions=[])
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_fragment(p)
    assert any("at least one session" in v for v in exc.value.violations)


def test_nutrition_fragment_with_no_windows_rejected(tmp_path: Path) -> None:
    body = _nutrition_body(nutrition_windows=[])
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_fragment(p)
    assert any("nutrition_window" in v for v in exc.value.violations)


def test_training_fragment_with_nutrition_windows_rejected(tmp_path: Path) -> None:
    """Splitting forces clean R-20 accounting (nutrition contributes 0 TSS)."""
    body = _training_body(
        nutrition_windows=[{"label": "x", "schedule": "daily", "macros": {}}]
    )
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_fragment(p)
    assert any("must not declare nutrition_windows" in v for v in exc.value.violations)


def test_nutrition_fragment_with_sessions_rejected(tmp_path: Path) -> None:
    body = _nutrition_body(
        sessions=[{"archetype": "x", "cadence_per_week": 1}]
    )
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_fragment(p)
    assert any("must not declare sessions" in v for v in exc.value.violations)


def test_re_evaluate_after_must_be_after_created_at(tmp_path: Path) -> None:
    body = _training_body(created_at="2026-04-30", re_evaluate_after="2026-04-30")
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_fragment(p)
    assert any("must be after" in v for v in exc.value.violations)


def test_invalid_kind_rejected(tmp_path: Path) -> None:
    body = _training_body(kind="mixed")
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError):
        fragments.load_fragment(p)


def test_session_cadence_must_be_positive_int(tmp_path: Path) -> None:
    body = _training_body(
        sessions=[{"archetype": "strength_intensification_block", "cadence_per_week": 0}]
    )
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_fragment(p)
    assert any("cadence_per_week" in v for v in exc.value.violations)


def test_missing_required_field_collects_all_violations(tmp_path: Path) -> None:
    """One run, all violations — Sean shouldn't have to fix one error at a time."""
    body = {
        "kind": "training",
        "sessions": [{"archetype": "x", "cadence_per_week": 1}],
    }
    p = _write_fragment(tmp_path / "f.yaml", body)
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_fragment(p)
    # at minimum: fragment_id, goal, created_at, re_evaluate_after, duration_weeks
    assert len(exc.value.violations) >= 4


# --- Lifecycle (is_active) -------------------------------------------------


def test_fragment_active_in_window(tmp_path: Path) -> None:
    p = _write_fragment(tmp_path / "f.yaml", _training_body())
    frag = fragments.load_fragment(p)
    assert frag.is_active(date(2026, 5, 15))


def test_fragment_inactive_after_re_evaluate_after(tmp_path: Path) -> None:
    p = _write_fragment(tmp_path / "f.yaml", _training_body())
    frag = fragments.load_fragment(p)
    assert not frag.is_active(date(2026, 6, 26))


def test_fragment_inactive_before_created(tmp_path: Path) -> None:
    p = _write_fragment(tmp_path / "f.yaml", _training_body())
    frag = fragments.load_fragment(p)
    assert not frag.is_active(date(2026, 4, 29))


def test_fragment_active_on_re_evaluate_date_inclusive(tmp_path: Path) -> None:
    """The day Sean is supposed to re-run /goal-research is still active."""
    p = _write_fragment(tmp_path / "f.yaml", _training_body())
    frag = fragments.load_fragment(p)
    assert frag.is_active(date(2026, 6, 25))


# --- estimated_weekly_tss --------------------------------------------------


def test_estimated_weekly_tss_sums_cadence_times_per_session(tmp_path: Path) -> None:
    body = _training_body(
        sessions=[
            {"archetype": "strength_intensification_block", "cadence_per_week": 2, "target_tss": 22},
            {"archetype": "strength_realization_block", "cadence_per_week": 1, "target_tss": 12},
        ]
    )
    p = _write_fragment(tmp_path / "f.yaml", body)
    frag = fragments.load_fragment(p)
    assert frag.estimated_weekly_tss() == pytest.approx(2 * 22 + 1 * 12)


def test_estimated_weekly_tss_skips_sessions_without_tss(tmp_path: Path) -> None:
    body = _training_body(
        sessions=[
            {"archetype": "strength_intensification_block", "cadence_per_week": 2, "target_tss": 22},
            {"archetype": "strength_realization_block", "cadence_per_week": 1},
        ]
    )
    p = _write_fragment(tmp_path / "f.yaml", body)
    frag = fragments.load_fragment(p)
    assert frag.estimated_weekly_tss() == pytest.approx(44)


def test_nutrition_fragment_has_zero_estimated_weekly_tss(tmp_path: Path) -> None:
    p = _write_fragment(tmp_path / "f.yaml", _nutrition_body())
    frag = fragments.load_fragment(p)
    assert frag.estimated_weekly_tss() == 0.0


# --- Archetype existence (live check against session-library) ---------------


def test_load_active_fragments_rejects_unknown_archetype(tmp_path: Path) -> None:
    """An unknown archetype is a fragment-level error — the composer would
    silently skip it later, much harder to debug than a fail-closed load."""
    plan_dir = tmp_path / "plans" / "test-plan" / "fragments"
    body = _training_body(
        sessions=[{"archetype": "totally_made_up_session", "cadence_per_week": 1}]
    )
    _write_fragment(plan_dir / "stronger-legs.yaml", body)
    # Use the real repo root so the session-library is the live one.
    with pytest.raises(fragments.FragmentSchemaError) as exc:
        fragments.load_active_fragments("test-plan", root=tmp_path)
    assert any("totally_made_up_session" in v for v in exc.value.violations)


def test_load_active_fragments_filters_inactive(tmp_path: Path) -> None:
    """A fragment past re_evaluate_after is silently dropped — that's the
    whole point of the lifecycle. archetype_check off so the test doesn't
    depend on session-library state."""
    plan_dir = tmp_path / "plans" / "test-plan" / "fragments"
    expired = _training_body(
        fragment_id="expired",
        created_at="2025-01-01",
        re_evaluate_after="2025-03-01",
    )
    active = _training_body(
        fragment_id="active",
        created_at="2026-04-01",
        re_evaluate_after="2026-12-01",
    )
    _write_fragment(plan_dir / "expired.yaml", expired)
    _write_fragment(plan_dir / "active.yaml", active)
    out = fragments.load_active_fragments(
        "test-plan",
        on=date(2026, 5, 1),
        root=tmp_path,
        archetype_check=False,
    )
    ids = [f.fragment_id for f in out]
    assert ids == ["active"]


def test_load_active_fragments_returns_empty_when_no_dir(tmp_path: Path) -> None:
    out = fragments.load_active_fragments("nonexistent-plan", root=tmp_path)
    assert out == []


def test_known_archetypes_picks_up_real_session_library() -> None:
    """Sanity check: the live session-library has the strength archetypes
    referenced by goal-research's example fragment."""
    known = fragments.known_archetypes()
    expected = {
        "strength_foundation",
        "strength_maintenance",
        "strength_hypertrophy_block",
        "strength_intensification_block",
        "strength_realization_block",
    }
    missing = expected - known
    assert not missing, f"session library missing strength archetypes: {missing}"
