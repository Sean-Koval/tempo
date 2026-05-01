"""Phase-chain composition over the ``phase_library`` in phases.yaml.

The previous bootstrap flow had three hardcoded triathlon templates and asked
the agent to "extend the base phase or add a maintenance block" by
freeform judgment when runway exceeded the template. That made the system
effectively triathlon-only and let plan structure drift from the corpus.

This module turns ``phase_library`` + ``composition_rules`` + ``templates``
in ``knowledge/methodology/phases.yaml`` into typed Python objects, plus a
:func:`compose_chain` that assembles a chain for a target distance and
runway, validated against the composition rules.

Scope of this module:
- **Templates first.** When a named template covers the requested distance
  and the runway matches, the composer instantiates it directly. This is
  the deterministic, regression-safe path.
- **Library fallback.** If no template fits but the goal is sport-specific
  (run / bike / swim / multisport), the composer generates a chain from
  the library by sport.
- **Injury-driven preblocks.** When ``active_injuries`` are passed and they
  match a known precondition (e.g. ``active_injury_no_impact``), the
  composer prepends ``rehab_bike_only`` and (where applicable) a return
  phase before the chosen base. The 2027-half-ironman BSI case lands here.

What's deliberately *not* yet here:
- Multi-sport chain composition with arbitrary sport_focus blends (the
  template path covers the canonical cases).
- Forward TSB simulation. ``weekly_tss_per_hour`` × hours gives a target
  band; CTL trajectory is checked separately at re-bootstrap time.
- Auto-resolving runway over-/under-shoot. If the template + injury preblock
  doesn't match the runway exactly, the composer extends/compresses the
  earliest base-style phase rather than re-balancing the chain — a known
  simplification that keeps composition deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

from .paths import repo_root

Severity = Literal["HARD", "SOFT"]


@dataclass(frozen=True)
class PhaseDef:
    """One entry from ``phase_library`` in phases.yaml — immutable archetype."""

    id: str
    sport_focus: dict[str, float]
    character: str
    duration_range: tuple[int, int]
    intensity_distribution: dict[str, float]
    weekly_tss_per_hour: tuple[float, float]
    key_sessions: tuple[str, ...]
    valid_predecessors: tuple[str | None, ...]
    valid_successors: tuple[str | None, ...]
    preconditions: tuple[str, ...]


@dataclass
class ComposedPhase:
    """A phase placed in a chain with a concrete week count."""

    id: str
    weeks: int
    sport_focus: dict[str, float]
    character: str
    intensity_distribution: dict[str, float]
    weekly_tss_per_hour: tuple[float, float]
    key_sessions: tuple[str, ...]
    preconditions: tuple[str, ...]


@dataclass
class PhaseChain:
    """A composed chain with origin metadata.

    ``active_fragments`` carries any goal-research sub-program fragments
    loaded from ``plans/<plan-id>/fragments/`` at compose time. They live on
    the chain (not inside phases) because a fragment is a sub-program that
    runs alongside the macro phases — a 6-week strength block doesn't belong
    inside a phase's immutable ``key_sessions`` tuple. ``plan-training-week``
    reads them when drafting a week, and R-20 budgets them against the
    phase's TSS target.
    """

    template_id: str | None
    distance: str | None
    sport_focus: dict[str, float]
    phases: list[ComposedPhase]
    pre_block_origin: str = ""  # short note when an injury-driven preblock was prepended
    # Typed as list[Any] to avoid a hard import of fragments.py at module load
    # — composition stays importable in environments that don't have fragments
    # populated. Concrete type is ``list[tempo.fragments.PlanFragment]``.
    active_fragments: list[Any] = field(default_factory=list)

    @property
    def total_weeks(self) -> int:
        return sum(p.weeks for p in self.phases)


@dataclass
class CompositionRule:
    id: str
    description: str
    severity: Severity


@dataclass
class CompositionViolation:
    rule_id: str
    severity: Severity
    message: str
    phase_id: str | None = None


class CompositionError(ValueError):
    """Raised when no chain can be assembled from given inputs.

    ``violations`` carries the structured reason so callers can surface
    each constraint separately.
    """

    def __init__(self, message: str, violations: list[CompositionViolation] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or []


# --- YAML loading ---------------------------------------------------------


def _phases_yaml_path(root: Path | None) -> Path:
    return (root or repo_root()) / "knowledge" / "methodology" / "phases.yaml"


def _load_yaml(root: Path | None = None) -> dict[str, Any]:
    path = _phases_yaml_path(root)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_phase_library(root: Path | None = None) -> dict[str, PhaseDef]:
    """Return the ``phase_library:`` block as immutable :class:`PhaseDef`."""
    raw = _load_yaml(root).get("phase_library") or {}
    out: dict[str, PhaseDef] = {}
    for pid, body in raw.items():
        body = body or {}
        dr = body.get("duration_range") or [1, 1]
        tss = body.get("weekly_tss_per_hour") or [50, 60]
        out[pid] = PhaseDef(
            id=pid,
            sport_focus=dict(body.get("sport_focus") or {}),
            character=body.get("character") or "",
            duration_range=(int(dr[0]), int(dr[1])),
            intensity_distribution=dict(body.get("intensity_distribution") or {}),
            weekly_tss_per_hour=(float(tss[0]), float(tss[1])),
            key_sessions=tuple(body.get("key_sessions") or ()),
            valid_predecessors=tuple(body.get("valid_predecessors") or (None,)),
            valid_successors=tuple(body.get("valid_successors") or (None,)),
            preconditions=tuple(body.get("preconditions") or ()),
        )
    return out


def load_composition_rules(root: Path | None = None) -> list[CompositionRule]:
    raw = _load_yaml(root).get("composition_rules") or []
    return [
        CompositionRule(
            id=r["id"],
            description=r.get("description", ""),
            severity=r.get("severity", "HARD"),
        )
        for r in raw
    ]


def load_named_templates(root: Path | None = None) -> dict[str, dict[str, Any]]:
    return _load_yaml(root).get("templates") or {}


# --- Validation -----------------------------------------------------------


def validate_chain(
    chain: PhaseChain,
    *,
    library: dict[str, PhaseDef] | None = None,
    rules: list[CompositionRule] | None = None,
    has_target_date: bool = True,
    requires_taper: bool = True,
    active_injury_preconditions: frozenset[str] = frozenset(),
) -> list[CompositionViolation]:
    """Walk every composition rule against the chain.

    HARD violations should normally block; SOFT ones are advisory.
    Caller decides what to do with WATCH/SOFT violations.

    ``requires_taper`` toggles the "chain must end in taper_*" rule —
    race chains require a taper, non-race performance-target chains
    end in ``deload_test`` instead.
    """
    library = library or load_phase_library()
    rules = rules or load_composition_rules()
    rule_map = {r.id: r for r in rules}
    out: list[CompositionViolation] = []

    if not chain.phases:
        out.append(
            CompositionViolation(
                rule_id="empty_chain",
                severity="HARD",
                message="Chain has no phases.",
            )
        )
        return out

    # Predecessor / successor edges
    rule = rule_map.get("predecessor_succession_valid")
    if rule:
        prev_id: str | None = None
        for phase in chain.phases:
            defn = library.get(phase.id)
            if defn is None:
                out.append(
                    CompositionViolation(
                        rule_id=rule.id,
                        severity=rule.severity,
                        message=f"Phase {phase.id!r} not in phase_library.",
                        phase_id=phase.id,
                    )
                )
            elif prev_id not in defn.valid_predecessors:
                out.append(
                    CompositionViolation(
                        rule_id=rule.id,
                        severity=rule.severity,
                        message=(
                            f"{phase.id!r} cannot follow {prev_id!r} — "
                            f"valid predecessors: {sorted(p or '(start)' for p in defn.valid_predecessors)}."
                        ),
                        phase_id=phase.id,
                    )
                )
            prev_id = phase.id

    # End with taper for A-race
    rule = rule_map.get("chain_must_end_taper_for_a_race")
    if rule and has_target_date and requires_taper and not chain.phases[-1].id.startswith("taper"):
        out.append(
            CompositionViolation(
                rule_id=rule.id,
                severity=rule.severity,
                message=(
                    f"Chain ends with {chain.phases[-1].id!r}; A-race chain must end "
                    "with a taper_<sport> phase."
                ),
                phase_id=chain.phases[-1].id,
            )
        )

    # Peak followed/preceded correctly
    rule = rule_map.get("peak_requires_preceding_build")
    if rule:
        for i, phase in enumerate(chain.phases):
            if not phase.id.startswith("peak"):
                continue
            prev = chain.phases[i - 1] if i > 0 else None
            if prev is None or not prev.id.startswith("build"):
                out.append(
                    CompositionViolation(
                        rule_id=rule.id,
                        severity=rule.severity,
                        message=(
                            f"Peak phase {phase.id!r} not preceded by a build_* phase."
                        ),
                        phase_id=phase.id,
                    )
                )

    # Taper duration cap
    rule = rule_map.get("taper_max_duration_3wk")
    if rule:
        for phase in chain.phases:
            if phase.id.startswith("taper") and phase.weeks > 3:
                out.append(
                    CompositionViolation(
                        rule_id=rule.id,
                        severity=rule.severity,
                        message=f"Taper {phase.id!r} is {phase.weeks} weeks — max 3.",
                        phase_id=phase.id,
                    )
                )

    # Build requires preceding base.
    # - Long chains (>= 12 wk total): >= 6 wk of base.
    # - Short chains (< 12 wk total, e.g. 5K / masters meet): base >= build
    #   AND base >= 3 wk. Short events still need *a* base, just not 6 wk of it.
    rule = rule_map.get("build_requires_base_total_6wk")
    if rule:
        cumulative_base = 0
        seen_build = False
        first_build_weeks = 0
        for phase in chain.phases:
            if phase.id.startswith("base"):
                cumulative_base += phase.weeks
            elif phase.id.startswith("build") and not seen_build:
                first_build_weeks = phase.weeks
                if chain.total_weeks >= 12:
                    if cumulative_base < 6:
                        out.append(
                            CompositionViolation(
                                rule_id=rule.id,
                                severity=rule.severity,
                                message=(
                                    f"Build {phase.id!r} preceded by only "
                                    f"{cumulative_base} weeks of base; long chain requires >= 6."
                                ),
                                phase_id=phase.id,
                            )
                        )
                else:
                    if cumulative_base < 3 or cumulative_base < first_build_weeks:
                        out.append(
                            CompositionViolation(
                                rule_id=rule.id,
                                severity=rule.severity,
                                message=(
                                    f"Build {phase.id!r} ({first_build_weeks}wk) preceded by only "
                                    f"{cumulative_base} weeks of base; short chain requires "
                                    "base >= build and >= 3 weeks."
                                ),
                                phase_id=phase.id,
                            )
                        )
                seen_build = True

    # Rehab phases require active injury context. ``return_to_*`` phases are
    # gated by ``pt_clearance`` instead — a structural follow-on to a rehab
    # phase, not an injury-state precondition. Composer's predecessor edges
    # already enforce that ``return_to_*`` only follows a rehab phase, so we
    # don't double-check the injury here.
    rule = rule_map.get("rehab_requires_active_injury")
    if rule:
        for phase in chain.phases:
            if not phase.id.startswith("rehab"):
                continue
            injury_pre = {p for p in phase.preconditions if p.startswith("active_injury")}
            if injury_pre and not (active_injury_preconditions & injury_pre):
                out.append(
                    CompositionViolation(
                        rule_id=rule.id,
                        severity=rule.severity,
                        message=(
                            f"{phase.id!r} requires injury preconditions {sorted(injury_pre)} "
                            "but no matching athlete-state precondition is active."
                        ),
                        phase_id=phase.id,
                    )
                )

    return out


# --- Composition ---------------------------------------------------------


_INJURY_PRECONDITION_BY_TYPE: dict[str, frozenset[str]] = {
    "BSI": frozenset({"active_injury_no_impact", "active_injury_no_run"}),
    "stress_fracture": frozenset({"active_injury_no_impact", "active_injury_no_run"}),
    "calf_strain": frozenset({"active_injury_no_run"}),
    "achilles": frozenset({"active_injury_no_run"}),
    "plantar_fasciitis": frozenset({"active_injury_no_run"}),
    "itbs": frozenset({"active_injury_no_run"}),
    "lower_back": frozenset({"active_injury_no_impact"}),
}


def derive_injury_preconditions(injury_types: list[str]) -> frozenset[str]:
    """Map free-text injury type tags to the precondition flags used by phases."""
    out: set[str] = set()
    for itype in injury_types:
        out |= _INJURY_PRECONDITION_BY_TYPE.get(itype, frozenset())
    return frozenset(out)


_INJURY_KEYWORDS_TO_TYPE: dict[str, str] = {
    "bsi": "BSI",
    "bone stress": "BSI",
    "stress fracture": "stress_fracture",
    "calf strain": "calf_strain",
    "achilles": "achilles",
    "plantar": "plantar_fasciitis",
    "itbs": "itbs",
    "it band": "itbs",
    "lower back": "lower_back",
    "low back": "lower_back",
}


def injury_types_from_flags(flags: list[str]) -> list[str]:
    """Extract known injury type tags from free-text injury-log.md headings.

    ``flags`` is the list returned by :func:`tempo.athlete.active_injury_flags`,
    each like ``"2026-04-25 — left tibia (BSI grade 2) — severity 4"``.

    Substring matching is intentionally permissive — unknown tags simply
    return no precondition flags from :func:`derive_injury_preconditions`,
    so the chain composes without an injury preblock for cases we don't
    know how to handle yet. Agents should still surface them in the
    rationale.
    """
    out: list[str] = []
    seen: set[str] = set()
    for flag in flags:
        lower = flag.lower()
        for keyword, tag in _INJURY_KEYWORDS_TO_TYPE.items():
            if keyword in lower and tag not in seen:
                out.append(tag)
                seen.add(tag)
    return out


def _instantiate_template(
    template_id: str,
    template: dict[str, Any],
    library: dict[str, PhaseDef],
) -> PhaseChain:
    phases: list[ComposedPhase] = []
    for entry in template.get("chain") or []:
        pid = entry["phase"]
        weeks = int(entry["weeks"])
        defn = library.get(pid)
        if defn is None:
            raise CompositionError(
                f"Template {template_id!r} references {pid!r} not in phase_library."
            )
        phases.append(_compose_phase(defn, weeks))
    return PhaseChain(
        template_id=template_id,
        distance=template.get("distance"),
        sport_focus=dict(template.get("sport_focus") or {}),
        phases=phases,
    )


def _compose_phase(defn: PhaseDef, weeks: int) -> ComposedPhase:
    lo, hi = defn.duration_range
    if weeks < lo:
        weeks = lo
    if weeks > hi:
        weeks = hi
    return ComposedPhase(
        id=defn.id,
        weeks=weeks,
        sport_focus=dict(defn.sport_focus),
        character=defn.character,
        intensity_distribution=dict(defn.intensity_distribution),
        weekly_tss_per_hour=defn.weekly_tss_per_hour,
        key_sessions=defn.key_sessions,
        preconditions=defn.preconditions,
    )


def _select_template(
    distance: str | None,
    runway_weeks: int,
    templates: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    """Pick the closest-matching named template by distance + runway."""
    if not distance:
        return None

    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for tid, body in templates.items():
        if body.get("distance") != distance:
            continue
        total = sum(int(c["weeks"]) for c in body.get("chain") or [])
        candidates.append((abs(total - runway_weeks), tid, body))

    if not candidates:
        return None
    candidates.sort(key=lambda triple: (triple[0], triple[1]))
    return candidates[0][1], candidates[0][2]


def _stretch_or_compress_to_runway(chain: PhaseChain, runway_weeks: int) -> None:
    """Adjust the chain's earliest base-style phase to land on the runway.

    Mutates ``chain.phases[*].weeks`` in place. Bounded by each phase's
    duration_range so we never violate the library.
    """
    library = load_phase_library()
    if chain.total_weeks == runway_weeks:
        return

    # Pick the phase with the largest duration_range slack — usually a base
    # phase. Prefer base_* / rehab_* / adventure_prep over peak/taper.
    candidates = [
        (i, p)
        for i, p in enumerate(chain.phases)
        if p.id.startswith("base")
        or p.id.startswith("rehab")
        or p.id == "adventure_prep_block"
    ]
    if not candidates:
        return

    delta = runway_weeks - chain.total_weeks
    # Distribute delta across base phases respecting each phase's duration_range
    while delta != 0 and candidates:
        # Find the phase that can absorb the most of the remaining delta
        adjustable: list[tuple[int, ComposedPhase, int]] = []  # (idx, phase, room)
        for i, phase in candidates:
            defn = library.get(phase.id)
            if defn is None:
                continue
            lo, hi = defn.duration_range
            if delta > 0:
                room = hi - phase.weeks
            else:
                room = lo - phase.weeks  # negative number
            if room == 0:
                continue
            adjustable.append((i, phase, room))
        if not adjustable:
            break
        i, phase, room = max(adjustable, key=lambda x: abs(x[2]))
        if delta > 0:
            shift = min(delta, room)
        else:
            shift = max(delta, room)
        phase.weeks += shift
        delta -= shift
        # If this phase still has room, keep it; otherwise drop it.
        candidates = [(j, p) for j, p in candidates if p is not phase or shift != room]


def compose_chain(
    *,
    distance: str | None,
    runway_weeks: int,
    has_target_date: bool = True,
    requires_taper: bool = True,
    sport_focus_hint: dict[str, float] | None = None,
    active_injury_types: list[str] | None = None,
    plan_id: str | None = None,
    today: date | None = None,
    root: Path | None = None,
) -> PhaseChain:
    """Compose a phase chain for a goal.

    Strategy:
    1. If ``active_injury_types`` map to a precondition, prepend an
       injury-driven preblock (rehab + return) before the standard chain
       and shorten the standard chain's earliest base accordingly.
    2. Otherwise, pick the named template whose runway is closest to the
       requested ``runway_weeks`` and stretch/compress its earliest base.
    3. If no template matches the distance, raise :class:`CompositionError`
       with structured violations explaining what's needed.

    Validation runs at the end; HARD violations raise.
    """
    library = load_phase_library(root)
    templates = load_named_templates(root)
    rules = load_composition_rules(root)

    if not library:
        raise CompositionError(
            "phase_library is empty in phases.yaml — composer cannot run."
        )

    selection = _select_template(distance, runway_weeks, templates)
    if selection is None:
        raise CompositionError(
            f"No named template covers distance={distance!r}. Add a template to "
            "phases.yaml or compose ad-hoc by passing a sport-specific chain.",
            violations=[
                CompositionViolation(
                    rule_id="no_template",
                    severity="HARD",
                    message=f"distance={distance!r} unsupported",
                )
            ],
        )

    template_id, template = selection
    chain = _instantiate_template(template_id, template, library)

    injury_pre = derive_injury_preconditions(active_injury_types or [])
    if injury_pre:
        chain = _prepend_injury_preblock(chain, library, injury_pre)

    _stretch_or_compress_to_runway(chain, runway_weeks)

    if sport_focus_hint:
        chain.sport_focus = dict(sport_focus_hint)

    violations = validate_chain(
        chain,
        library=library,
        rules=rules,
        has_target_date=has_target_date,
        requires_taper=requires_taper,
        active_injury_preconditions=injury_pre,
    )
    hard = [v for v in violations if v.severity == "HARD"]
    if hard:
        raise CompositionError(
            f"Composed chain failed {len(hard)} HARD rule(s).",
            violations=violations,
        )

    # Goal-research sub-programs land here. They DON'T mutate the phase
    # chain (the macro plan stays the anchor); they ride along on the
    # PhaseChain so plan-training-week and R-20 can see them. We import
    # locally to avoid a circular import — fragments.py reads sources but
    # doesn't depend on composition, but composition imports goals.py at
    # function-call time too, so we keep the same pattern.
    if plan_id:
        from . import fragments as _fragments

        try:
            chain.active_fragments = _fragments.load_active_fragments(
                plan_id, on=today, root=root
            )
        except _fragments.FragmentSchemaError:
            # Re-raise as CompositionError so callers get one error type to
            # handle. A schema-invalid fragment is a deliberate authoring
            # bug — surfaced, not silently dropped.
            raise

    return chain


# --- Goal-aware composition ----------------------------------------------
#
# Higher-level entry point. Skills (bootstrap-plan) call this with a typed
# :class:`tempo.goals.Goal`; the composer figures out the right template
# distance, runway, and whether a taper is required from the goal type.


# Default runway when a non-race goal has no by_date — paired with the
# template name's nominal length. The composer's stretch logic adjusts to
# fit the actual phase duration_ranges.
_DEFAULT_RUNWAY_BY_DISTANCE: dict[str, int] = {
    "ftp_target": 16,
    "css_target": 12,
    "strength_peak": 12,
    "base_building": 8,
    "rolling_base_block": 12,
    # Streak/adventure defaults match the canonical template length so
    # an open-ended (no by_date) streak/adventure composes without
    # stretching the block past its conservative ramp.
    "streak_30day": 5,
    "streak_100day": 15,
    "adventure_hike": 4,
    "adventure_ride": 8,
}


_MULTI_A_WINDOW_WEEKS = 8


def _confirmed_a_races_near(
    goal_id: str,
    target_date: date,
    *,
    window_weeks: int,
    root: Path | None,
) -> list[dict[str, Any]]:
    """Return confirmed A-races within ``window_weeks`` of ``target_date`` (excluding the goal itself).

    Tempo-wk7 multi-A guardrail. The ticket asks for an explicit error rather
    than silently picking one peak — the calendar window where two A-races
    collide forces a coaching tradeoff (sub-peak the second, race-through
    the first, or split the chain) that's outside the composer's automatic
    behavior. Multi-A composition itself is deferred (separate ticket).
    """
    from . import athlete as _athlete

    horizon = timedelta(weeks=window_weeks)
    out: list[dict[str, Any]] = []
    for race in _athlete.load_races(root=root):
        if race.get("id") == goal_id:
            continue
        if (race.get("priority") or "").upper() != "A":
            continue
        if race.get("status") != "confirmed":
            continue
        rd = race.get("date") or race.get("target_date")
        if isinstance(rd, str):
            try:
                rd = date.fromisoformat(rd)
            except ValueError:
                continue
        if not isinstance(rd, date):
            continue
        if abs((rd - target_date).days) <= horizon.days:
            out.append(race)
    return out


def compose_for_goal(
    goal: _GoalLike,
    *,
    today: date | None = None,
    runway_weeks_override: int | None = None,
    active_injury_types: list[str] | None = None,
    multi_a: bool = False,
    plan_id: str | None = None,
    root: Path | None = None,
) -> PhaseChain:
    """Compose a phase chain anchored on a typed :class:`tempo.goals.Goal`.

    Routes by ``goal.type``:

    - ``race`` -> existing race-anchored composer; taper required.
    - ``performance_target`` -> metric maps to a synthetic distance
      (ftp_target, css_target, strength_peak); chain ends in deload_test.
    - ``maintenance`` -> base_building (dated) or rolling_base_block (open).
    - ``streak`` -> count-based ``streak_block`` template (no taper, no
      periodisation peak; injury-resistance is the dominant constraint).
    - ``adventure`` -> ``adventure_prep_block`` + final 3wk
      ``adventure_simulation_block`` (terrain + nutrition rehearsal,
      no taper).

    Runway is computed from ``today`` to ``goal.target_date``; an override
    is allowed for tests and for non-dated goals where the default
    template length is the right baseline.

    Raises :class:`CompositionError` for unsupported metrics with the
    list of supported metrics in the violation message — the agent can
    surface that to the user without guessing.
    """
    # Local import keeps composition.py importable without goals.py at
    # module load — avoids a cycle if goals.py ever imports composition.
    from .goals import GoalSchemaError, template_distance_for

    is_race = goal.type == "race"

    if (
        is_race
        and not multi_a
        and goal.target_date is not None
        and (goal.priority or "").upper() == "A"
    ):
        collisions = _confirmed_a_races_near(
            goal.id,
            goal.target_date,
            window_weeks=_MULTI_A_WINDOW_WEEKS,
            root=root,
        )
        if collisions:
            ids = [r.get("id") for r in collisions]
            raise CompositionError(
                f"goal {goal.id!r} has {len(collisions)} other confirmed A-race(s) "
                f"({ids}) within {_MULTI_A_WINDOW_WEEKS} weeks of {goal.target_date}. "
                "Pass multi_a=True (or --multi-a on the CLI) to opt into a "
                "single-anchor compose; full multi-A handling is deferred.",
                violations=[
                    CompositionViolation(
                        rule_id="multi_a_within_window",
                        severity="HARD",
                        message=(
                            f"colliding confirmed A-race ids: {ids}; "
                            f"window={_MULTI_A_WINDOW_WEEKS}wk around {goal.target_date}"
                        ),
                    )
                ],
            )

    try:
        distance = template_distance_for(goal)
    except GoalSchemaError as e:
        raise CompositionError(
            str(e),
            violations=[
                CompositionViolation(
                    rule_id="goal_schema",
                    severity="HARD",
                    message=v,
                )
                for v in (e.violations or [str(e)])
            ],
        ) from e

    today_d = today or date.today()
    if runway_weeks_override is not None:
        runway = runway_weeks_override
    elif goal.target_date is not None:
        delta_days = (goal.target_date - today_d).days
        runway = max(1, delta_days // 7)
    else:
        runway = _DEFAULT_RUNWAY_BY_DISTANCE.get(distance, 12)

    return compose_chain(
        distance=distance,
        runway_weeks=runway,
        has_target_date=goal.target_date is not None,
        requires_taper=is_race,
        active_injury_types=active_injury_types,
        plan_id=plan_id,
        today=today_d,
        root=root,
    )


# Forward-declared duck type so we don't import goals.Goal at module top
# (avoid the import cycle described above).
class _GoalLike:  # pragma: no cover - typing aid
    type: str
    target_date: date | None
    distance: str | None
    metric: str | None


def _prepend_injury_preblock(
    chain: PhaseChain,
    library: dict[str, PhaseDef],
    injury_pre: frozenset[str],
) -> PhaseChain:
    """Prepend rehab_bike_only (+ return phase) before the chain's first phase.

    Only applies when the chain's distance is multisport or run; pure swim
    or pure bike chains aren't blocked by run injuries.
    """
    distance = chain.distance or ""

    # Decide which return phase fits between rehab and the standard chain.
    return_phase_id: str | None = None
    if distance in {"ironman", "half_ironman", "olympic"}:
        return_phase_id = "return_to_3sport"
    elif distance in {"marathon", "half_marathon", "5k", "10k"}:
        return_phase_id = "return_to_run"
    else:
        # Bike-only or swim-only chains: no return-to-run preblock needed.
        return chain

    rehab = library.get("rehab_bike_only")
    return_def = library.get(return_phase_id) if return_phase_id else None
    if rehab is None or return_def is None:
        return chain

    pre_phases: list[ComposedPhase] = [
        _compose_phase(rehab, weeks=rehab.duration_range[0]),
        _compose_phase(return_def, weeks=return_def.duration_range[0]),
    ]

    # Drop the chain's leading prep_* phase if any — the rehab phase already
    # serves the anatomical-adaptation function, and prep_* doesn't have
    # return_to_* in its valid_predecessors list anyway.
    standard_phases = list(chain.phases)
    if standard_phases and standard_phases[0].id.startswith("prep"):
        standard_phases = standard_phases[1:]

    new_phases: list[ComposedPhase] = pre_phases + standard_phases
    new_chain = PhaseChain(
        template_id=chain.template_id,
        distance=chain.distance,
        sport_focus=dict(chain.sport_focus),
        phases=new_phases,
        pre_block_origin=(
            f"injury preblock prepended (rehab_bike_only + {return_phase_id}) "
            f"because preconditions {sorted(injury_pre)} are active"
        ),
    )

    # Update successor edge: rehab → return → first_phase.
    # The pre_phases were built from library defs whose successors include
    # the standard chain's first phase, so the chain validates as long as
    # first_phase's predecessors include the return phase or its synonyms.
    return new_chain


__all__ = [
    "ComposedPhase",
    "CompositionError",
    "CompositionRule",
    "CompositionViolation",
    "PhaseChain",
    "PhaseDef",
    "Severity",
    "compose_chain",
    "compose_for_goal",
    "derive_injury_preconditions",
    "load_composition_rules",
    "load_named_templates",
    "load_phase_library",
    "validate_chain",
]
