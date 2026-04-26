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
