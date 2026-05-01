"""Plan-fragment loading + validation for goal-research sub-programs.

A plan-fragment is a time-boxed, goal-driven sub-program that lives alongside
the macro plan rather than inside it. Two examples Sean called out:

- "Build stronger legs for cycling, 8 weeks, 2 lifts/week" — a training
  fragment slotting two strength sessions/week into the existing plan.
- "Cut 4kg before the 70.3, 6 weeks" — a nutrition fragment with daily
  fueling windows that shape sessions but don't add new ones.

Why fragments live in their own files (and not inside ``plan.yaml``):

- The macro plan is the anchor. A 6-week strength block shouldn't trigger a
  full plan re-bootstrap to insert.
- Fragments sunset by design — every fragment carries
  ``re_evaluate_after``. After that date, the loader treats it as inactive
  and the composer ignores it. Persistent sub-programs are an anti-pattern;
  if Sean wants the strength work to continue, he re-runs ``/goal-research``
  and gets a new fragment with a fresh re-evaluation date.
- Diff review survives. ``plans/<plan-id>/fragments/`` shows up in
  ``git diff`` like everything else under ``plans/``.

The composer's R-20 (active_subprogram_capacity) reads fragments through
this module — it doesn't reach into ``plans/`` directly. That keeps the
"what is an active fragment" decision in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml

from .paths import repo_root

FragmentKind = Literal["training", "nutrition"]


class FragmentSchemaError(ValueError):
    """Raised when a fragment YAML doesn't validate against the schema.

    ``violations`` carries the specific failures so callers can surface
    each one rather than a single opaque message.
    """

    def __init__(self, message: str, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or [message]


@dataclass(frozen=True)
class FragmentSession:
    """One archetype-referenced session inside a training fragment.

    ``archetype`` MUST resolve to an entry in
    ``knowledge/methodology/session-library/`` — the schema check enforces
    this at load time so a fragment can't reference a session that doesn't
    exist.

    ``slot_preference`` is advisory ordering for ``plan-training-week`` —
    e.g. ``["tuesday", "friday"]`` for a 2x/week strength fragment. The
    weekly composer is free to ignore the preference if a hard constraint
    (R-5 injury, R-11 back-to-back hard) overrides.
    """

    archetype: str
    cadence_per_week: int
    slot_preference: tuple[str, ...] = ()
    target_tss: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class NutritionWindow:
    """One nutrition window inside a diet fragment.

    Schedule is free-form (e.g. ``"daily"``, ``"workout days"``,
    ``"long-ride days"``) — the agent reads it during weekly planning.
    """

    label: str
    schedule: str
    macros: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class PlanFragment:
    """A loaded, validated fragment.

    Either ``sessions`` (training kind) or ``nutrition_windows`` (nutrition
    kind) is populated — never both. The schema check enforces that.
    """

    fragment_id: str
    goal: str
    kind: FragmentKind
    created_at: date
    re_evaluate_after: date
    duration_weeks: int
    sessions: tuple[FragmentSession, ...] = ()
    nutrition_windows: tuple[NutritionWindow, ...] = ()
    research_refs: tuple[str, ...] = ()
    rationale: str = ""
    source_path: Path | None = None

    def is_active(self, on: date) -> bool:
        """Active when ``created_at <= on <= re_evaluate_after``.

        The re-evaluation date is inclusive: a fragment with
        ``re_evaluate_after: 2026-06-30`` is active on 2026-06-30 itself,
        which is the day Sean is supposed to re-run ``/goal-research``.
        """
        return self.created_at <= on <= self.re_evaluate_after

    def estimated_weekly_tss(self) -> float:
        """Sum of (cadence × per-session TSS) across the fragment's sessions.

        Sessions without a ``target_tss`` contribute 0 — the fragment author
        should set TSS for any session that materially changes the budget.
        Nutrition fragments return 0 (no training load contribution).
        """
        total = 0.0
        for s in self.sessions:
            if s.target_tss is None:
                continue
            total += float(s.target_tss) * max(0, int(s.cadence_per_week))
        return total


# --- Loading --------------------------------------------------------------


def fragments_dir(plan_id: str, *, root: Path | None = None) -> Path:
    """``plans/<plan_id>/fragments/`` — does not create the directory."""
    return (root or repo_root()) / "plans" / plan_id / "fragments"


def load_fragment(path: Path) -> PlanFragment:
    """Parse and validate a single fragment YAML.

    Raises :class:`FragmentSchemaError` with all schema violations gathered
    into one exception — callers can show the user the full list rather than
    fixing one error at a time.
    """
    if not path.is_file():
        raise FragmentSchemaError(f"fragment not found: {path}")
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    if not isinstance(doc, dict):
        raise FragmentSchemaError(f"fragment {path}: top-level must be a mapping")

    violations: list[str] = []

    fid = doc.get("fragment_id")
    if not isinstance(fid, str) or not fid:
        violations.append("missing or non-string fragment_id")

    goal = doc.get("goal")
    if not isinstance(goal, str) or not goal:
        violations.append("missing or non-string goal")

    kind = doc.get("kind") or "training"
    if kind not in ("training", "nutrition"):
        violations.append(f"kind must be 'training' or 'nutrition'; got {kind!r}")

    created = _parse_date(doc.get("created_at"), "created_at", violations)
    re_eval = _parse_date(doc.get("re_evaluate_after"), "re_evaluate_after", violations)

    duration = doc.get("duration_weeks")
    if not isinstance(duration, int) or duration <= 0:
        violations.append(f"duration_weeks must be a positive int; got {duration!r}")

    if created is not None and re_eval is not None and re_eval <= created:
        violations.append(
            f"re_evaluate_after ({re_eval}) must be after created_at ({created})"
        )

    sessions_raw = doc.get("sessions") or []
    nutrition_raw = doc.get("nutrition_windows") or []

    if kind == "training":
        if not sessions_raw:
            violations.append("training fragment must declare at least one session")
        if nutrition_raw:
            violations.append(
                "training fragment must not declare nutrition_windows; split into a separate fragment"
            )
    else:  # nutrition
        if not nutrition_raw:
            violations.append(
                "nutrition fragment must declare at least one nutrition_window"
            )
        if sessions_raw:
            violations.append(
                "nutrition fragment must not declare sessions; split into a separate fragment"
            )

    sessions = tuple(_parse_session(s, violations) for s in sessions_raw)
    windows = tuple(_parse_nutrition_window(n, violations) for n in nutrition_raw)

    research_refs = doc.get("research_refs") or []
    if not isinstance(research_refs, list) or not all(
        isinstance(x, str) for x in research_refs
    ):
        violations.append("research_refs must be a list of strings")
        research_refs = []

    if violations:
        raise FragmentSchemaError(
            f"fragment {path}: {len(violations)} schema violation(s)",
            violations=violations,
        )

    return PlanFragment(
        fragment_id=str(fid),
        goal=str(goal),
        kind=kind,  # type: ignore[arg-type]
        created_at=created,  # type: ignore[arg-type]
        re_evaluate_after=re_eval,  # type: ignore[arg-type]
        duration_weeks=int(duration),  # type: ignore[arg-type]
        sessions=sessions,
        nutrition_windows=windows,
        research_refs=tuple(str(r) for r in research_refs),
        rationale=str(doc.get("rationale") or ""),
        source_path=path,
    )


def load_active_fragments(
    plan_id: str,
    *,
    on: date | None = None,
    root: Path | None = None,
    archetype_check: bool = True,
) -> list[PlanFragment]:
    """Return every fragment in ``plans/<plan_id>/fragments/`` active on ``on``.

    Inactive fragments (``on > re_evaluate_after`` or ``on < created_at``)
    are silently skipped — the lifecycle is the whole point.

    Schema-invalid fragments are NOT silently skipped. They raise — a
    malformed fragment in a Sean-authored plans/ tree is a bug we want
    surfaced, not hidden. Callers can catch :class:`FragmentSchemaError`
    if they need partial loading.

    ``archetype_check`` is enabled by default and validates each training
    fragment's ``archetype`` against the session-library. Tests with
    custom archetypes can opt out.
    """
    on = on or date.today()
    fdir = fragments_dir(plan_id, root=root)
    if not fdir.is_dir():
        return []
    out: list[PlanFragment] = []
    for path in sorted(fdir.glob("*.yaml")):
        frag = load_fragment(path)
        if archetype_check and frag.kind == "training":
            unknown = unknown_archetypes(frag, root=root)
            if unknown:
                raise FragmentSchemaError(
                    f"fragment {path}: archetype(s) not in session-library: {sorted(unknown)}",
                    violations=[f"unknown archetype: {a}" for a in sorted(unknown)],
                )
        if frag.is_active(on):
            out.append(frag)
    return out


# --- Archetype validation -------------------------------------------------


def known_archetypes(*, root: Path | None = None) -> set[str]:
    """Return every archetype id declared under
    ``knowledge/methodology/session-library/``.

    Walks per-sport markdown files and pulls headings of the form
    ``### `archetype_id``` — same shape ``embed.py`` indexes against. Falls
    back to the legacy monolithic file when the directory is absent.
    """
    import re

    base = (root or repo_root()) / "knowledge" / "methodology"
    lib_dir = base / "session-library"
    legacy = base / "session-library.md"

    files: list[Path] = []
    if lib_dir.is_dir():
        files = sorted(p for p in lib_dir.glob("*.md") if p.is_file())
    elif legacy.is_file():
        files = [legacy]

    pattern = re.compile(r"^###\s+`([a-z0-9_]+)`", re.MULTILINE)
    out: set[str] = set()
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        out.update(pattern.findall(text))
    return out


def unknown_archetypes(
    fragment: PlanFragment, *, root: Path | None = None
) -> set[str]:
    """Subset of fragment archetypes that aren't in the session-library."""
    if fragment.kind != "training":
        return set()
    known = known_archetypes(root=root)
    return {s.archetype for s in fragment.sessions} - known


# --- Helpers --------------------------------------------------------------


def _parse_date(value: Any, field_name: str, violations: list[str]) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            violations.append(f"{field_name} {value!r} is not ISO YYYY-MM-DD")
            return None
    violations.append(f"{field_name} missing or wrong type")
    return None


def _parse_session(raw: Any, violations: list[str]) -> FragmentSession:
    if not isinstance(raw, dict):
        violations.append(f"session entry must be a mapping; got {type(raw).__name__}")
        return FragmentSession(archetype="(invalid)", cadence_per_week=0)
    archetype = raw.get("archetype")
    cadence = raw.get("cadence_per_week", raw.get("cadence", 1))
    if not isinstance(archetype, str) or not archetype:
        violations.append("session.archetype missing or non-string")
        archetype = "(invalid)"
    if not isinstance(cadence, int) or cadence <= 0:
        violations.append(
            f"session.cadence_per_week must be a positive int; got {cadence!r}"
        )
        cadence = 0
    slot_pref_raw = raw.get("slot_preference") or ()
    if not isinstance(slot_pref_raw, list | tuple) or not all(
        isinstance(x, str) for x in slot_pref_raw
    ):
        violations.append("session.slot_preference must be a list of strings")
        slot_pref_raw = ()
    target_tss = raw.get("target_tss")
    if target_tss is not None and not isinstance(target_tss, int | float):
        violations.append(f"session.target_tss must be numeric or null; got {target_tss!r}")
        target_tss = None
    return FragmentSession(
        archetype=str(archetype),
        cadence_per_week=int(cadence),
        slot_preference=tuple(str(x) for x in slot_pref_raw),
        target_tss=float(target_tss) if target_tss is not None else None,
        notes=str(raw.get("notes") or ""),
    )


def _parse_nutrition_window(raw: Any, violations: list[str]) -> NutritionWindow:
    if not isinstance(raw, dict):
        violations.append(
            f"nutrition_window entry must be a mapping; got {type(raw).__name__}"
        )
        return NutritionWindow(label="(invalid)", schedule="")
    label = raw.get("label")
    schedule = raw.get("schedule")
    if not isinstance(label, str) or not label:
        violations.append("nutrition_window.label missing or non-string")
        label = "(invalid)"
    if not isinstance(schedule, str) or not schedule:
        violations.append("nutrition_window.schedule missing or non-string")
        schedule = ""
    macros = raw.get("macros") or {}
    if not isinstance(macros, dict):
        violations.append("nutrition_window.macros must be a mapping")
        macros = {}
    return NutritionWindow(
        label=str(label),
        schedule=str(schedule),
        macros=dict(macros),
        notes=str(raw.get("notes") or ""),
    )


__all__ = [
    "FragmentKind",
    "FragmentSchemaError",
    "FragmentSession",
    "NutritionWindow",
    "PlanFragment",
    "fragments_dir",
    "known_archetypes",
    "load_active_fragments",
    "load_fragment",
    "unknown_archetypes",
]
