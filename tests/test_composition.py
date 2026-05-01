"""Tests for ``tempo.composition`` — the multi-sport phase-chain composer.

Covers run-only, bike-only, swim-only, and multisport templates plus the
injury-driven preblock case (BSI g2 active → rehab + return prepended).
"""

from __future__ import annotations

import pytest

from tempo import composition

# --- Library loads cleanly ------------------------------------------------


def test_phase_library_loads_with_known_phases() -> None:
    library = composition.load_phase_library()
    expected = {
        "prep_run", "base_aerobic_run", "build_run", "peak_run", "taper_run",
        "prep_bike", "base_aerobic_bike", "build_bike", "peak_bike", "taper_bike",
        "base_aerobic_swim", "build_swim", "peak_swim", "taper_swim",
        "base_aerobic_multisport", "build_multisport", "peak_multisport",
        "taper_multisport", "rehab_bike_only", "return_to_run", "return_to_3sport",
    }
    missing = expected - set(library.keys())
    assert not missing, f"phase_library missing: {missing}"


def test_composition_rules_load() -> None:
    rules = composition.load_composition_rules()
    rule_ids = {r.id for r in rules}
    assert "chain_must_end_taper_for_a_race" in rule_ids
    assert "build_requires_base_total_6wk" in rule_ids
    assert "rehab_requires_active_injury" in rule_ids


def test_named_templates_present() -> None:
    templates = composition.load_named_templates()
    expected = {
        "marathon_18wk", "half_marathon_12wk", "five_k_8wk",
        "gran_fondo_12wk", "road_race_10wk",
        "masters_swim_meet_8wk",
        "triathlon_olympic_12wk", "triathlon_half_16wk", "triathlon_full_24wk",
    }
    missing = expected - set(templates.keys())
    assert not missing, f"templates missing: {missing}"


# --- Run-only composition -------------------------------------------------


def test_compose_marathon_chain_18wk() -> None:
    chain = composition.compose_chain(
        distance="marathon",
        runway_weeks=18,
        has_target_date=True,
    )
    ids = [p.id for p in chain.phases]
    assert ids[0].startswith("prep") or ids[0].startswith("base")
    assert ids[-1] == "taper_run"
    assert chain.total_weeks == 18
    # All phases should be run-focused
    for p in chain.phases:
        assert p.sport_focus.get("run", 0) >= 0.4, (
            f"phase {p.id!r} sport_focus={p.sport_focus} not run-dominant"
        )


def test_compose_5k_chain_8wk() -> None:
    chain = composition.compose_chain(distance="5k", runway_weeks=8)
    assert chain.template_id == "five_k_8wk"
    assert chain.phases[-1].id == "taper_run"
    assert chain.total_weeks == 8


def test_compose_marathon_runway_overshoot_extends_base() -> None:
    """22wk runway > 18wk template → extend base, keep peak/taper intact."""
    chain = composition.compose_chain(distance="marathon", runway_weeks=22)
    assert chain.total_weeks == 22
    assert chain.phases[-1].id == "taper_run"
    assert chain.phases[-2].id == "peak_run"


# --- Bike-only composition ------------------------------------------------


def test_compose_gran_fondo_chain_12wk() -> None:
    chain = composition.compose_chain(distance="gran_fondo", runway_weeks=12)
    assert chain.template_id == "gran_fondo_12wk"
    assert chain.phases[-1].id == "taper_bike"
    assert chain.total_weeks == 12
    for p in chain.phases:
        assert p.sport_focus.get("bike", 0) >= 0.5


# --- Swim-only composition ------------------------------------------------


def test_compose_masters_swim_meet_chain_8wk() -> None:
    chain = composition.compose_chain(distance="masters_swim_meet", runway_weeks=8)
    assert chain.phases[-1].id == "taper_swim"
    assert chain.total_weeks == 8


# --- Multisport (the canonical regression case) --------------------------


def test_compose_70_3_chain_16wk() -> None:
    chain = composition.compose_chain(distance="half_ironman", runway_weeks=16)
    assert chain.template_id == "triathlon_half_16wk"
    assert chain.phases[-1].id == "taper_multisport"
    assert chain.total_weeks == 16


def test_compose_full_ironman_24wk() -> None:
    chain = composition.compose_chain(distance="ironman", runway_weeks=24)
    assert chain.template_id == "triathlon_full_24wk"
    assert chain.phases[-1].id == "taper_multisport"
    assert chain.total_weeks == 24


# --- Injury-driven preblock (the 2027-half-ironman BSI case) -------------


def test_bsi_active_prepends_rehab_and_return_phases_for_70_3() -> None:
    chain = composition.compose_chain(
        distance="half_ironman",
        runway_weeks=53,  # the 2027-half-ironman runway
        active_injury_types=["BSI"],
    )
    ids = [p.id for p in chain.phases]
    assert ids[0] == "rehab_bike_only", f"first phase {ids[0]!r} not rehab_bike_only"
    assert "return_to_3sport" in ids
    # Build phase still requires preceding base totaling >=6wk
    assert chain.phases[-1].id == "taper_multisport"
    assert chain.pre_block_origin  # populated with rationale


def test_bsi_active_prepends_return_to_run_for_marathon() -> None:
    chain = composition.compose_chain(
        distance="marathon",
        runway_weeks=30,
        active_injury_types=["BSI"],
    )
    ids = [p.id for p in chain.phases]
    assert "rehab_bike_only" in ids
    assert "return_to_run" in ids
    assert chain.phases[-1].id == "taper_run"


def test_no_injury_no_preblock() -> None:
    chain = composition.compose_chain(distance="marathon", runway_weeks=18)
    ids = [p.id for p in chain.phases]
    assert "rehab_bike_only" not in ids
    assert "return_to_run" not in ids


def test_bike_only_injury_preserves_chain() -> None:
    """A BSI active flag shouldn't add rehab to a bike-only goal."""
    chain = composition.compose_chain(
        distance="gran_fondo",
        runway_weeks=12,
        active_injury_types=["BSI"],
    )
    ids = [p.id for p in chain.phases]
    # No rehab preblock for bike-only chain
    assert "rehab_bike_only" not in ids


# --- Validation -----------------------------------------------------------


def test_validate_catches_chain_missing_taper() -> None:
    library = composition.load_phase_library()
    bad_chain = composition.PhaseChain(
        template_id=None,
        distance="marathon",
        sport_focus={"run": 1.0},
        phases=[
            composition._compose_phase(library["base_aerobic_run"], 6),
            composition._compose_phase(library["build_run"], 4),
        ],
    )
    violations = composition.validate_chain(bad_chain, has_target_date=True)
    rule_ids = {v.rule_id for v in violations}
    assert "chain_must_end_taper_for_a_race" in rule_ids


def test_validate_catches_peak_without_build() -> None:
    library = composition.load_phase_library()
    bad_chain = composition.PhaseChain(
        template_id=None,
        distance="marathon",
        sport_focus={"run": 1.0},
        phases=[
            composition._compose_phase(library["base_aerobic_run"], 6),
            composition._compose_phase(library["peak_run"], 1),  # invalid: no build before
            composition._compose_phase(library["taper_run"], 1),
        ],
    )
    violations = composition.validate_chain(bad_chain)
    rule_ids = {v.rule_id for v in violations}
    assert (
        "peak_requires_preceding_build" in rule_ids
        or "predecessor_succession_valid" in rule_ids
    )


def test_validate_catches_rehab_without_injury() -> None:
    library = composition.load_phase_library()
    bad_chain = composition.PhaseChain(
        template_id=None,
        distance="half_ironman",
        sport_focus={"bike": 1.0},
        phases=[
            composition._compose_phase(library["rehab_bike_only"], 6),
            composition._compose_phase(library["base_aerobic_multisport"], 6),
            composition._compose_phase(library["build_multisport"], 4),
            composition._compose_phase(library["peak_multisport"], 2),
            composition._compose_phase(library["taper_multisport"], 2),
        ],
    )
    violations = composition.validate_chain(
        bad_chain,
        active_injury_preconditions=frozenset(),  # no injury
    )
    rule_ids = {v.rule_id for v in violations}
    assert "rehab_requires_active_injury" in rule_ids


def test_compose_unknown_distance_raises_with_violations() -> None:
    with pytest.raises(composition.CompositionError) as exc_info:
        composition.compose_chain(distance="orienteering_meet", runway_weeks=12)
    assert exc_info.value.violations
    assert exc_info.value.violations[0].rule_id == "no_template"


# --- Injury type → precondition mapping -----------------------------------


def test_derive_injury_preconditions_handles_bsi() -> None:
    pre = composition.derive_injury_preconditions(["BSI"])
    assert "active_injury_no_impact" in pre
    assert "active_injury_no_run" in pre


def test_derive_injury_preconditions_handles_unknown() -> None:
    pre = composition.derive_injury_preconditions(["completely_made_up"])
    assert pre == frozenset()


# --- Free-text injury parsing -----------------------------------------------


def test_injury_types_from_flags_extracts_bsi_from_real_world_heading() -> None:
    flags = ["2026-04-25 — left tibia (BSI grade 2) — severity 4"]
    types = composition.injury_types_from_flags(flags)
    assert "BSI" in types


def test_injury_types_from_flags_handles_multiple_signals() -> None:
    flags = [
        "2025-08-12 — right achilles tendinopathy",
        "2026-01-03 — left ITBS — severity 3",
    ]
    types = composition.injury_types_from_flags(flags)
    assert "achilles" in types
    assert "itbs" in types


def test_injury_types_from_flags_dedupes() -> None:
    flags = [
        "2026-04-25 — tibial bone stress",
        "2026-04-25 — left tibia (BSI grade 2)",  # both should map to BSI
    ]
    types = composition.injury_types_from_flags(flags)
    assert types.count("BSI") == 1


def test_injury_types_from_flags_empty_for_unknown_injury() -> None:
    flags = ["2026-04-25 — left ankle sprain — severity 2"]
    types = composition.injury_types_from_flags(flags)
    assert types == []  # ankle sprain not in known mapping; agent surfaces in rationale


# --- Non-race performance-target composition (US-07) ----------------------
#
# The composer needs to anchor on goals.yaml performance targets, not just
# race-calendar.yaml races. Lena/Tomás/Wes from US-07 each test a
# different metric → template path.


from datetime import date  # noqa: E402  -- placed near non-race tests for locality

from tempo import goals  # noqa: E402


def _perf_target_match(
    *,
    gid: str,
    metric: str,
    current: float,
    target: float,
    by_date: str | None = "2026-08-16",
) -> goals.athlete.GoalMatch:
    """Synthesize a non-race GoalMatch for a perf-target test."""
    data: dict = {
        "id": gid,
        "type": "performance_target",
        "metric": metric,
        "current": current,
        "target": target,
    }
    if by_date is not None:
        data["by_date"] = by_date
    return goals.athlete.GoalMatch(kind="non_race", data=data)


def test_compose_for_goal_lena_ftp_target_16wk() -> None:
    """Lena: ftp_w 248→280 by 2026-08-16 (16 weeks out from 2026-04-26)."""
    match = _perf_target_match(
        gid="2026-ftp-280",
        metric="ftp_w",
        current=248,
        target=280,
        by_date="2026-08-16",
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 4, 26))
    ids = [p.id for p in chain.phases]
    assert chain.template_id == "ftp_target_16wk"
    assert "ftp_progression_block" in ids
    assert "vo2_polarisation_block" in ids
    assert ids[-1] == "deload_test"
    # No taper required for non-race goals — ends in deload, not taper_*.
    assert not ids[-1].startswith("taper")
    assert chain.total_weeks == 16


def test_compose_for_goal_wes_strength_peak() -> None:
    """Wes: squat_1rm_kg target → strength-led chain."""
    match = _perf_target_match(
        gid="squat-1rm-2026",
        metric="squat_1rm_kg",
        current=145,
        target=170,
        by_date="2026-09-30",
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 4, 26))
    ids = [p.id for p in chain.phases]
    assert chain.template_id == "strength_peak_12wk"
    assert "strength_peak_block" in ids
    assert ids[-1] == "deload_test"
    # Strength-dominant sport_focus on the peaking block.
    peak = next(p for p in chain.phases if p.id == "strength_peak_block")
    assert peak.sport_focus.get("strength", 0) >= 0.5


def test_compose_for_goal_css_target() -> None:
    match = _perf_target_match(
        gid="css-pace-2026",
        metric="css_pace_s_per_100m",
        current=85.0,
        target=80.0,
        by_date="2026-08-01",
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 4, 26))
    assert chain.template_id == "css_target_12wk"
    assert "css_progression_block" in [p.id for p in chain.phases]
    assert chain.phases[-1].id == "deload_test"


def test_compose_for_goal_unsupported_metric_clear_error() -> None:
    """Unknown metric is rejected with a CompositionError listing supported metrics —
    no silent fallback to a generic chain."""
    match = goals.athlete.GoalMatch(
        kind="non_race",
        data={
            "id": "vert-jump-2026",
            "type": "performance_target",
            "metric": "vertical_jump_cm",
            "current": 50,
            "target": 60,
            "by_date": "2026-09-01",
        },
    )
    goal = goals.from_match(match)
    with pytest.raises(composition.CompositionError) as exc_info:
        composition.compose_for_goal(goal, today=date(2026, 4, 26))
    assert exc_info.value.violations
    # The violation surfaces the metric for the agent to relay.
    assert any("vertical_jump_cm" in v.message for v in exc_info.value.violations)


def test_compose_for_goal_maintenance_dated_uses_base_building() -> None:
    match = goals.athlete.GoalMatch(
        kind="non_race",
        data={
            "id": "maintain-2026",
            "type": "maintenance",
            "metric": "ftp_w",
            "current": 260,
            "by_date": "2026-08-01",
        },
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 6, 1))
    assert chain.template_id == "base_building_8wk"
    assert chain.phases[-1].id == "deload_test"


def test_compose_for_goal_streak_not_supported() -> None:
    """Streak/adventure types raise — they need different anchoring (future ticket)."""
    match = goals.athlete.GoalMatch(
        kind="non_race",
        data={
            "id": "100-day-ride-streak",
            "type": "streak",
            "by_date": "2026-09-01",
        },
    )
    goal = goals.from_match(match)
    with pytest.raises(composition.CompositionError):
        composition.compose_for_goal(goal, today=date(2026, 4, 26))


def test_compose_for_goal_race_path_still_works() -> None:
    """Existing race-calendar-anchored chains keep working through the new entry point."""
    match = goals.athlete.GoalMatch(
        kind="race",
        data={
            "id": "2026-marathon",
            "distance": "marathon",
            "date": "2026-09-01",
        },
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 4, 26))
    # Race chain DOES end in taper_*.
    assert chain.phases[-1].id.startswith("taper")


def test_from_match_rejects_perf_target_without_metric() -> None:
    bad = goals.athlete.GoalMatch(
        kind="non_race",
        data={
            "id": "broken",
            "type": "performance_target",
            "target": 280,
            "by_date": "2026-08-01",
        },
    )
    with pytest.raises(goals.GoalSchemaError) as exc_info:
        goals.from_match(bad)
    assert any("metric" in v for v in exc_info.value.violations)


def test_perf_target_runway_overshoot_extends_base() -> None:
    """Extra runway extends the earliest base phase, not the progression
    blocks — same simplification the race path uses."""
    match = _perf_target_match(
        gid="long-ftp",
        metric="ftp_w",
        current=240,
        target=260,
        by_date=None,
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(
        goal, today=date(2026, 4, 26), runway_weeks_override=20
    )
    assert chain.total_weeks == 20
    # Progression block stays in its 4-6 wk band.
    progression = next(p for p in chain.phases if p.id == "ftp_progression_block")
    assert 4 <= progression.weeks <= 6
    # Extra weeks landed in base_aerobic_bike.
    base = next(p for p in chain.phases if p.id == "base_aerobic_bike")
    assert base.weeks > 4


def test_validate_chain_requires_taper_flag() -> None:
    """Non-race chains end in deload_test — the taper rule should not fire."""
    library = composition.load_phase_library()
    chain = composition.PhaseChain(
        template_id=None,
        distance="ftp_target",
        sport_focus={"bike": 1.0},
        phases=[
            composition._compose_phase(library["base_aerobic_bike"], 4),
            composition._compose_phase(library["ftp_progression_block"], 6),
            composition._compose_phase(library["deload_test"], 2),
        ],
    )
    violations = composition.validate_chain(
        chain, has_target_date=True, requires_taper=False
    )
    rule_ids = {v.rule_id for v in violations}
    assert "chain_must_end_taper_for_a_race" not in rule_ids


def test_supported_perf_metrics_listed_for_introspection() -> None:
    """Agents should be able to read the supported metric list for error UX."""
    assert "ftp_w" in goals.SUPPORTED_PERF_METRICS
    assert "squat_1rm_kg" in goals.SUPPORTED_PERF_METRICS
    assert "css_pace_s_per_100m" in goals.SUPPORTED_PERF_METRICS


# --- Multi-A guardrail (tempo-wk7) ----------------------------------------


def _seed_race_calendar(tmp_path, races: list[dict]) -> None:
    """Helper for multi-A tests: write a races yaml under tmp_path/athlete/."""
    import yaml as _y

    (tmp_path / "athlete").mkdir(exist_ok=True)
    (tmp_path / "athlete" / "race-calendar.yaml").write_text(
        _y.safe_dump({"races": races}, sort_keys=False),
        encoding="utf-8",
    )


def test_multi_a_within_4wk_raises_without_flag(tmp_path, monkeypatch) -> None:
    """Acceptance: two confirmed A-races within 4 weeks raise CompositionError."""
    from tempo import athlete as athlete_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    _seed_race_calendar(
        tmp_path,
        [
            {"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
            {"id": "second-a", "date": "2026-09-22", "distance": "marathon", "priority": "A"},
        ],
    )

    match = goals.athlete.GoalMatch(
        kind="race",
        data={
            "id": "primary-a",
            "date": "2026-09-01",
            "distance": "marathon",
            "priority": "A",
            "status": "confirmed",
        },
    )
    goal = goals.from_match(match)
    with pytest.raises(composition.CompositionError, match="multi_a") as exc_info:
        composition.compose_for_goal(goal, today=date(2026, 4, 1), root=tmp_path)
    assert any(v.rule_id == "multi_a_within_window" for v in exc_info.value.violations)


def test_multi_a_within_8wk_raises_without_flag(tmp_path, monkeypatch) -> None:
    """Scope says 8wk window — 6wk separation still trips the guardrail."""
    from tempo import athlete as athlete_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    _seed_race_calendar(
        tmp_path,
        [
            {"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
            {"id": "second-a", "date": "2026-10-13", "distance": "marathon", "priority": "A"},
        ],
    )
    match = goals.athlete.GoalMatch(
        kind="race",
        data={"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
    )
    goal = goals.from_match(match)
    with pytest.raises(composition.CompositionError):
        composition.compose_for_goal(goal, today=date(2026, 4, 1), root=tmp_path)


def test_multi_a_outside_window_does_not_trigger(tmp_path, monkeypatch) -> None:
    """A-races more than 8wk apart compose normally without the flag."""
    from tempo import athlete as athlete_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    _seed_race_calendar(
        tmp_path,
        [
            {"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
            {"id": "later-a", "date": "2027-01-01", "distance": "marathon", "priority": "A"},
        ],
    )
    match = goals.athlete.GoalMatch(
        kind="race",
        data={"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 4, 1))
    assert chain.phases[-1].id == "taper_run"


def test_multi_a_cancelled_race_does_not_trip_guardrail(tmp_path, monkeypatch) -> None:
    """A nearby A-race that's cancelled is invisible to the guardrail."""
    from tempo import athlete as athlete_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    _seed_race_calendar(
        tmp_path,
        [
            {"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
            {
                "id": "ghost-a",
                "date": "2026-09-22",
                "distance": "marathon",
                "priority": "A",
                "status": "cancelled",
                "cancelled_reason": "travel conflict",
            },
        ],
    )
    match = goals.athlete.GoalMatch(
        kind="race",
        data={"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 4, 1))
    assert chain.phases[-1].id == "taper_run"


def test_multi_a_flag_opts_into_compose(tmp_path, monkeypatch) -> None:
    """Passing multi_a=True bypasses the guardrail (single-anchor compose for now)."""
    from tempo import athlete as athlete_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    _seed_race_calendar(
        tmp_path,
        [
            {"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
            {"id": "second-a", "date": "2026-09-22", "distance": "marathon", "priority": "A"},
        ],
    )
    match = goals.athlete.GoalMatch(
        kind="race",
        data={"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(
        goal, today=date(2026, 4, 1), multi_a=True
    )
    assert chain.phases[-1].id == "taper_run"


def test_multi_a_b_race_nearby_does_not_trigger(tmp_path, monkeypatch) -> None:
    """Only A-vs-A collisions are guarded; nearby B-races are fine."""
    from tempo import athlete as athlete_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    _seed_race_calendar(
        tmp_path,
        [
            {"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
            {"id": "tune-up-b", "date": "2026-08-15", "distance": "10k", "priority": "B"},
        ],
    )
    match = goals.athlete.GoalMatch(
        kind="race",
        data={"id": "primary-a", "date": "2026-09-01", "distance": "marathon", "priority": "A"},
    )
    goal = goals.from_match(match)
    chain = composition.compose_for_goal(goal, today=date(2026, 4, 1))
    assert chain.phases[-1].id == "taper_run"


# --- Fragment hook (tempo-0e3) -------------------------------------------


def _seed_minimal_plan_fragment(tmp_path, *, fragment_id, archetype, target_tss=22,
                                 cadence=2, created="2026-04-30", expires="2026-12-31"):
    import yaml as _yaml
    fdir = tmp_path / "plans" / "p1" / "fragments"
    fdir.mkdir(parents=True, exist_ok=True)
    body = {
        "fragment_id": fragment_id,
        "goal": "test fragment",
        "kind": "training",
        "created_at": created,
        "re_evaluate_after": expires,
        "duration_weeks": 8,
        "sessions": [
            {"archetype": archetype, "cadence_per_week": cadence, "target_tss": target_tss}
        ],
    }
    (fdir / f"{fragment_id}.yaml").write_text(_yaml.safe_dump(body), encoding="utf-8")


def _copy_methodology(tmp_path) -> None:
    """Copy phases.yaml + session-library so compose_chain works under tmp_path."""
    import shutil

    from tempo.paths import repo_root as _repo_root

    src = _repo_root() / "knowledge" / "methodology"
    dst = tmp_path / "knowledge" / "methodology"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def test_compose_chain_loads_active_fragments_for_plan(tmp_path) -> None:
    _copy_methodology(tmp_path)
    _seed_minimal_plan_fragment(
        tmp_path,
        fragment_id="stronger-legs",
        archetype="strength_intensification_block",
    )
    chain = composition.compose_chain(
        distance="half_ironman",
        runway_weeks=16,
        plan_id="p1",
        today=date(2026, 5, 15),
        root=tmp_path,
    )
    ids = [f.fragment_id for f in chain.active_fragments]
    assert ids == ["stronger-legs"]


def test_compose_chain_omits_expired_fragments(tmp_path) -> None:
    _copy_methodology(tmp_path)
    _seed_minimal_plan_fragment(
        tmp_path,
        fragment_id="old-block",
        archetype="strength_intensification_block",
        created="2025-01-01",
        expires="2025-03-01",
    )
    chain = composition.compose_chain(
        distance="half_ironman",
        runway_weeks=16,
        plan_id="p1",
        today=date(2026, 5, 15),
        root=tmp_path,
    )
    assert chain.active_fragments == []


def test_compose_chain_without_plan_id_loads_no_fragments(tmp_path) -> None:
    _copy_methodology(tmp_path)
    _seed_minimal_plan_fragment(
        tmp_path,
        fragment_id="stronger-legs",
        archetype="strength_intensification_block",
    )
    chain = composition.compose_chain(
        distance="half_ironman",
        runway_weeks=16,
        # no plan_id → no fragment loading at all (back-compat for callers
        # that don't have a plan context yet).
        today=date(2026, 5, 15),
        root=tmp_path,
    )
    assert chain.active_fragments == []


def test_compose_chain_propagates_unknown_archetype_error(tmp_path) -> None:
    """A fragment referencing a non-existent archetype is a hard error,
    not a silent skip — easier to debug at compose time than later."""
    from tempo import fragments as _frag

    _copy_methodology(tmp_path)
    _seed_minimal_plan_fragment(
        tmp_path,
        fragment_id="bad-frag",
        archetype="not_a_real_archetype",
    )
    with pytest.raises(_frag.FragmentSchemaError):
        composition.compose_chain(
            distance="half_ironman",
            runway_weeks=16,
            plan_id="p1",
            today=date(2026, 5, 15),
            root=tmp_path,
        )
