"""Typed validators for ``knowledge/methodology/decision-rules.md``.

The decision rules (R-1..R-18) used to live only as prose. Each skill said
"validate every drafted session against decision-rules.md" — meaning the
agent had to remember each rule on each draft. HARD rules could be skipped
by oversight.

This module mirrors decision-rules.md as code:

- One :func:`register_rule` per R-N.
- :func:`validate_week` walks every registered rule against a
  :class:`RulesContext` and returns a flat list of :class:`Violation`.
- The ``conformance`` test asserts every R-N in decision-rules.md has a
  matching registered rule, so the prose and the code can't drift.

Severity contract:
- **HARD**: never override. ``override_path`` is ``None``.
- **SOFT**: override allowed with rationale. ``override_path`` describes
  what the override needs (e.g. ``changelog rationale + log_decision``).
- **WATCH**: informational only; surfaced in review, not blocking.

Many rules need data (wellness time series, load curve) that isn't always
in the brief. Those rules are registered with ``implemented=False`` and
return an empty list — the conformance test still passes, but
``validate_week`` won't surface those violations until the upstream data
flows. Switching them on is a one-line change once data lands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Severity = Literal["HARD", "SOFT", "WATCH"]
Topic = Literal[
    "wellness",
    "injury",
    "load",
    "session_placement",
    "progression",
    "fueling",
    "subprogram",
]


@dataclass
class Session:
    """One drafted session inside a week."""

    id: str  # e.g. "mon-2026-04-27" or library_ref-keyed slug
    day: str  # ISO weekday: "Monday".."Sunday"
    date: str  # YYYY-MM-DD
    sport: str  # "bike" | "run" | "swim" | "strength" | "brick"
    library_ref: str | None = None  # e.g. "easy_aerobic_ride"
    target_tss: float | None = None
    target_duration_s: int | None = None
    purpose: str | None = None
    notes: str = ""


@dataclass
class WeekDraft:
    """Draft of a week, parsed from ``plans/<plan>/weeks/<week>.md``."""

    week_id: str
    plan_id: str
    phase: str
    week_of_phase: int | None
    target_tss: float | None
    intensity_distribution: dict[str, Any] = field(default_factory=dict)
    sessions: list[Session] = field(default_factory=list)


@dataclass
class InjuryFlag:
    """One active flag from injury-log.md.

    ``forbidden_sports`` is the parsed set of sports the injury blocks
    (e.g. {"run"} for a tibial BSI). ``forbidden_categories`` covers
    finer-grained constraints (e.g. {"plyo", "high_impact"}) that can
    apply across sports.
    """

    description: str
    forbidden_sports: frozenset[str] = frozenset()
    forbidden_categories: frozenset[str] = frozenset()
    bone_stress: bool = False  # signals R-7 lineage


@dataclass
class RaceInWeek:
    """Race that falls inside the week being drafted.

    ``priority`` mirrors race-calendar.yaml's A/B/C convention. R-19 reads
    this to enforce per-priority taper shape (full taper for A is handled
    by phase composition; B requires micro-taper; C trains through).
    """

    race_id: str
    date: str  # YYYY-MM-DD
    priority: str  # "A" | "B" | "C"


@dataclass
class RulesContext:
    """All inputs the rule registry needs to validate a week.

    Future signals (HRV trend, load ramp, readiness) will land as
    additional optional fields. Rules that need them gate on presence
    via ``hasattr`` / ``is None`` checks.
    """

    week_draft: WeekDraft
    prior_week_draft: WeekDraft | None = None  # for week-over-week progression rules
    active_injuries: tuple[InjuryFlag, ...] = ()
    current_phase: str | None = None
    is_taper_or_peak: bool = False
    race_in_week: RaceInWeek | None = None  # B/C race week → R-19 fires
    # R-20 inputs. ``active_fragments`` is list[tempo.fragments.PlanFragment];
    # typed loose to keep rules.py importable without a hard dep on fragments.
    active_fragments: tuple[Any, ...] = ()
    phase_tss_upper: float | None = None  # phase's upper TSS-target band, for R-20


@dataclass
class Violation:
    """One rule violation against a session (or whole week)."""

    rule_id: str  # "R-5"
    severity: Severity
    message: str
    session_id: str | None = None  # None = week-level violation
    override_path: str | None = None  # how to override SOFT rules; None for HARD


# --- Registry --------------------------------------------------------------

RuleFn = Callable[[RulesContext], list[Violation]]


@dataclass
class _RuleEntry:
    rule_id: str
    severity: Severity
    topic: Topic
    name: str
    implemented: bool
    fn: RuleFn


_REGISTRY: dict[str, _RuleEntry] = {}


def register_rule(
    *,
    rule_id: str,
    severity: Severity,
    topic: Topic,
    name: str,
    implemented: bool = True,
) -> Callable[[RuleFn], RuleFn]:
    """Register a rule function under its R-N id."""

    def decorator(fn: RuleFn) -> RuleFn:
        if rule_id in _REGISTRY:
            raise ValueError(f"rule {rule_id} registered twice")
        _REGISTRY[rule_id] = _RuleEntry(
            rule_id=rule_id,
            severity=severity,
            topic=topic,
            name=name,
            implemented=implemented,
            fn=fn,
        )
        return fn

    return decorator


def registered_rule_ids() -> list[str]:
    """All R-N ids currently in the registry, sorted by numeric order."""
    return sorted(_REGISTRY.keys(), key=lambda rid: int(rid.split("-")[1]))


def registry_entry(rule_id: str) -> _RuleEntry:
    return _REGISTRY[rule_id]


def validate_week(ctx: RulesContext) -> list[Violation]:
    """Run every registered rule, return flat violation list.

    Order is stable (R-1, R-2, ..., R-18). Rules that aren't implemented
    yet return ``[]`` so callers don't see false negatives, but the
    conformance test still verifies every R-N exists.
    """
    out: list[Violation] = []
    for rid in registered_rule_ids():
        out.extend(_REGISTRY[rid].fn(ctx))
    return out


# --- Override-path constants ----------------------------------------------

_SOFT_OVERRIDE = (
    "Override requires: (1) a line in plans/<plan>/changelog.md with the "
    "reasoning, (2) coach-db.log_decision with kind=adjust."
)


# --- Implemented rules -----------------------------------------------------


@register_rule(
    rule_id="R-1",
    severity="SOFT",
    topic="wellness",
    name="HRV downward trend + negative TSB",
    implemented=False,
)
def r1_hrv_trend(ctx: RulesContext) -> list[Violation]:
    return []  # needs wellness time-series via brief; see roadmap


@register_rule(
    rule_id="R-2",
    severity="SOFT",
    topic="wellness",
    name="Low readiness",
    implemented=False,
)
def r2_low_readiness(ctx: RulesContext) -> list[Violation]:
    return []  # needs readiness time-series


@register_rule(
    rule_id="R-3",
    severity="WATCH",
    topic="wellness",
    name="Sleep deficit",
    implemented=False,
)
def r3_sleep_deficit(ctx: RulesContext) -> list[Violation]:
    return []


@register_rule(
    rule_id="R-4",
    severity="HARD",
    topic="wellness",
    name="Illness-adjacent symptoms",
    implemented=False,
)
def r4_illness_adjacent(ctx: RulesContext) -> list[Violation]:
    return []  # check requires symptom log; not in brief yet


@register_rule(
    rule_id="R-5",
    severity="HARD",
    topic="injury",
    name="Active injury flag",
)
def r5_active_injury(ctx: RulesContext) -> list[Violation]:
    """Block any session whose sport is in an active injury's forbidden_sports."""
    if not ctx.active_injuries:
        return []
    out: list[Violation] = []
    for session in ctx.week_draft.sessions:
        for flag in ctx.active_injuries:
            if session.sport in flag.forbidden_sports:
                out.append(
                    Violation(
                        rule_id="R-5",
                        severity="HARD",
                        message=(
                            f"Session sport={session.sport!r} is forbidden by "
                            f"active injury: {flag.description}"
                        ),
                        session_id=session.id,
                        override_path=None,
                    )
                )
    return out


@register_rule(
    rule_id="R-6",
    severity="HARD",
    topic="injury",
    name="Pain during warmup",
    implemented=False,
)
def r6_warmup_pain(ctx: RulesContext) -> list[Violation]:
    return []  # post-session rule; not a planning-time check


@register_rule(
    rule_id="R-7",
    severity="HARD",
    topic="injury",
    name="Bone stress red flags",
)
def r7_bone_stress(ctx: RulesContext) -> list[Violation]:
    """Bone stress: no run, no plyos/jumps, no above-Z3 bike until cleared."""
    bone_flags = [f for f in ctx.active_injuries if f.bone_stress]
    if not bone_flags:
        return []

    out: list[Violation] = []
    for session in ctx.week_draft.sessions:
        offending = None
        if session.sport == "run":
            offending = "running prohibited under bone-stress red flag"
        elif "plyo" in (session.library_ref or "").lower():
            offending = "plyometric content prohibited under bone-stress red flag"
        elif session.sport == "bike" and session.library_ref:
            ref = session.library_ref.lower()
            if any(tag in ref for tag in ("vo2", "anaerobic", "z5", "z6", "max_effort")):
                offending = (
                    "above-Z3 bike effort prohibited until bone stress cleared"
                )
        if offending:
            out.append(
                Violation(
                    rule_id="R-7",
                    severity="HARD",
                    message=offending,
                    session_id=session.id,
                    override_path=None,
                )
            )
    return out


@register_rule(
    rule_id="R-8",
    severity="SOFT",
    topic="load",
    name="CTL ramp rate cap",
    implemented=False,
)
def r8_ctl_ramp(ctx: RulesContext) -> list[Violation]:
    return []  # needs load curve in context


@register_rule(
    rule_id="R-9",
    severity="SOFT",
    topic="load",
    name="Sustained negative TSB",
    implemented=False,
)
def r9_neg_tsb(ctx: RulesContext) -> list[Violation]:
    return []  # needs load curve


@register_rule(
    rule_id="R-10",
    severity="HARD",
    topic="load",
    name="TSB recovery before A-race",
    implemented=False,
)
def r10_taper_tsb(ctx: RulesContext) -> list[Violation]:
    return []  # needs forward TSB simulation


@register_rule(
    rule_id="R-11",
    severity="SOFT",
    topic="session_placement",
    name="No back-to-back hard",
)
def r11_back_to_back_hard(ctx: RulesContext) -> list[Violation]:
    """Hardest bike and hardest run on consecutive days → SOFT violation."""
    sessions = sorted(
        (s for s in ctx.week_draft.sessions if s.target_tss is not None),
        key=lambda s: s.date,
    )
    bike_sessions = [s for s in sessions if s.sport == "bike"]
    run_sessions = [s for s in sessions if s.sport == "run"]
    if not bike_sessions or not run_sessions:
        return []

    hardest_bike = max(bike_sessions, key=lambda s: float(s.target_tss or 0))
    hardest_run = max(run_sessions, key=lambda s: float(s.target_tss or 0))

    if abs(_days_between(hardest_bike.date, hardest_run.date)) <= 1:
        return [
            Violation(
                rule_id="R-11",
                severity="SOFT",
                message=(
                    f"Hardest bike ({hardest_bike.id}) and hardest run "
                    f"({hardest_run.id}) are on consecutive days."
                ),
                session_id=hardest_run.id,
                override_path=_SOFT_OVERRIDE,
            )
        ]
    return []


@register_rule(
    rule_id="R-12",
    severity="SOFT",
    topic="session_placement",
    name="Long-day anchors separated",
    implemented=False,
)
def r12_long_day_anchors(ctx: RulesContext) -> list[Violation]:
    return []  # needs explicit long-ride / long-run tagging


@register_rule(
    rule_id="R-13",
    severity="WATCH",
    topic="session_placement",
    name="Swim-first on hard-bike days",
    implemented=False,
)
def r13_swim_first(ctx: RulesContext) -> list[Violation]:
    return []  # needs swim+bike-on-same-day detection with morning/evening tags


@register_rule(
    rule_id="R-14",
    severity="HARD",
    topic="progression",
    name="Long-run progression cap",
)
def r14_long_run_cap(ctx: RulesContext) -> list[Violation]:
    """Long run duration may not increase >15% week-over-week."""
    if ctx.prior_week_draft is None:
        return []

    cur_long = _longest_run(ctx.week_draft)
    prev_long = _longest_run(ctx.prior_week_draft)
    if cur_long is None or prev_long is None:
        return []
    if (prev_long.target_duration_s or 0) <= 0:
        return []

    growth = (
        (cur_long.target_duration_s or 0) - (prev_long.target_duration_s or 0)
    ) / float(prev_long.target_duration_s or 1)
    if growth > 0.15:
        return [
            Violation(
                rule_id="R-14",
                severity="HARD",
                message=(
                    f"Long run grew {growth * 100:.1f}% week-over-week "
                    f"({prev_long.target_duration_s}s → {cur_long.target_duration_s}s); "
                    "exceeds +15% cap."
                ),
                session_id=cur_long.id,
                override_path=None,
            )
        ]
    return []


@register_rule(
    rule_id="R-15",
    severity="SOFT",
    topic="progression",
    name="Down-week cadence",
    implemented=False,
)
def r15_down_week(ctx: RulesContext) -> list[Violation]:
    return []  # needs multi-week history beyond prior_week


@register_rule(
    rule_id="R-16",
    severity="SOFT",
    topic="progression",
    name="Race-pace introduction timing",
    implemented=False,
)
def r16_race_pace_timing(ctx: RulesContext) -> list[Violation]:
    return []  # phase awareness exists; need session-level race-pace tag


@register_rule(
    rule_id="R-17",
    severity="HARD",
    topic="fueling",
    name="Long-session fueling rehearsal",
)
def r17_long_session_fueling(ctx: RulesContext) -> list[Violation]:
    """Long ride > 3h or long run > 90 min must mention a fueling plan."""
    out: list[Violation] = []
    for session in ctx.week_draft.sessions:
        duration = session.target_duration_s or 0
        if session.sport == "bike" and duration > 3 * 3600:
            triggered = True
        elif session.sport == "run" and duration > 90 * 60:
            triggered = True
        else:
            triggered = False
        if not triggered:
            continue
        notes_lower = session.notes.lower()
        if "fuel" not in notes_lower and "carb" not in notes_lower and "gel" not in notes_lower:
            out.append(
                Violation(
                    rule_id="R-17",
                    severity="HARD",
                    message=(
                        f"Long {session.sport} session ({duration // 60} min) lacks a "
                        "documented fueling plan in notes."
                    ),
                    session_id=session.id,
                    override_path=None,
                )
            )
    return out


@register_rule(
    rule_id="R-18",
    severity="WATCH",
    topic="fueling",
    name="Gut training gate",
    implemented=False,
)
def r18_gut_training(ctx: RulesContext) -> list[Violation]:
    return []  # needs cross-session athlete-tested.yaml lookup


@register_rule(
    rule_id="R-19",
    severity="SOFT",
    topic="session_placement",
    name="Race priority taper",
)
def r19_race_priority_taper(ctx: RulesContext) -> list[Violation]:
    """Per-priority taper shape on a race week.

    A-race weeks are tapered by phase composition (full taper_* phase) — R-19
    doesn't need to fire there. B-race weeks require a micro-taper: Mon-Thu
    volume cut by ~20% vs the prior week, plus an opener Friday session.
    C-race weeks train through with no taper-shape adjustment.

    Why SOFT: Sean may consciously choose to trial race-pace through a B-race
    or skip the opener for a logistically tight week — those are valid coaching
    choices that get logged in changelog.md. The override path captures that.
    """
    race = ctx.race_in_week
    if race is None:
        return []
    priority = (race.priority or "").upper()
    if priority not in {"B", "C"}:
        return []

    out: list[Violation] = []
    sessions = ctx.week_draft.sessions

    if priority == "C":
        return out  # train-through; no shape constraint

    # B-race week: Mon-Thu volume vs prior week's Mon-Thu volume.
    if ctx.prior_week_draft is None:
        return out  # no baseline to compare against; surface nothing

    cur_mon_thu = _mon_thu_duration_total(sessions)
    prev_mon_thu = _mon_thu_duration_total(ctx.prior_week_draft.sessions)
    if prev_mon_thu > 0 and cur_mon_thu > prev_mon_thu * 0.85:
        # 0.85 = 15% reduction floor; ticket asks for ~20% cut, so anything
        # above 85% of prior week's volume fails to deliver the micro-taper.
        out.append(
            Violation(
                rule_id="R-19",
                severity="SOFT",
                message=(
                    f"B-race week ({race.race_id} on {race.date}): Mon-Thu volume "
                    f"{cur_mon_thu // 60}min is {cur_mon_thu / prev_mon_thu * 100:.0f}% "
                    f"of prior week ({prev_mon_thu // 60}min); micro-taper expects "
                    "~80% (≥20% cut)."
                ),
                session_id=None,
                override_path=_SOFT_OVERRIDE,
            )
        )

    has_friday_opener = any(
        s.day == "Friday" and (s.target_duration_s or 0) > 0
        for s in sessions
    )
    if not has_friday_opener:
        out.append(
            Violation(
                rule_id="R-19",
                severity="SOFT",
                message=(
                    f"B-race week ({race.race_id} on {race.date}): no Friday "
                    "opener session — micro-taper expects a short race-pace "
                    "primer the day before."
                ),
                session_id=None,
                override_path=_SOFT_OVERRIDE,
            )
        )

    return out


@register_rule(
    rule_id="R-20",
    severity="SOFT",
    topic="subprogram",
    name="Active sub-program capacity",
)
def r20_active_subprogram_capacity(ctx: RulesContext) -> list[Violation]:
    """Active goal-research fragments shouldn't push weekly TSS > +15% over phase upper.

    Reads ``ctx.active_fragments`` (list of :class:`tempo.fragments.PlanFragment`)
    and ``ctx.phase_tss_upper`` (the upper end of the phase's
    ``weekly_tss_target`` band). The drafted week's session TSS plus the
    fragments' estimated weekly TSS contribution must stay within
    ``phase_tss_upper * 1.15``.

    Why SOFT: a one-week intentional spike (race-sim landing in an active
    strength block) is a valid coaching choice. The override path captures
    that. Persistent overage past two weeks should trigger fragment
    re-evaluation — that's a coaching prompt, not a HARD reject.
    """
    if not ctx.active_fragments or ctx.phase_tss_upper is None:
        return []

    week_tss = sum((s.target_tss or 0.0) for s in ctx.week_draft.sessions)
    fragment_tss = 0.0
    contributions: list[str] = []
    for frag in ctx.active_fragments:
        # Duck-type: PlanFragment has estimated_weekly_tss(); kept loose so
        # rules.py doesn't import fragments.py at module load.
        contrib = float(getattr(frag, "estimated_weekly_tss", lambda: 0.0)())
        if contrib > 0:
            fragment_tss += contrib
            contributions.append(
                f"{getattr(frag, 'fragment_id', '?')}: {contrib:.0f}"
            )

    projected = week_tss + fragment_tss
    cap = float(ctx.phase_tss_upper) * 1.15
    if projected <= cap:
        return []

    overage_pct = (projected / float(ctx.phase_tss_upper) - 1.0) * 100.0
    return [
        Violation(
            rule_id="R-20",
            severity="SOFT",
            message=(
                f"Active sub-program(s) push projected TSS to {projected:.0f} "
                f"(week sessions {week_tss:.0f} + fragments {fragment_tss:.0f}), "
                f"{overage_pct:.0f}% over phase upper {ctx.phase_tss_upper:.0f}; "
                f"15% cap exceeded. Fragment contributions: "
                f"[{', '.join(contributions) or 'none'}]. "
                "Drop a fragment session this week or accept overage in changelog."
            ),
            session_id=None,
            override_path=_SOFT_OVERRIDE,
        )
    ]


# --- Helpers ---------------------------------------------------------------


def _days_between(a: str, b: str) -> int:
    from datetime import date as _date

    da = _date.fromisoformat(a)
    db = _date.fromisoformat(b)
    return (da - db).days


_MON_THU = frozenset({"Monday", "Tuesday", "Wednesday", "Thursday"})


def _mon_thu_duration_total(sessions: list[Session]) -> int:
    """Sum target_duration_s for sessions on Mon-Thu (R-19 micro-taper baseline)."""
    return sum((s.target_duration_s or 0) for s in sessions if s.day in _MON_THU)


def _longest_run(week: WeekDraft) -> Session | None:
    runs = [s for s in week.sessions if s.sport == "run" and s.target_duration_s]
    if not runs:
        return None
    return max(runs, key=lambda s: s.target_duration_s or 0)


# --- Conformance helper (used by test) -------------------------------------


def parse_decision_rules_md(path: Path) -> list[tuple[str, str, str]]:
    """Return (rule_id, name, severity) tuples from decision-rules.md.

    Looks for headings like ``### R-5 Active injury flag (HARD)``.
    """
    import re

    pattern = re.compile(
        r"^###\s+(R-\d+)\s+(.+?)\s+\((HARD|SOFT|WATCH)\)\s*$",
        flags=re.MULTILINE,
    )
    text = path.read_text(encoding="utf-8")
    return [(m.group(1), m.group(2).strip(), m.group(3)) for m in pattern.finditer(text)]


__all__ = [
    "InjuryFlag",
    "RaceInWeek",
    "RulesContext",
    "Session",
    "Severity",
    "Topic",
    "Violation",
    "WeekDraft",
    "parse_decision_rules_md",
    "register_rule",
    "registered_rule_ids",
    "registry_entry",
    "validate_week",
]
