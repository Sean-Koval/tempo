"""Atomic amendments to a plan — the editing path that closes the long
"agent edits three files in three careful steps" loop the user stories
keep flagging (01 §8, 03 §9, 04, 05 §4, 06).

Each operation does the same five things in a single call:

1. Read the current plan + athlete state.
2. Compute the structural change (date math, phase shift, session edit).
3. Validate against the relevant invariants (active-injury HARD blocks,
   composition rules, `rules.py` for session edits).
4. Apply targeted line-level edits to ``plan.yaml`` / ``goal.yaml`` /
   ``weeks/<week_id>.md`` so YAML comments survive (PyYAML can't
   round-trip those — the comments are load-bearing for the placeholder
   markers, so we never call ``yaml.dump`` on these files).
5. Append a structured entry to ``changelog.md`` and insert a row into
   ``coach.db.decisions`` so the macro / decisions dashboards pick it up.

``--dry-run`` skips step 4 + 5 and returns the same :class:`AmendResult`
so the CLI can print a unified diff without writing.

Single-session edits (``coach week amend-session``) append a structured
``## Amendments`` block to the week file rather than rewriting prose.
The week files are agent-authored markdown without a stable schema — an
appended block is auditable, idempotent, and survives subsequent
``/plan-training-week`` reruns.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

from . import athlete as _athlete
from . import composition, plans
from .db import connect, init_schema
from .events import log_event

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TestKind = Literal["ftp_test", "css_test", "5k_tt", "run_threshold"]
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}
_TEST_LIBRARY_REF = {
    "ftp_test": "ftp_20min_test",
    "css_test": "css_400_assessment",
    "5k_tt": "5k_time_trial",
    "run_threshold": "run_threshold_30min",
}
_TEST_DEFAULT_DURATION_S = {
    "ftp_test": 75 * 60,
    "css_test": 60 * 60,
    "5k_tt": 45 * 60,
    "run_threshold": 60 * 60,
}
_TEST_SPORT = {
    "ftp_test": "bike",
    "css_test": "swim",
    "5k_tt": "run",
    "run_threshold": "run",
}


@dataclass
class FileChange:
    """One file that the amendment will (or did) write."""

    path: Path
    before: str
    after: str
    label: str = ""

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass
class AmendResult:
    """Outcome of one amendment, applied or dry-run.

    ``violations`` carries any HARD or SOFT rule hits surfaced during
    validation — HARD ones cause :func:`apply` to refuse without
    ``--force``; SOFT ones are surfaced but written.
    """

    operation: str
    plan_id: str
    summary: str
    files: list[FileChange] = field(default_factory=list)
    decision_kind: str = ""
    decision_scope: str = ""
    decision_rationale: str = ""
    violations: list[str] = field(default_factory=list)  # human-readable
    hard_block: bool = False
    applied: bool = False

    def changed_files(self) -> list[FileChange]:
        return [f for f in self.files if f.changed]


class AmendError(RuntimeError):
    """Raised when an amendment cannot be performed (state, lookup, or HARD rule)."""


# ---------------------------------------------------------------------------
# shift-target
# ---------------------------------------------------------------------------


_SHIFT_DELTA_RE = re.compile(r"^([+-])\s*(\d+)\s*([dw])$", re.IGNORECASE)


def _parse_shift_delta(spec: str) -> int:
    """Return signed days from ``+6d``, ``-2w``, ``14d`` (sign optional)."""
    s = spec.strip().replace(" ", "")
    if not s:
        raise AmendError("shift delta is empty")
    if s[0].isdigit():
        s = "+" + s
    m = _SHIFT_DELTA_RE.match(s)
    if not m:
        raise AmendError(f"invalid shift delta {spec!r} — expected forms like '+6d', '-1w', '14d'")
    sign = 1 if m.group(1) == "+" else -1
    n = int(m.group(2))
    unit = m.group(3).lower()
    return sign * n * (1 if unit == "d" else 7)


def shift_target(
    plan_id: str,
    *,
    days_delta: int | None = None,
    target: str | None = None,
    reason: str,
    dry_run: bool = False,
    today: date | None = None,
    root: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> AmendResult:
    """Move the A-race / target date by N days OR to an explicit ISO date.

    Phases that have already started (``start_week`` Monday < today's
    week Monday) are left alone — they happened. Future phases shift by
    ``round(days_delta / 7)`` weeks so phase boundaries stay on Monday.

    The taper still ends on race week because every future phase
    (including the taper) shifts uniformly.
    """
    if (days_delta is None) == (target is None):
        raise AmendError("pass exactly one of days_delta=…  or target=YYYY-MM-DD")

    today = today or date.today()
    plan_dir = plans.plan_dir(plan_id, root=root)
    plan_yaml_path = plan_dir / "plan.yaml"
    goal_yaml_path = plan_dir / "goal.yaml"
    changelog_path = plan_dir / "changelog.md"
    if not plan_yaml_path.is_file() or not goal_yaml_path.is_file():
        raise AmendError(f"plan {plan_id!r} not found at {plan_dir}")

    plan_doc = plans.read_plan_yaml(plan_id, root=root) or {}
    goal_doc = _read_yaml(goal_yaml_path)

    cur_target = _coerce_date(
        plan_doc.get("target_date") or goal_doc.get("date"),
        what=f"plan {plan_id!r} target_date",
    )

    if days_delta is None:
        try:
            new_target = date.fromisoformat(target)  # type: ignore[arg-type]
        except ValueError as e:
            raise AmendError(f"--target {target!r} is not YYYY-MM-DD") from e
        days_delta = (new_target - cur_target).days
    else:
        new_target = cur_target + timedelta(days=days_delta)

    if days_delta == 0:
        raise AmendError("delta resolves to 0 days — nothing to shift")

    week_delta = round(days_delta / 7)
    today_week = plans.week_id_for(today)
    today_monday = plans.week_start(today_week)

    # Compute new phases.
    new_phases: list[dict[str, Any]] = []
    shifted_count = 0
    for phase in plan_doc.get("phases") or []:
        sw = phase.get("start_week")
        try:
            phase_monday = plans.week_start(sw) if isinstance(sw, str) else None
        except (ValueError, AttributeError):
            phase_monday = None
        new_entry = dict(phase)
        if phase_monday is not None and phase_monday >= today_monday and week_delta != 0:
            new_entry["start_week"] = plans.shift_week(sw, weeks=week_delta)
            shifted_count += 1
        new_phases.append(new_entry)

    cur_total = plan_doc.get("total_weeks")
    if isinstance(cur_total, int) and shifted_count > 0:
        new_total_weeks: int | None = cur_total + week_delta
    else:
        new_total_weeks = cur_total if isinstance(cur_total, int) else None

    # Validate active-injury HARD rule isn't disturbed: shift_target never
    # changes phase identities, only their dates — so the only risk is
    # accidentally pushing a rehab phase into the past. That can't happen
    # for a positive shift; for a negative one we check.
    violations: list[str] = []
    hard_block = False
    if days_delta < 0 and shifted_count > 0:
        first_future = next(
            (
                p
                for p in new_phases
                if isinstance(p.get("start_week"), str)
                and plans.week_start(p["start_week"]) >= today_monday
            ),
            None,
        )
        if first_future and "rehab" in str(first_future.get("id", "")):
            injury_flags = _athlete.active_injury_flags(root=root)
            if injury_flags:
                violations.append(
                    f"shift would compress rehab phase {first_future['id']!r} "
                    "while injuries are still active — rehab cannot start in the past."
                )
                hard_block = True

    # File changes.
    plan_after = _rewrite_plan_yaml(
        plan_yaml_path.read_text(encoding="utf-8"),
        new_target_date=new_target.isoformat(),
        new_total_weeks=new_total_weeks if isinstance(new_total_weeks, int) else None,
        phase_shifts=[
            (p.get("id"), p["start_week"])  # type: ignore[index]
            for p in new_phases
            if isinstance(p.get("start_week"), str)
        ],
    )
    plan_change = FileChange(
        path=plan_yaml_path,
        before=plan_yaml_path.read_text(encoding="utf-8"),
        after=plan_after,
        label="plan.yaml",
    )

    goal_after = _replace_yaml_scalar(
        goal_yaml_path.read_text(encoding="utf-8"),
        key="date",
        new_value=new_target.isoformat(),
    )
    goal_change = FileChange(
        path=goal_yaml_path,
        before=goal_yaml_path.read_text(encoding="utf-8"),
        after=goal_after,
        label="goal.yaml",
    )

    summary = (
        f"shift target {cur_target.isoformat()} → {new_target.isoformat()} "
        f"({_signed(days_delta)}d, {_signed(week_delta)}w; {shifted_count} phases shifted)"
    )

    changelog_entry = _format_changelog_entry(
        kind="amend",
        title="shift-target",
        body_lines=[
            f"- **From:** {cur_target.isoformat()}",
            f"- **To:** {new_target.isoformat()}",
            f"- **Delta:** {_signed(days_delta)}d ({_signed(week_delta)}w in phase shifts)",
            f"- **Phases shifted:** {shifted_count}",
            f"- **Reason:** {reason}",
        ],
        today=today,
    )
    changelog_change = _changelog_append_change(changelog_path, changelog_entry)

    result = AmendResult(
        operation="plan-shift-target",
        plan_id=plan_id,
        summary=summary,
        files=[plan_change, goal_change, changelog_change],
        decision_kind="adjust",
        decision_scope=f"plan:{plan_id}",
        decision_rationale=f"shift-target {_signed(days_delta)}d — {reason}",
        violations=violations,
        hard_block=hard_block,
    )

    if dry_run or hard_block:
        return result

    _write_changes(result.changed_files())
    _log_decision(
        conn=conn,
        kind=result.decision_kind,
        scope=result.decision_scope,
        rationale=result.decision_rationale,
        changed_files=[str(f.path) for f in result.changed_files()],
    )
    log_event(
        "plan_amend",
        {
            "operation": result.operation,
            "plan_id": plan_id,
            "delta_days": days_delta,
            "delta_weeks": week_delta,
        },
    )
    result.applied = True
    return result


# ---------------------------------------------------------------------------
# switch-target
# ---------------------------------------------------------------------------


def switch_target(
    plan_id: str,
    *,
    new_race_id: str,
    reason: str,
    dry_run: bool = False,
    today: date | None = None,
    root: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> AmendResult:
    """Re-anchor the plan on a different race.

    Same-distance: identical to ``shift_target`` with the new race's date.

    Cross-distance: carries forward all phases that have already started
    (``start_week`` Monday < today's Monday) and re-composes the tail
    against the new distance + remaining runway via
    :func:`composition.compose_chain`. HARD validators on the carry-
    forward + new tail are checked before applying.
    """
    today = today or date.today()
    plan_dir = plans.plan_dir(plan_id, root=root)
    plan_yaml_path = plan_dir / "plan.yaml"
    goal_yaml_path = plan_dir / "goal.yaml"
    changelog_path = plan_dir / "changelog.md"
    if not plan_yaml_path.is_file() or not goal_yaml_path.is_file():
        raise AmendError(f"plan {plan_id!r} not found at {plan_dir}")

    plan_doc = plans.read_plan_yaml(plan_id, root=root) or {}
    goal_doc = _read_yaml(goal_yaml_path)

    new_match = _athlete.find_goal(new_race_id, root=root)
    if new_match is None or new_match.kind != "race":
        raise AmendError(
            f"race {new_race_id!r} not found in athlete/race-calendar.yaml — "
            f"known ids: {_athlete.all_goal_ids(root=root)}"
        )
    new_race = new_match.data
    if new_race.get("status") == "cancelled":
        reason = new_race.get("cancelled_reason") or "(no reason recorded)"
        raise AmendError(
            f"race {new_race_id!r} is cancelled ({reason}); cannot re-anchor onto "
            "it. Pick a confirmed or tentative race."
        )
    new_distance = new_race.get("type") or new_race.get("distance")
    new_target = _coerce_date(new_race.get("date"), what=f"race {new_race_id!r} date")

    cur_distance = goal_doc.get("distance") or plan_doc.get("distance")
    same_distance = cur_distance is not None and cur_distance == new_distance

    if same_distance:
        # Compose the same diff as shift_target but rewrite goal.yaml's id /
        # name fields so the plan re-anchors on the new race.
        cur_target = _coerce_date(goal_doc.get("date"), what="goal.yaml date")
        days_delta = (new_target - cur_target).days
        week_delta = round(days_delta / 7) if days_delta != 0 else 0
        today_monday = plans.week_start(plans.week_id_for(today))

        new_phases: list[dict[str, Any]] = []
        shifted_count = 0
        for phase in plan_doc.get("phases") or []:
            sw = phase.get("start_week")
            try:
                phase_monday = plans.week_start(sw) if isinstance(sw, str) else None
            except (ValueError, AttributeError):
                phase_monday = None
            new_entry = dict(phase)
            if phase_monday is not None and phase_monday >= today_monday and week_delta != 0:
                new_entry["start_week"] = plans.shift_week(sw, weeks=week_delta)
                shifted_count += 1
            new_phases.append(new_entry)

        plan_after = _rewrite_plan_yaml(
            plan_yaml_path.read_text(encoding="utf-8"),
            new_target_date=new_target.isoformat(),
            new_total_weeks=None,
            phase_shifts=[
                (p.get("id"), p["start_week"])  # type: ignore[index]
                for p in new_phases
                if isinstance(p.get("start_week"), str)
            ],
        )
        plan_change = FileChange(
            path=plan_yaml_path,
            before=plan_yaml_path.read_text(encoding="utf-8"),
            after=plan_after,
            label="plan.yaml",
        )

        goal_after = _rewrite_goal_yaml_for_race(
            goal_yaml_path.read_text(encoding="utf-8"),
            new_race=new_race,
        )
        goal_change = FileChange(
            path=goal_yaml_path,
            before=goal_yaml_path.read_text(encoding="utf-8"),
            after=goal_after,
            label="goal.yaml",
        )

        summary = (
            f"switch-target → {new_race_id} (same distance {new_distance}); "
            f"date {cur_target.isoformat()} → {new_target.isoformat()}; "
            f"{shifted_count} future phases shifted"
        )
        body_lines = [
            f"- **From race:** {goal_doc.get('id')} ({cur_target.isoformat()})",
            f"- **To race:** {new_race_id} ({new_target.isoformat()})",
            f"- **Distance unchanged:** {new_distance}",
            f"- **Phases shifted:** {shifted_count}",
            f"- **Reason:** {reason}",
        ]
    else:
        # Cross-distance switch: carry-forward + recompose tail.
        today_monday = plans.week_start(plans.week_id_for(today))
        completed = [
            p
            for p in plan_doc.get("phases") or []
            if isinstance(p.get("start_week"), str)
            and plans.week_start(p["start_week"]) < today_monday
        ]

        # Runway from the start of next week to the race week (inclusive of race week).
        next_week = plans.shift_week(plans.week_id_for(today), weeks=1)
        runway_weeks = max(
            1,
            int(
                (plans.week_start(plans.week_id_for(new_target)) - plans.week_start(next_week)).days
                / 7
            )
            + 1,
        )

        injury_types = composition.injury_types_from_flags(_athlete.active_injury_flags(root=root))
        try:
            chain = composition.compose_chain(
                distance=new_distance,
                runway_weeks=runway_weeks,
                has_target_date=True,
                active_injury_types=injury_types,
                root=root,
            )
        except composition.CompositionError as e:
            raise AmendError(
                f"cannot recompose tail for {new_distance!r} runway={runway_weeks}w: {e}"
            ) from e

        new_phases = list(completed)
        cursor = next_week
        for cp in chain.phases:
            new_phases.append(
                {
                    "id": cp.id,
                    "start_week": cursor,
                    "weeks": cp.weeks,
                    "weekly_tss_target": list(
                        cp.weekly_tss_per_hour
                    ),  # placeholder; tuning is plan-week's job
                    "intensity_distribution": dict(cp.intensity_distribution),
                    "key_sessions": list(cp.key_sessions),
                    "sport_focus": dict(cp.sport_focus),
                }
            )
            cursor = plans.shift_week(cursor, weeks=cp.weeks)

        # Rewrite plan.yaml's phases section wholesale — comments under the
        # phases block will not survive this. The header above (plan_id /
        # template / target_date / etc.) is preserved by anchoring on the
        # 'phases:' marker line.
        plan_after = _rewrite_plan_yaml_phases_block(
            plan_yaml_path.read_text(encoding="utf-8"),
            new_target_date=new_target.isoformat(),
            phases=new_phases,
        )
        plan_change = FileChange(
            path=plan_yaml_path,
            before=plan_yaml_path.read_text(encoding="utf-8"),
            after=plan_after,
            label="plan.yaml",
        )

        goal_after = _rewrite_goal_yaml_for_race(
            goal_yaml_path.read_text(encoding="utf-8"),
            new_race=new_race,
            new_distance=new_distance,
        )
        goal_change = FileChange(
            path=goal_yaml_path,
            before=goal_yaml_path.read_text(encoding="utf-8"),
            after=goal_after,
            label="goal.yaml",
        )

        summary = (
            f"switch-target → {new_race_id} (distance {cur_distance!r} → {new_distance!r}); "
            f"{len(completed)} phases carried forward; tail recomposed "
            f"({len(chain.phases)} phases over {runway_weeks}w)."
        )
        body_lines = [
            f"- **From race:** {goal_doc.get('id')} ({cur_distance})",
            f"- **To race:** {new_race_id} ({new_distance}, {new_target.isoformat()})",
            f"- **Carry-forward:** {len(completed)} phase(s)",
            f"- **New tail:** {[p.id for p in chain.phases]}",
            f"- **Reason:** {reason}",
        ]

    changelog_entry = _format_changelog_entry(
        kind="amend",
        title="switch-target",
        body_lines=body_lines,
        today=today,
    )
    changelog_change = _changelog_append_change(changelog_path, changelog_entry)

    result = AmendResult(
        operation="plan-switch-target",
        plan_id=plan_id,
        summary=summary,
        files=[plan_change, goal_change, changelog_change],
        decision_kind="adjust",
        decision_scope=f"plan:{plan_id}",
        decision_rationale=f"switch-target {new_race_id} — {reason}",
    )

    if dry_run:
        return result

    _write_changes(result.changed_files())
    _log_decision(
        conn=conn,
        kind=result.decision_kind,
        scope=result.decision_scope,
        rationale=result.decision_rationale,
        changed_files=[str(f.path) for f in result.changed_files()],
    )
    log_event(
        "plan_amend",
        {
            "operation": result.operation,
            "plan_id": plan_id,
            "new_race_id": new_race_id,
        },
    )
    result.applied = True
    return result


# ---------------------------------------------------------------------------
# insert-test
# ---------------------------------------------------------------------------


_TEST_SLOT_RE = re.compile(r"^(\d{4}-W\d{2})-(mon|tue|wed|thu|fri|sat|sun)$", re.IGNORECASE)


def insert_test(
    plan_id: str,
    *,
    slot: str,
    kind: TestKind,
    reason: str,
    recalibrate_on_result: bool = True,
    dry_run: bool = False,
    today: date | None = None,
    root: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> AmendResult:
    """Insert a calibration test (FTP / CSS / 5K / run threshold) into a week.

    Writes both:
      - A structured ``## Inserted test`` block to the week file.
      - A ``sessions_planned`` row in coach.db so ``coach push-week``
        emits it to intervals.icu on the next push.
      - A line to ``plans/<id>/calibration_followups.md`` if
        ``recalibrate_on_result`` (default on) so future-you remembers
        to revisit zones after the test lands.
    """
    today = today or date.today()
    if kind not in _TEST_LIBRARY_REF:
        raise AmendError(f"unknown test kind {kind!r} — pick from {sorted(_TEST_LIBRARY_REF)}")
    m = _TEST_SLOT_RE.match(slot.strip())
    if not m:
        raise AmendError(f"slot {slot!r} must be like '2026-W22-Wed'")
    week_id = m.group(1)
    day_key = m.group(2).lower()
    weekday_index = DAY_KEYS.index(day_key) + 1
    try:
        slot_date = date.fromisocalendar(*plans.parse_week_id(week_id), weekday_index)
    except ValueError as e:
        raise AmendError(f"slot {slot!r} doesn't resolve to a real date: {e}") from e

    plan_dir = plans.plan_dir(plan_id, root=root)
    if not (plan_dir / "plan.yaml").is_file():
        raise AmendError(f"plan {plan_id!r} not found at {plan_dir}")

    week_file = plans.week_file(plan_id, week_id, root=root)
    library_ref = _TEST_LIBRARY_REF[kind]
    sport = _TEST_SPORT[kind]
    duration_s = _TEST_DEFAULT_DURATION_S[kind]
    session_id = f"{plan_id}-{slot_date.isoformat()}-{kind}"

    block = (
        f"\n## Inserted test — {kind} ({slot_date.isoformat()})\n\n"
        f"- **Library ref:** `{library_ref}`\n"
        f"- **Sport:** {sport}\n"
        f"- **Day:** {DAY_NAMES[day_key]}\n"
        f"- **Default duration:** {duration_s // 60} min\n"
        f"- **Reason:** {reason}\n"
    )
    if recalibrate_on_result:
        block += (
            "- **Recalibrate-on-result:** when this test lands, update "
            "`athlete/profile.yaml` zones and re-run /plan-training-week.\n"
        )

    week_file_change = _append_or_create(
        path=week_file,
        appended=block,
        creation_skeleton=lambda p: (
            f"# Week {week_id}\n\n"
            f"_Created by `coach plan amend insert-test` on {today.isoformat()}._\n"
        ),
        label=f"plans/{plan_id}/weeks/{week_id}.md",
    )

    files: list[FileChange] = [week_file_change]
    db_inserted = False
    owns_conn = False
    if not dry_run:
        if conn is None:
            conn = connect()
            init_schema(conn)
            owns_conn = True
        with conn:
            conn.execute(
                """
                INSERT INTO sessions_planned(
                    id, plan_id, week_id, date, sport, library_ref,
                    target_tss, target_duration_s, purpose, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    plan_id = excluded.plan_id,
                    week_id = excluded.week_id,
                    date = excluded.date,
                    sport = excluded.sport,
                    library_ref = excluded.library_ref,
                    target_duration_s = excluded.target_duration_s,
                    purpose = excluded.purpose,
                    notes = excluded.notes
                """,
                (
                    session_id,
                    plan_id,
                    week_id,
                    slot_date.isoformat(),
                    sport,
                    library_ref,
                    None,
                    duration_s,
                    f"calibration test ({kind})",
                    reason,
                ),
            )
        db_inserted = True

    # Calibration follow-ups file.
    if recalibrate_on_result:
        followups_path = plan_dir / "calibration_followups.md"
        followup_line = (
            f"- [ ] {slot_date.isoformat()} **{kind}** — recalibrate "
            f"{'FTP' if kind == 'ftp_test' else 'CSS' if kind == 'css_test' else 'run threshold'} "
            f"on result. (planned via `coach plan amend insert-test`)\n"
        )
        files.append(
            _append_or_create(
                path=followups_path,
                appended=followup_line,
                creation_skeleton=lambda p: (
                    "# Calibration follow-ups\n\nTest insertions waiting on a "
                    "result to update zones in `athlete/profile.yaml`.\n\n"
                ),
                label=f"plans/{plan_id}/calibration_followups.md",
            )
        )

    changelog_entry = _format_changelog_entry(
        kind="amend",
        title=f"insert-test ({kind})",
        body_lines=[
            f"- **Slot:** {slot_date.isoformat()} ({DAY_NAMES[day_key]})",
            f"- **Sport:** {sport}",
            f"- **Library ref:** `{library_ref}`",
            f"- **Reason:** {reason}",
            f"- **Recalibrate-on-result:** {'yes' if recalibrate_on_result else 'no'}",
        ],
        today=today,
    )
    changelog_change = _changelog_append_change(plan_dir / "changelog.md", changelog_entry)
    files.append(changelog_change)

    result = AmendResult(
        operation="plan-insert-test",
        plan_id=plan_id,
        summary=f"insert {kind} on {slot_date.isoformat()} ({DAY_NAMES[day_key]})",
        files=files,
        decision_kind="adjust",
        decision_scope=f"plan:{plan_id}",
        decision_rationale=f"insert-test {kind} @ {slot_date.isoformat()} — {reason}",
    )

    if dry_run:
        return result

    _write_changes(result.changed_files())
    _log_decision(
        conn=conn,
        kind=result.decision_kind,
        scope=result.decision_scope,
        rationale=result.decision_rationale,
        changed_files=[str(f.path) for f in result.changed_files()],
    )
    if owns_conn and conn is not None:
        conn.close()
    log_event(
        "plan_amend",
        {
            "operation": result.operation,
            "plan_id": plan_id,
            "kind": kind,
            "slot": slot_date.isoformat(),
            "db_inserted": db_inserted,
        },
    )
    result.applied = True
    return result


# ---------------------------------------------------------------------------
# week amend-session
# ---------------------------------------------------------------------------


def amend_session(
    plan_id: str,
    *,
    week_id: str,
    day: str,
    duration: str | None = None,
    zone: str | None = None,
    swap_sport: str | None = None,
    reason: str,
    dry_run: bool = False,
    today: date | None = None,
    root: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> AmendResult:
    """Append a structured single-session amendment to ``weeks/<week_id>.md``.

    Week files don't have a stable schema (they're agent-authored
    markdown), so we append an ``## Amendments`` block rather than
    rewriting prose. This is auditable, idempotent at the date level
    (each amendment is a new bullet under the section header), and
    survives subsequent ``/plan-training-week`` reruns.

    If a ``sessions_planned`` row exists for the day in ``coach.db``,
    this also updates its ``target_duration_s`` / ``sport`` / ``notes``
    so ``coach push-week`` reflects the amendment.
    """
    today = today or date.today()
    day_key = day.strip().lower()[:3]
    if day_key not in DAY_KEYS:
        raise AmendError(f"day {day!r} must be one of {DAY_KEYS} (case-insensitive)")
    weekday_index = DAY_KEYS.index(day_key) + 1
    try:
        slot_date = date.fromisocalendar(*plans.parse_week_id(week_id), weekday_index)
    except ValueError as e:
        raise AmendError(f"week_id {week_id!r} or day {day!r} invalid: {e}") from e

    plan_dir = plans.plan_dir(plan_id, root=root)
    if not (plan_dir / "plan.yaml").is_file():
        raise AmendError(f"plan {plan_id!r} not found at {plan_dir}")

    if duration is None and zone is None and swap_sport is None:
        raise AmendError("at least one of --duration / --zone / --swap-sport must be supplied")

    duration_s = _parse_duration_to_seconds(duration) if duration else None
    if swap_sport and swap_sport.lower() not in {"bike", "run", "swim", "strength", "brick"}:
        raise AmendError(f"--swap-sport {swap_sport!r} must be one of bike/run/swim/strength/brick")

    week_file = plans.week_file(plan_id, week_id, root=root)
    block_header = "\n## Amendments\n"

    parts: list[str] = [f"### {slot_date.isoformat()} ({DAY_NAMES[day_key]})"]
    if duration:
        suffix = f" ({duration_s // 60} min)" if duration_s is not None else ""
        parts.append(f"- **Duration:** {duration}{suffix}")
    if zone:
        parts.append(f"- **Zone:** {zone}")
    if swap_sport:
        parts.append(f"- **Swap sport:** {swap_sport}")
    parts.append(f"- **Reason:** {reason}")
    parts.append(f"- **Logged:** {today.isoformat()}")
    entry = "\n".join(parts) + "\n"

    week_file_change = _append_or_create(
        path=week_file,
        appended=block_header + entry if not _has_amendments_section(week_file) else "\n" + entry,
        creation_skeleton=lambda p: (
            f"# Week {week_id}\n\n_Stub created by `coach week amend-session` on {today.isoformat()}._\n"
        ),
        label=f"plans/{plan_id}/weeks/{week_id}.md",
    )

    files: list[FileChange] = [week_file_change]
    db_updated_count = 0
    owns_conn = False
    if not dry_run:
        if conn is None:
            conn = connect()
            init_schema(conn)
            owns_conn = True
        updates: list[str] = []
        params: list[Any] = []
        if duration_s is not None:
            updates.append("target_duration_s = ?")
            params.append(duration_s)
        if swap_sport:
            updates.append("sport = ?")
            params.append(swap_sport.lower())
        if zone:
            updates.append("notes = COALESCE(notes || char(10), '') || ?")
            params.append(f"amend {today.isoformat()}: zone={zone} reason={reason}")
        if updates:
            params.extend([plan_id, slot_date.isoformat()])
            with conn:
                cur = conn.execute(
                    f"UPDATE sessions_planned SET {', '.join(updates)} "
                    "WHERE plan_id = ? AND date = ?",
                    params,
                )
                db_updated_count = cur.rowcount or 0

    changelog_change = _changelog_append_change(
        plan_dir / "changelog.md",
        _format_changelog_entry(
            kind="amend",
            title=f"week {week_id} {DAY_NAMES[day_key]} session",
            body_lines=[
                f"- **Date:** {slot_date.isoformat()}"
                + (f" — duration {duration}" if duration else "")
                + (f" zone {zone}" if zone else "")
                + (f" swap to {swap_sport}" if swap_sport else ""),
                f"- **Reason:** {reason}",
            ],
            today=today,
        ),
    )
    files.append(changelog_change)

    result = AmendResult(
        operation="week-amend-session",
        plan_id=plan_id,
        summary=f"amend {week_id} {DAY_NAMES[day_key]} ({slot_date.isoformat()})",
        files=files,
        decision_kind="adjust",
        decision_scope=f"week:{week_id}",
        decision_rationale=(
            f"amend-session {week_id}/{DAY_NAMES[day_key]}: "
            + ", ".join(
                filter(
                    None,
                    [
                        f"duration={duration}" if duration else None,
                        f"zone={zone}" if zone else None,
                        f"swap_sport={swap_sport}" if swap_sport else None,
                    ],
                )
            )
            + f" — {reason}"
        ),
    )

    if dry_run:
        return result

    _write_changes(result.changed_files())
    _log_decision(
        conn=conn,
        kind=result.decision_kind,
        scope=result.decision_scope,
        rationale=result.decision_rationale,
        changed_files=[str(f.path) for f in result.changed_files()],
    )
    if owns_conn and conn is not None:
        conn.close()
    log_event(
        "week_amend_session",
        {
            "plan_id": plan_id,
            "week_id": week_id,
            "date": slot_date.isoformat(),
            "duration": duration,
            "zone": zone,
            "swap_sport": swap_sport,
            "db_updated_count": db_updated_count,
        },
    )
    result.applied = True
    return result


# ---------------------------------------------------------------------------
# Helpers — YAML in-place edits
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _coerce_date(value: Any, *, what: str) -> date:
    """Accept either a ``datetime.date`` (PyYAML's auto-coercion) or an ISO string.

    PyYAML resolves bare ``YYYY-MM-DD`` scalars to ``datetime.date`` objects,
    so plan files that look textual on disk come back typed in memory. The
    code path needs to handle both forms transparently.
    """
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as e:
            raise AmendError(f"{what} {value!r} is not ISO YYYY-MM-DD") from e
    raise AmendError(f"{what} is missing or not a date")


def _replace_yaml_scalar(text: str, *, key: str, new_value: str) -> str:
    """Replace ``<key>: <value>`` line at the top level, preserving comments.

    Only top-level key/value lines (not nested in lists). Trailing inline
    comments on the same line are preserved. ``[ \\t]*`` rather than
    ``\\s*`` so the regex doesn't greedily absorb the line-terminating
    newline.
    """
    pattern = re.compile(
        rf"^(?P<key>{re.escape(key)})[ \t]*:[ \t]*(?P<val>[^\n#]*?)(?P<trail>[ \t]*(#.*)?)$",
        re.MULTILINE,
    )

    def _sub(m: re.Match[str]) -> str:
        return f"{m.group('key')}: {new_value}{m.group('trail')}"

    new_text, count = pattern.subn(_sub, text, count=1)
    if count == 0:
        raise AmendError(f"key {key!r} not found at top-level for in-place rewrite")
    return new_text


def _rewrite_plan_yaml(
    text: str,
    *,
    new_target_date: str,
    new_total_weeks: int | None,
    phase_shifts: list[tuple[str | None, str]],
) -> str:
    """Rewrite ``target_date``, ``total_weeks``, and per-phase ``start_week`` lines."""
    text = _replace_yaml_scalar(text, key="target_date", new_value=new_target_date)
    if new_total_weeks is not None:
        text = _replace_yaml_scalar(text, key="total_weeks", new_value=str(new_total_weeks))

    # Walk phase blocks and replace their start_week. Phase blocks look like:
    #   - id: <name>
    #     start_week: <YYYY-Www>
    text = _rewrite_phase_start_weeks(text, phase_shifts)
    return text


def _rewrite_phase_start_weeks(text: str, shifts: list[tuple[str | None, str]]) -> str:
    """For each (phase_id, new_start_week), update that phase's start_week line."""
    lines = text.splitlines(keepends=True)
    shift_map = {pid: new_sw for pid, new_sw in shifts if pid}
    out: list[str] = []
    cur_phase: str | None = None
    for line in lines:
        m_id = re.match(r"^\s*-\s*id:\s*([\w_\-]+)", line)
        if m_id:
            cur_phase = m_id.group(1)
            out.append(line)
            continue
        m_sw = re.match(r"^([ \t]*)start_week:[ \t]*[\w\-]+([ \t]*(#.*)?)\n?$", line)
        if m_sw and cur_phase and cur_phase in shift_map:
            out.append(f"{m_sw.group(1)}start_week: {shift_map[cur_phase]}{m_sw.group(2)}\n")
            continue
        out.append(line)
    return "".join(out)


def _rewrite_plan_yaml_phases_block(
    text: str,
    *,
    new_target_date: str,
    phases: list[dict[str, Any]],
) -> str:
    """Replace everything from ``phases:`` onward with a freshly-dumped block.

    Used by switch-target when the chain shape changes. Comments on the
    *header* (above ``phases:``) are preserved; comments inside the
    phases block are not — they belonged to the old chain.
    """
    text = _replace_yaml_scalar(text, key="target_date", new_value=new_target_date)
    m = re.search(r"^phases\s*:\s*$", text, re.MULTILINE)
    if not m:
        raise AmendError("'phases:' marker not found in plan.yaml")
    head = text[: m.start()]
    tail = "phases:\n"
    for phase in phases:
        tail += "\n  - id: " + str(phase.get("id", ""))
        for k, v in phase.items():
            if k == "id":
                continue
            tail += f"\n    {k}: " + _format_yaml_value(v)
        tail += "\n"
    return head.rstrip() + "\n\n" + tail


def _format_yaml_value(v: Any) -> str:
    """One-line YAML representation that matches the project's flow style."""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return yaml.safe_dump(v, default_flow_style=True, sort_keys=False).strip()


def _rewrite_goal_yaml_for_race(
    text: str,
    *,
    new_race: dict[str, Any],
    new_distance: str | None = None,
) -> str:
    """Update goal.yaml's id / name / date / distance / location / priority fields."""
    new_id = new_race.get("id")
    new_name = new_race.get("name") or f"{new_race.get('type') or new_distance or '?'} ({new_id})"
    new_date = new_race.get("date")
    new_priority = new_race.get("priority")
    new_location = new_race.get("location") or "TBD"
    distance_v = new_distance or new_race.get("type") or new_race.get("distance")

    if isinstance(new_id, str):
        text = _replace_yaml_scalar(text, key="id", new_value=new_id)
    if isinstance(new_name, str):
        text = _replace_yaml_scalar(text, key="name", new_value=_quote_if_needed(new_name))
    if isinstance(new_date, str):
        text = _replace_yaml_scalar(text, key="date", new_value=new_date)
    if isinstance(distance_v, str):
        text = _replace_yaml_scalar(text, key="distance", new_value=distance_v)
    if isinstance(new_priority, str):
        text = _replace_yaml_scalar(text, key="priority", new_value=new_priority)
    if isinstance(new_location, str):
        text = _replace_yaml_scalar(text, key="location", new_value=_quote_if_needed(new_location))
    return text


def _quote_if_needed(s: str) -> str:
    if any(ch in s for ch in ":#-{}[],&*!|>'\"%@`") or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


# ---------------------------------------------------------------------------
# Helpers — markdown append / changelog / decisions
# ---------------------------------------------------------------------------


def _format_changelog_entry(
    *,
    kind: str,
    title: str,
    body_lines: list[str],
    today: date,
) -> str:
    header = f"\n## {today.isoformat()} — {title}\n"
    return header + "\n".join(body_lines) + f"\n_kind: {kind}_\n"


def _changelog_append_change(path: Path, entry: str) -> FileChange:
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    after = (before.rstrip() + "\n" + entry) if before else f"# Plan Changelog{entry}"
    return FileChange(path=path, before=before, after=after, label=path.name)


def _append_or_create(
    *,
    path: Path,
    appended: str,
    creation_skeleton,
    label: str,
) -> FileChange:
    if path.is_file():
        before = path.read_text(encoding="utf-8")
        after = before.rstrip() + "\n" + appended
    else:
        before = ""
        after = creation_skeleton(path) + appended
    return FileChange(path=path, before=before, after=after, label=label)


def _has_amendments_section(path: Path) -> bool:
    if not path.is_file():
        return False
    return bool(re.search(r"^##\s+Amendments\s*$", path.read_text(encoding="utf-8"), re.MULTILINE))


def _write_changes(changes: list[FileChange]) -> None:
    for change in changes:
        change.path.parent.mkdir(parents=True, exist_ok=True)
        change.path.write_text(change.after, encoding="utf-8")


def _log_decision(
    *,
    conn: sqlite3.Connection | None,
    kind: str,
    scope: str,
    rationale: str,
    changed_files: list[str],
) -> None:
    owns_conn = conn is None
    if conn is None:
        conn = connect()
        init_schema(conn)
    try:
        with conn:
            conn.execute(
                "INSERT INTO decisions(timestamp, scope, kind, rationale, changed_files) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    scope,
                    kind,
                    rationale,
                    "\n".join(changed_files),
                ),
            )
    finally:
        if owns_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


_DUR_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]+)?$")


def _parse_duration_to_seconds(spec: str) -> int | None:
    """Best-effort duration parser for ``--duration`` (45min, 1.5h, 14km, etc.).

    Distance specs (km / mi / m) return None — the field is informational
    on the markdown side, but coach.db tracks duration_s so we leave the
    DB column unchanged when a distance is supplied.
    """
    s = spec.strip().lower().replace(" ", "")
    m = _DUR_RE.match(s)
    if not m:
        return None
    num = float(m.group("num"))
    unit = (m.group("unit") or "min").lower()
    if unit in {"s", "sec", "secs"}:
        return int(num)
    if unit in {"m", "min", "mins"}:
        return int(num * 60)
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return int(num * 3600)
    return None


def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


__all__ = [
    "AmendError",
    "AmendResult",
    "FileChange",
    "TestKind",
    "amend_session",
    "insert_test",
    "shift_target",
    "switch_target",
]
