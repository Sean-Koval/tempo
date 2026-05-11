"""``coach push-week`` — write planned sessions to intervals.icu with
conflict detection + post-write verification.

Closes the trust gap surfaced in user story 04 §6:

1. **Pre-write conflict detection.** Manually-created intervals.icu events
   in slots we're about to write to are flagged. By default the user is
   prompted; ``--force-overwrite`` proceeds and logs the conflict;
   ``--dry-run`` only reports.

2. **Idempotent upsert.** External IDs are ``"<plan_id>/<session_id>"``
   so re-running an already-pushed week updates rather than duplicates.
   This is the same scheme as the ``bulk_upsert_tagged_events`` MCP tool;
   we replicate it here so the CLI doesn't have to round-trip the MCP.

3. **Post-write verification.** With ``--verify`` (default on), the week
   is re-fetched and each intended event is diffed against what landed.
   Mismatches exit non-zero and are reported field-by-field.

4. **Audit trail.** One JSONL line is appended to ``data/events.jsonl``
   for each push: ``{plan_id, week_id, planned_count, written_count,
   conflicts, mismatches}`` so a future-you can reconstruct what
   happened.

The previous CLI was dry-run-only as a Phase-1 stub. This module replaces
that with a real write path while keeping ``--dry-run`` available for
preview.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from intervals_icu_mcp.auth import ICUConfig, load_config
from intervals_icu_mcp.client import ICUClient
from intervals_icu_mcp.models import Event

from . import plans as _plans
from .events import log_event

TEMPO_TAG = "[tempo]"
_DEFAULT_HOUR_OF_DAY = "06:00:00"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedSession:
    """One row from ``coach.db.sessions_planned`` ready to push."""

    id: str
    plan_id: str
    date: str  # ISO YYYY-MM-DD
    sport: str | None = None
    library_ref: str | None = None
    target_tss: float | None = None
    target_duration_s: int | None = None
    purpose: str | None = None
    notes: str | None = None
    # Set when a mapping exists in library_workout_map for this session's
    # library_ref. When present, the event POST attaches it as
    # ``plan_workout_id`` so intervals.icu links the library workout instead
    # of relying on free-form description text.
    intervals_workout_id: int | None = None

    @property
    def external_id(self) -> str:
        return f"{self.plan_id}/{self.id}"

    def to_event_payload(self) -> dict[str, Any]:
        """Translate to the dict shape ``ICUClient.create_event`` accepts."""
        body: dict[str, Any] = {
            "start_date_local": f"{self.date}T{_DEFAULT_HOUR_OF_DAY}",
            "name": self._event_name(),
            "category": "WORKOUT",
            "external_id": self.external_id,
            "description": self._description(),
        }
        if self.sport:
            body["type"] = _intervals_event_type(self.sport)
        if self.target_duration_s:
            body["moving_time"] = int(self.target_duration_s)
        if self.target_tss is not None:
            body["icu_training_load"] = int(round(self.target_tss))
        if self.intervals_workout_id is not None:
            body["plan_workout_id"] = int(self.intervals_workout_id)
        return body

    def _event_name(self) -> str:
        if self.library_ref:
            base = self.library_ref.replace("_", " ")
        else:
            base = self.purpose or self.id
        return f"{base.strip()} [tempo]"

    def _description(self) -> str:
        prefix = f"{TEMPO_TAG} plan={self.plan_id} session={self.id}"
        if self.notes:
            return f"{prefix}\n\n{self.notes}"
        return prefix


@dataclass(slots=True)
class Conflict:
    """An existing intervals event that occupies a slot we'd write to.

    "Occupies" is permissive: same date + same primary sport. Tightening
    to same hour produces too many false negatives because intervals
    events are often timestamped 00:00 by default.
    """

    date: str
    intervals_event_id: int
    intervals_name: str | None
    intervals_external_id: str | None
    planned_session_id: str
    planned_name: str
    planned_external_id: str
    sport: str | None


@dataclass(slots=True)
class Mismatch:
    """A field-level disagreement between intended and post-write event."""

    session_id: str
    external_id: str
    field: str
    intended: Any
    actual: Any


@dataclass
class PushResult:
    """Outcome of a push (dry-run or applied)."""

    plan_id: str
    week_id: str
    planned_count: int = 0
    written_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    error_count: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    verified: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "week_id": self.week_id,
            "planned_count": self.planned_count,
            "written_count": self.written_count,
            "created_count": self.created_count,
            "updated_count": self.updated_count,
            "error_count": self.error_count,
            "conflict_count": len(self.conflicts),
            "mismatch_count": len(self.mismatches),
            "dry_run": self.dry_run,
            "verified": self.verified,
        }


class PushAborted(RuntimeError):
    """Caller signaled abort (e.g., refused to overwrite conflicts)."""


# ---------------------------------------------------------------------------
# DB read
# ---------------------------------------------------------------------------


def load_planned_sessions(conn: sqlite3.Connection, *, week_id: str) -> list[PlannedSession]:
    rows = conn.execute(
        """
        SELECT id, plan_id, date, sport, library_ref,
               target_tss, target_duration_s, purpose, notes
        FROM sessions_planned
        WHERE week_id = ?
        ORDER BY date
        """,
        (week_id,),
    ).fetchall()
    sessions = [PlannedSession(**dict(r)) for r in rows]
    refs = [s.library_ref for s in sessions if s.library_ref]
    if refs:
        from .library_map import lookup_workout_ids

        try:
            id_by_ref = lookup_workout_ids(conn, refs=refs)
        except sqlite3.OperationalError:
            # library_workout_map only exists post-schema-migration; older
            # databases predating tempo-d5e Track A keep the inline-description
            # fallback unchanged.
            id_by_ref = {}
        for s in sessions:
            if s.library_ref and s.library_ref in id_by_ref:
                s.intervals_workout_id = id_by_ref[s.library_ref]
    return sessions


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def detect_conflicts(
    *,
    existing_events: list[Event],
    planned: list[PlannedSession],
    plan_id: str,
) -> list[Conflict]:
    """Find existing events that overlap planned slots but aren't ours.

    "Ours" = ``external_id`` starting with ``"<plan_id>/"`` OR
    description prefixed with ``[tempo]``. Anything else in a planned
    date is treated as a manual event the user added on intervals.icu
    that push-week would otherwise clobber.
    """
    by_planned_date: dict[str, list[PlannedSession]] = {}
    for s in planned:
        by_planned_date.setdefault(s.date, []).append(s)

    conflicts: list[Conflict] = []
    for ev in existing_events:
        ev_date = (ev.start_date_local or "").split("T", 1)[0]
        if ev_date not in by_planned_date:
            continue
        if _event_belongs_to_plan(ev, plan_id):
            continue
        # Pair the conflict with the first planned session on that date —
        # which is what we'd be 'overwriting'.
        match = by_planned_date[ev_date][0]
        conflicts.append(
            Conflict(
                date=ev_date,
                intervals_event_id=ev.id,
                intervals_name=ev.name,
                intervals_external_id=ev.external_id,
                planned_session_id=match.id,
                planned_name=match._event_name(),
                planned_external_id=match.external_id,
                sport=match.sport,
            )
        )
    return conflicts


def _event_belongs_to_plan(ev: Event, plan_id: str) -> bool:
    if ev.external_id and ev.external_id.startswith(f"{plan_id}/"):
        return True
    if ev.description and ev.description.startswith(TEMPO_TAG):
        return True
    return False


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


_VERIFY_FIELDS = (
    ("start_date_local", lambda ev: (ev.start_date_local or "").split("T", 1)[0]),
    ("name", lambda ev: ev.name),
    ("type", lambda ev: ev.type),
    ("moving_time", lambda ev: ev.moving_time),
    ("icu_training_load", lambda ev: ev.icu_training_load),
    ("external_id", lambda ev: ev.external_id),
)


def verify_writes(
    *,
    intended: list[PlannedSession],
    actual_events: list[Event],
) -> list[Mismatch]:
    """Diff what we asked to land vs what intervals actually returns.

    The intended payload's ``start_date_local`` carries a fake
    ``T06:00:00`` because intervals normalizes to date-level for
    WORKOUT events; we compare on the date prefix only.
    """
    by_ext = {ev.external_id: ev for ev in actual_events if ev.external_id}
    mismatches: list[Mismatch] = []
    for plan in intended:
        ev = by_ext.get(plan.external_id)
        if ev is None:
            mismatches.append(
                Mismatch(
                    session_id=plan.id,
                    external_id=plan.external_id,
                    field="<event>",
                    intended="present",
                    actual="missing",
                )
            )
            continue
        intended_payload = plan.to_event_payload()
        for field_name, extractor in _VERIFY_FIELDS:
            intended_v = intended_payload.get(field_name)
            if field_name == "start_date_local":
                intended_v = (intended_v or "").split("T", 1)[0]
            actual_v = extractor(ev)
            if intended_v is None and actual_v is None:
                continue
            if intended_v != actual_v:
                mismatches.append(
                    Mismatch(
                        session_id=plan.id,
                        external_id=plan.external_id,
                        field=field_name,
                        intended=intended_v,
                        actual=actual_v,
                    )
                )
    return mismatches


# ---------------------------------------------------------------------------
# Upsert (mirrors bulk_upsert_tagged_events, but in Python)
# ---------------------------------------------------------------------------


async def _upsert_one(
    client: ICUClient,
    *,
    payload: dict[str, Any],
    existing: dict[str, int],
    external_id: str,
) -> tuple[Event, str]:
    if external_id in existing:
        ev = await client.update_event(existing[external_id], payload)
        return ev, "updated"
    ev = await client.create_event(payload)
    return ev, "created"


async def push_week_async(
    *,
    config: ICUConfig,
    plan_id: str,
    week_id: str,
    planned: list[PlannedSession],
    dry_run: bool = False,
    verify: bool = True,
    force_overwrite: bool = False,
    on_conflict_prompt=None,  # callable(conflicts) -> bool (proceed?)
    mark_pushed_conn: sqlite3.Connection | None = None,
) -> PushResult:
    """Push a week with conflict detection + verification.

    ``on_conflict_prompt`` is called only when conflicts are detected
    AND ``force_overwrite`` is False. It receives the conflict list and
    must return True (proceed) or False (abort). When ``None`` and
    conflicts exist, the function refuses unless ``force_overwrite``.

    ``mark_pushed_conn`` (optional): when set, after a successful upsert
    the matching ``sessions_planned`` rows get
    ``pushed_to_intervals = 1`` and ``intervals_event_id = <id>`` so
    subsequent runs and dashboards see the push state.
    """
    result = PushResult(
        plan_id=plan_id, week_id=week_id, planned_count=len(planned), dry_run=dry_run
    )
    if not planned:
        return result

    monday = _plans.week_start(week_id)
    sunday = monday + timedelta(days=6)

    async with ICUClient(config) as client:
        existing = await client.get_events(oldest=monday.isoformat(), newest=sunday.isoformat())

        result.conflicts = detect_conflicts(
            existing_events=existing, planned=planned, plan_id=plan_id
        )

        if result.conflicts and not force_overwrite:
            if dry_run:
                # Surface and stop — the dry-run user explicitly asked for preview.
                pass
            elif on_conflict_prompt is not None:
                if not on_conflict_prompt(result.conflicts):
                    raise PushAborted("conflicts present, push aborted by caller")
            else:
                raise PushAborted(
                    f"{len(result.conflicts)} non-Tempo event(s) in target slots; "
                    "pass --force-overwrite to proceed."
                )

        if dry_run:
            return result

        by_ext = {ev.external_id: ev.id for ev in existing if ev.external_id}
        pushed_ids: list[tuple[str, int]] = []

        for plan in planned:
            try:
                ev, kind = await _upsert_one(
                    client,
                    payload=plan.to_event_payload(),
                    existing=by_ext,
                    external_id=plan.external_id,
                )
                if kind == "created":
                    result.created_count += 1
                else:
                    result.updated_count += 1
                result.written_count += 1
                pushed_ids.append((plan.id, ev.id))
            except Exception as e:  # pragma: no cover - network surface
                result.error_count += 1
                result.errors.append(f"{plan.id}: {e!s}")

        if mark_pushed_conn is not None and pushed_ids:
            with mark_pushed_conn:
                for sess_id, ev_id in pushed_ids:
                    mark_pushed_conn.execute(
                        "UPDATE sessions_planned "
                        "SET pushed_to_intervals = 1, intervals_event_id = ? "
                        "WHERE id = ?",
                        (str(ev_id), sess_id),
                    )

        if verify and result.error_count == 0:
            after = await client.get_events(oldest=monday.isoformat(), newest=sunday.isoformat())
            result.mismatches = verify_writes(intended=planned, actual_events=after)
            result.verified = True

    return result


def push_week(
    *,
    plan_id: str,
    week_id: str,
    planned: list[PlannedSession],
    dry_run: bool = False,
    verify: bool = True,
    force_overwrite: bool = False,
    config: ICUConfig | None = None,
    on_conflict_prompt=None,
    mark_pushed_conn: sqlite3.Connection | None = None,
) -> PushResult:
    """Sync wrapper for :func:`push_week_async` that the CLI can call."""
    cfg = config or load_config()
    result = asyncio.run(
        push_week_async(
            config=cfg,
            plan_id=plan_id,
            week_id=week_id,
            planned=planned,
            dry_run=dry_run,
            verify=verify,
            force_overwrite=force_overwrite,
            on_conflict_prompt=on_conflict_prompt,
            mark_pushed_conn=mark_pushed_conn,
        )
    )
    log_event("push_week", _summary_for_log(result))
    return result


def _summary_for_log(result: PushResult) -> dict[str, Any]:
    summary = result.summary()
    summary["conflicts"] = [asdict(c) for c in result.conflicts]
    summary["mismatches"] = [asdict(m) for m in result.mismatches]
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SPORT_TO_ICU_TYPE: dict[str, str] = {
    "bike": "Ride",
    "run": "Run",
    "swim": "Swim",
    "strength": "WeightTraining",
    "brick": "Ride",  # primary leg of a brick; the run leg goes in description
}


def _intervals_event_type(sport: str) -> str:
    return _SPORT_TO_ICU_TYPE.get(sport.lower(), "Workout")


def render_conflicts_text(conflicts: list[Conflict]) -> str:
    if not conflicts:
        return "No conflicts."
    out: list[str] = [f"{len(conflicts)} conflict(s):"]
    for c in conflicts:
        ext = f" (external_id={c.intervals_external_id})" if c.intervals_external_id else ""
        out.append(
            f"  • {c.date}: existing intervals event #{c.intervals_event_id} "
            f"{c.intervals_name!r}{ext} would be overwritten by planned session "
            f"{c.planned_session_id} ({c.planned_name})"
        )
    return "\n".join(out)


def render_mismatches_text(mismatches: list[Mismatch]) -> str:
    if not mismatches:
        return "All writes verified clean."
    out: list[str] = [f"{len(mismatches)} mismatch(es):"]
    for m in mismatches:
        out.append(
            f"  • {m.session_id} ({m.external_id}) field {m.field!r}: "
            f"intended {m.intended!r}, got {m.actual!r}"
        )
    return "\n".join(out)


def render_session_table_rows(planned: list[PlannedSession]) -> list[tuple[str, ...]]:
    """Plain-data rows for the dry-run preview table."""
    rows: list[tuple[str, ...]] = []
    for s in planned:
        dur = f"{s.target_duration_s // 60} min" if s.target_duration_s else "—"
        tss = str(int(s.target_tss)) if s.target_tss is not None else "—"
        wkout = f"#{s.intervals_workout_id}" if s.intervals_workout_id is not None else "—"
        rows.append(
            (
                s.date,
                s.sport or "—",
                s.library_ref or "—",
                tss,
                dur,
                wkout,
                s.purpose or "—",
            )
        )
    return rows


__all__ = [
    "Conflict",
    "Mismatch",
    "PlannedSession",
    "PushAborted",
    "PushResult",
    "TEMPO_TAG",
    "detect_conflicts",
    "load_planned_sessions",
    "push_week",
    "push_week_async",
    "render_conflicts_text",
    "render_mismatches_text",
    "render_session_table_rows",
    "verify_writes",
]


# Silence unused-import linting; json/Path stay for future helpers.
_ = json
