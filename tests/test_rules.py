"""Tests for ``tempo.rules`` — typed validators for decision-rules.md.

The conformance test pins the markdown spec to the code registry: any new
R-N added to the markdown must be registered in code (with matching id +
severity), and vice versa. That guarantee is what lets ``decision-rules.md``
remain the human-readable spec without drifting from the executable mirror.
"""

from __future__ import annotations

from tempo import rules
from tempo.paths import repo_root


def _session(
    *,
    sid: str,
    sport: str,
    date: str,
    day: str,
    target_tss: float | None = None,
    duration_s: int | None = None,
    library_ref: str | None = None,
    notes: str = "",
) -> rules.Session:
    return rules.Session(
        id=sid,
        day=day,
        date=date,
        sport=sport,
        library_ref=library_ref,
        target_tss=target_tss,
        target_duration_s=duration_s,
        notes=notes,
    )


def _week(sessions: list[rules.Session], **overrides: object) -> rules.WeekDraft:
    base: dict[str, object] = {
        "week_id": "2026-W18",
        "plan_id": "p1",
        "phase": "base",
        "week_of_phase": 1,
        "target_tss": 600.0,
    }
    base.update(overrides)
    return rules.WeekDraft(sessions=sessions, **base)  # type: ignore[arg-type]


# --- Conformance ---------------------------------------------------------


def test_every_markdown_rule_has_a_registered_function() -> None:
    md_rules = rules.parse_decision_rules_md(
        repo_root() / "knowledge" / "methodology" / "decision-rules.md"
    )
    assert md_rules, "decision-rules.md parse returned no rules"

    registered = set(rules.registered_rule_ids())
    md_ids = {rid for rid, _, _ in md_rules}

    missing_from_code = md_ids - registered
    missing_from_md = registered - md_ids
    assert not missing_from_code, f"rules in markdown but not registered: {missing_from_code}"
    assert not missing_from_md, f"rules registered but not in markdown: {missing_from_md}"


def test_severity_matches_markdown() -> None:
    md_rules = rules.parse_decision_rules_md(
        repo_root() / "knowledge" / "methodology" / "decision-rules.md"
    )
    for rule_id, _, md_severity in md_rules:
        entry = rules.registry_entry(rule_id)
        assert entry.severity == md_severity, (
            f"{rule_id}: markdown says {md_severity}, code says {entry.severity}"
        )


# --- R-5 Active injury ---------------------------------------------------


def test_r5_blocks_run_when_run_is_forbidden() -> None:
    week = _week([
        _session(sid="mon-run", sport="run", day="Monday", date="2026-04-27",
                 target_tss=70.0, duration_s=45 * 60),
        _session(sid="tue-bike", sport="bike", day="Tuesday", date="2026-04-28",
                 target_tss=80.0, duration_s=90 * 60),
    ])
    ctx = rules.RulesContext(
        week_draft=week,
        active_injuries=(rules.InjuryFlag(
            description="L tibia BSI g2",
            forbidden_sports=frozenset({"run"}),
            bone_stress=True,
        ),),
    )
    violations = [v for v in rules.validate_week(ctx) if v.rule_id == "R-5"]
    assert len(violations) == 1
    assert violations[0].severity == "HARD"
    assert violations[0].session_id == "mon-run"
    assert violations[0].override_path is None


def test_r5_passes_when_no_injuries() -> None:
    week = _week([
        _session(sid="mon-run", sport="run", day="Monday", date="2026-04-27",
                 target_tss=70.0, duration_s=45 * 60),
    ])
    ctx = rules.RulesContext(week_draft=week, active_injuries=())
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-5"] == []


# --- R-7 Bone stress -----------------------------------------------------


def test_r7_blocks_high_intensity_bike_with_bone_stress() -> None:
    week = _week([
        _session(sid="wed-vo2", sport="bike", day="Wednesday", date="2026-04-29",
                 library_ref="vo2_intervals_bike", target_tss=110.0, duration_s=60 * 60),
        _session(sid="thu-z2", sport="bike", day="Thursday", date="2026-04-30",
                 library_ref="easy_aerobic_ride", target_tss=55.0, duration_s=75 * 60),
    ])
    ctx = rules.RulesContext(
        week_draft=week,
        active_injuries=(rules.InjuryFlag(
            description="L tibia BSI g2",
            forbidden_sports=frozenset({"run"}),
            bone_stress=True,
        ),),
    )
    violations = [v for v in rules.validate_week(ctx) if v.rule_id == "R-7"]
    assert len(violations) == 1
    assert violations[0].session_id == "wed-vo2"


def test_r7_silent_without_bone_stress_flag() -> None:
    week = _week([
        _session(sid="wed-vo2", sport="bike", day="Wednesday", date="2026-04-29",
                 library_ref="vo2_intervals_bike", target_tss=110.0, duration_s=60 * 60),
    ])
    ctx = rules.RulesContext(
        week_draft=week,
        active_injuries=(rules.InjuryFlag(
            description="left calf strain",
            forbidden_sports=frozenset({"run"}),
            bone_stress=False,
        ),),
    )
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-7"] == []


# --- R-11 Back-to-back hard ----------------------------------------------


def test_r11_flags_hard_bike_then_hard_run_consecutive() -> None:
    week = _week([
        _session(sid="mon-z2-bike", sport="bike", day="Monday", date="2026-04-27",
                 target_tss=50.0, duration_s=60 * 60),
        _session(sid="tue-thresh-bike", sport="bike", day="Tuesday", date="2026-04-28",
                 target_tss=110.0, duration_s=80 * 60),
        _session(sid="wed-thresh-run", sport="run", day="Wednesday", date="2026-04-29",
                 target_tss=85.0, duration_s=50 * 60),
    ])
    ctx = rules.RulesContext(week_draft=week)
    violations = [v for v in rules.validate_week(ctx) if v.rule_id == "R-11"]
    assert len(violations) == 1
    assert violations[0].severity == "SOFT"
    assert violations[0].override_path is not None  # SOFT rules describe override


def test_r11_passes_when_separated() -> None:
    week = _week([
        _session(sid="tue-thresh-bike", sport="bike", day="Tuesday", date="2026-04-28",
                 target_tss=110.0, duration_s=80 * 60),
        _session(sid="thu-thresh-run", sport="run", day="Thursday", date="2026-04-30",
                 target_tss=85.0, duration_s=50 * 60),
    ])
    ctx = rules.RulesContext(week_draft=week)
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-11"] == []


# --- R-14 Long-run +15% cap ----------------------------------------------


def test_r14_blocks_long_run_growing_more_than_15_percent() -> None:
    prior = _week([
        _session(sid="prev-long", sport="run", day="Saturday", date="2026-04-25",
                 target_tss=120.0, duration_s=60 * 60),
    ], week_id="2026-W17")
    current = _week([
        _session(sid="this-long", sport="run", day="Saturday", date="2026-05-02",
                 target_tss=160.0, duration_s=80 * 60),  # +33%
    ])
    ctx = rules.RulesContext(week_draft=current, prior_week_draft=prior)
    violations = [v for v in rules.validate_week(ctx) if v.rule_id == "R-14"]
    assert len(violations) == 1
    assert violations[0].severity == "HARD"
    assert violations[0].override_path is None


def test_r14_passes_at_or_below_15_percent() -> None:
    prior = _week([
        _session(sid="prev-long", sport="run", day="Saturday", date="2026-04-25",
                 target_tss=120.0, duration_s=60 * 60),
    ], week_id="2026-W17")
    current = _week([
        _session(sid="this-long", sport="run", day="Saturday", date="2026-05-02",
                 target_tss=130.0, duration_s=int(60 * 60 * 1.15)),
    ])
    ctx = rules.RulesContext(week_draft=current, prior_week_draft=prior)
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-14"] == []


# --- R-17 Long-session fueling -------------------------------------------


def test_r17_flags_long_ride_without_fueling_plan() -> None:
    week = _week([
        _session(sid="sat-long-ride", sport="bike", day="Saturday", date="2026-05-02",
                 target_tss=200.0, duration_s=4 * 3600,
                 notes="Z2 endurance, hold 0.65 IF."),
    ])
    ctx = rules.RulesContext(week_draft=week)
    violations = [v for v in rules.validate_week(ctx) if v.rule_id == "R-17"]
    assert len(violations) == 1
    assert violations[0].severity == "HARD"


def test_r17_passes_with_fueling_mention() -> None:
    week = _week([
        _session(sid="sat-long-ride", sport="bike", day="Saturday", date="2026-05-02",
                 target_tss=200.0, duration_s=4 * 3600,
                 notes="Z2; fueling: 80g carb/h via 2x bottles + 2 gels."),
    ])
    ctx = rules.RulesContext(week_draft=week)
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-17"] == []


def test_r17_does_not_fire_for_short_run() -> None:
    week = _week([
        _session(sid="tue-tempo", sport="run", day="Tuesday", date="2026-04-28",
                 target_tss=70.0, duration_s=50 * 60, notes=""),
    ])
    ctx = rules.RulesContext(week_draft=week)
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-17"] == []


# --- R-19 Race priority taper (tempo-wk7) --------------------------------


def _full_volume_week(week_id: str = "2026-W37") -> rules.WeekDraft:
    """A normal week's Mon-Thu — used as the prior-week baseline for R-19."""
    return _week(
        [
            _session(sid="mon-z2", sport="bike", day="Monday", date="2026-09-07",
                     duration_s=60 * 60),
            _session(sid="tue-thresh", sport="bike", day="Tuesday", date="2026-09-08",
                     duration_s=80 * 60),
            _session(sid="wed-easy-run", sport="run", day="Wednesday", date="2026-09-09",
                     duration_s=45 * 60),
            _session(sid="thu-tempo-run", sport="run", day="Thursday", date="2026-09-10",
                     duration_s=60 * 60),
        ],
        week_id=week_id,
    )


def test_r19_b_race_week_with_full_volume_flags_micro_taper_violation() -> None:
    """Acceptance: a B-race week without a Mon-Thu cut surfaces R-19 unprompted."""
    prior = _full_volume_week(week_id="2026-W37")
    # This week's Mon-Thu volume == prior week's. No cut → R-19 fires.
    current_sessions = [
        _session(sid="mon-z2", sport="bike", day="Monday", date="2026-09-14",
                 duration_s=60 * 60),
        _session(sid="tue-thresh", sport="bike", day="Tuesday", date="2026-09-15",
                 duration_s=80 * 60),
        _session(sid="wed-easy-run", sport="run", day="Wednesday", date="2026-09-16",
                 duration_s=45 * 60),
        _session(sid="thu-tempo-run", sport="run", day="Thursday", date="2026-09-17",
                 duration_s=60 * 60),
        # Friday opener present so we isolate the volume violation:
        _session(sid="fri-opener", sport="run", day="Friday", date="2026-09-18",
                 duration_s=20 * 60),
        _session(sid="sat-race", sport="run", day="Saturday", date="2026-09-19",
                 duration_s=60 * 60),
    ]
    current = _week(current_sessions, week_id="2026-W38")
    ctx = rules.RulesContext(
        week_draft=current,
        prior_week_draft=prior,
        race_in_week=rules.RaceInWeek(
            race_id="tune-up-10k", date="2026-09-19", priority="B"
        ),
    )
    violations = [v for v in rules.validate_week(ctx) if v.rule_id == "R-19"]
    assert len(violations) == 1
    assert violations[0].severity == "SOFT"
    assert "Mon-Thu volume" in violations[0].message
    assert violations[0].override_path is not None


def test_r19_b_race_week_with_micro_taper_passes() -> None:
    prior = _full_volume_week(week_id="2026-W37")
    # Cut Mon-Thu by 30% (well under 80% threshold).
    current_sessions = [
        _session(sid="mon-z2", sport="bike", day="Monday", date="2026-09-14",
                 duration_s=40 * 60),
        _session(sid="tue-thresh", sport="bike", day="Tuesday", date="2026-09-15",
                 duration_s=50 * 60),
        _session(sid="wed-easy-run", sport="run", day="Wednesday", date="2026-09-16",
                 duration_s=30 * 60),
        _session(sid="thu-tempo-run", sport="run", day="Thursday", date="2026-09-17",
                 duration_s=40 * 60),
        _session(sid="fri-opener", sport="run", day="Friday", date="2026-09-18",
                 duration_s=20 * 60),
        _session(sid="sat-race", sport="run", day="Saturday", date="2026-09-19",
                 duration_s=60 * 60),
    ]
    current = _week(current_sessions, week_id="2026-W38")
    ctx = rules.RulesContext(
        week_draft=current,
        prior_week_draft=prior,
        race_in_week=rules.RaceInWeek(
            race_id="tune-up-10k", date="2026-09-19", priority="B"
        ),
    )
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-19"] == []


def test_r19_b_race_week_without_friday_opener_flags() -> None:
    prior = _full_volume_week(week_id="2026-W37")
    # Mon-Thu cut applied; Friday is a rest day → opener-missing violation only.
    current_sessions = [
        _session(sid="mon-z2", sport="bike", day="Monday", date="2026-09-14",
                 duration_s=40 * 60),
        _session(sid="tue-thresh", sport="bike", day="Tuesday", date="2026-09-15",
                 duration_s=50 * 60),
        _session(sid="wed-easy-run", sport="run", day="Wednesday", date="2026-09-16",
                 duration_s=30 * 60),
        _session(sid="thu-tempo-run", sport="run", day="Thursday", date="2026-09-17",
                 duration_s=40 * 60),
        _session(sid="sat-race", sport="run", day="Saturday", date="2026-09-19",
                 duration_s=60 * 60),
    ]
    current = _week(current_sessions, week_id="2026-W38")
    ctx = rules.RulesContext(
        week_draft=current,
        prior_week_draft=prior,
        race_in_week=rules.RaceInWeek(
            race_id="tune-up-10k", date="2026-09-19", priority="B"
        ),
    )
    violations = [v for v in rules.validate_week(ctx) if v.rule_id == "R-19"]
    assert len(violations) == 1
    assert "opener" in violations[0].message.lower()


def test_r19_c_race_week_does_not_fire() -> None:
    """C races train through — no taper-shape requirement."""
    prior = _full_volume_week(week_id="2026-W37")
    current = _full_volume_week(week_id="2026-W38")
    ctx = rules.RulesContext(
        week_draft=current,
        prior_week_draft=prior,
        race_in_week=rules.RaceInWeek(
            race_id="local-5k", date="2026-09-19", priority="C"
        ),
    )
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-19"] == []


def test_r19_a_race_week_silent_taper_handled_by_phase() -> None:
    """A-race weeks rely on the taper_* phase; R-19 stays silent."""
    prior = _full_volume_week(week_id="2026-W37")
    current = _full_volume_week(week_id="2026-W38")
    ctx = rules.RulesContext(
        week_draft=current,
        prior_week_draft=prior,
        race_in_week=rules.RaceInWeek(
            race_id="goal-marathon", date="2026-09-19", priority="A"
        ),
    )
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-19"] == []


def test_r19_no_race_in_week_silent() -> None:
    week = _full_volume_week()
    ctx = rules.RulesContext(week_draft=week)
    assert [v for v in rules.validate_week(ctx) if v.rule_id == "R-19"] == []


# --- HARD violations have None override_path -----------------------------


def test_all_hard_violations_have_no_override_path() -> None:
    """Sanity: every HARD severity must surface override_path=None."""
    week = _week([
        _session(sid="run", sport="run", day="Monday", date="2026-04-27",
                 target_tss=70.0, duration_s=45 * 60),
        _session(sid="long-bike", sport="bike", day="Saturday", date="2026-05-02",
                 target_tss=220.0, duration_s=4 * 3600, notes=""),
    ])
    ctx = rules.RulesContext(
        week_draft=week,
        active_injuries=(rules.InjuryFlag(
            description="L tibia BSI g2",
            forbidden_sports=frozenset({"run"}),
            bone_stress=True,
        ),),
    )
    for v in rules.validate_week(ctx):
        if v.severity == "HARD":
            assert v.override_path is None, f"HARD rule {v.rule_id} surfaced override_path"
