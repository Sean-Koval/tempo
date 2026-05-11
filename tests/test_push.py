"""Unit tests for ``tempo.push`` — conflict detection + verify logic.

The async ``push_week_async`` orchestrator is exercised through a
stubbed ``ICUClient`` that records calls, so we can assert idempotency
+ verify behaviour without hitting intervals.icu.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any

import pytest
from intervals_icu_mcp.models import Event

from tempo.push import (
    Conflict,
    PlannedSession,
    PushAborted,
    detect_conflicts,
    push_week_async,
    verify_writes,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeClient:
    """Records calls; serves a fixed in-memory event list.

    Implements the subset of ``ICUClient`` that ``push_week_async`` uses:
    ``__aenter__`` / ``__aexit__`` / ``get_events`` / ``create_event`` /
    ``update_event``.
    """

    def __init__(self, *, initial: list[Event]) -> None:
        self.events: list[Event] = list(initial)
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[int, dict[str, Any]]] = []
        self.get_calls = 0

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get_events(self, *, oldest=None, newest=None, athlete_id=None) -> list[Event]:
        self.get_calls += 1
        return list(self.events)

    async def create_event(self, payload: dict[str, Any]) -> Event:
        self.created.append(payload)
        new = _make_event(
            id=1000 + len(self.events),
            start_date_local=payload.get("start_date_local", ""),
            external_id=payload.get("external_id"),
            name=payload.get("name"),
            description=payload.get("description"),
            type_=payload.get("type"),
            moving_time=payload.get("moving_time"),
            icu_training_load=payload.get("icu_training_load"),
        )
        self.events.append(new)
        return new

    async def update_event(self, event_id: int, payload: dict[str, Any]) -> Event:
        self.updated.append((event_id, payload))
        for i, ev in enumerate(self.events):
            if ev.id == event_id:
                merged = ev.model_copy(
                    update={
                        k: payload[k]
                        for k in (
                            "start_date_local",
                            "name",
                            "description",
                            "external_id",
                            "type",
                            "moving_time",
                            "icu_training_load",
                        )
                        if k in payload
                    }
                )
                self.events[i] = merged
                return merged
        raise AssertionError(f"unknown event id {event_id}")


def _make_event(**overrides: Any) -> Event:
    """Build an Event with sensible defaults so tests can override one field."""
    base: dict[str, Any] = {
        "id": 1,
        "start_date_local": "2026-04-27",
        "category": "WORKOUT",
        "name": None,
        "description": None,
        "external_id": None,
        "type": None,
        "moving_time": None,
        "icu_training_load": None,
    }
    if "type_" in overrides:
        overrides["type"] = overrides.pop("type_")
    base.update(overrides)
    return Event(**base)


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


def test_no_conflict_when_existing_event_is_tempo_owned():
    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27")]
    existing = [
        _make_event(
            id=42,
            start_date_local="2026-04-27",
            external_id="demo/s1",
        )
    ]
    assert detect_conflicts(existing_events=existing, planned=planned, plan_id="demo") == []


def test_conflict_when_manual_event_in_slot():
    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27", sport="run")]
    existing = [
        _make_event(
            id=99,
            start_date_local="2026-04-27",
            name="Mom's birthday party",
            external_id=None,
        )
    ]
    conflicts = detect_conflicts(existing_events=existing, planned=planned, plan_id="demo")
    assert len(conflicts) == 1
    assert conflicts[0].intervals_event_id == 99
    assert conflicts[0].planned_session_id == "s1"


def test_no_conflict_when_existing_event_outside_planned_dates():
    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27")]
    existing = [_make_event(id=88, start_date_local="2026-05-15")]
    assert detect_conflicts(existing_events=existing, planned=planned, plan_id="demo") == []


def test_other_plan_event_is_a_conflict():
    """Events from a different plan_id must conflict — we don't blindly clobber them."""
    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27")]
    existing = [
        _make_event(
            id=77,
            start_date_local="2026-04-27",
            external_id="other-plan/foo",
        )
    ]
    conflicts = detect_conflicts(existing_events=existing, planned=planned, plan_id="demo")
    assert len(conflicts) == 1


# ---------------------------------------------------------------------------
# verify_writes
# ---------------------------------------------------------------------------


def test_verify_clean_after_round_trip():
    planned = [
        PlannedSession(
            id="s1",
            plan_id="demo",
            date="2026-04-27",
            sport="bike",
            target_duration_s=3600,
            target_tss=80,
            library_ref="endurance_z2",
        )
    ]
    payload = planned[0].to_event_payload()
    actual = [
        _make_event(
            id=10,
            start_date_local=payload["start_date_local"],
            name=payload["name"],
            type_=payload["type"],
            moving_time=payload["moving_time"],
            icu_training_load=payload["icu_training_load"],
            external_id=payload["external_id"],
        )
    ]
    assert verify_writes(intended=planned, actual_events=actual) == []


def test_verify_flags_missing_event():
    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27")]
    actual: list[Event] = []
    mismatches = verify_writes(intended=planned, actual_events=actual)
    assert len(mismatches) == 1
    assert mismatches[0].field == "<event>"


def test_verify_flags_field_drift():
    planned = [
        PlannedSession(
            id="s1",
            plan_id="demo",
            date="2026-04-27",
            sport="run",
            target_duration_s=2700,
        )
    ]
    actual = [
        _make_event(
            id=10,
            start_date_local="2026-04-27",
            external_id="demo/s1",
            name=planned[0]._event_name(),
            type_="Run",
            moving_time=1800,  # drift
        )
    ]
    mismatches = verify_writes(intended=planned, actual_events=actual)
    fields = {m.field for m in mismatches}
    assert "moving_time" in fields


# ---------------------------------------------------------------------------
# push_week_async — orchestration
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_config():
    @dataclass
    class _Cfg:
        intervals_icu_athlete_id: str = "i1"

    return _Cfg()


@pytest.mark.asyncio
async def test_push_dry_run_does_not_write(monkeypatch, fake_config, tmp_path: Path):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    fake = _FakeClient(initial=[])
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    planned = [
        PlannedSession(
            id="s1", plan_id="demo", date="2026-04-27", sport="run", target_duration_s=2700
        )
    ]
    result = await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
        dry_run=True,
    )
    assert result.dry_run
    assert result.written_count == 0
    assert fake.created == []
    assert fake.updated == []


@pytest.mark.asyncio
async def test_push_creates_then_updates_idempotently(monkeypatch, fake_config, tmp_path: Path):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    fake = _FakeClient(initial=[])
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    planned = [
        PlannedSession(
            id="s1", plan_id="demo", date="2026-04-27", sport="run", target_duration_s=2700
        )
    ]

    # First push: create
    r1 = await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
    )
    assert r1.created_count == 1
    assert r1.updated_count == 0
    assert r1.verified
    assert r1.mismatches == []

    # Second push of same payload: must be an update, not a duplicate create.
    r2 = await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
    )
    assert r2.created_count == 0
    assert r2.updated_count == 1


@pytest.mark.asyncio
async def test_push_aborts_on_conflict_when_no_force(monkeypatch, fake_config, tmp_path: Path):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    existing = [
        _make_event(
            id=99,
            start_date_local="2026-04-27",
            name="Manual event",
            external_id=None,
        )
    ]
    fake = _FakeClient(initial=existing)
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27", sport="run")]

    with pytest.raises(PushAborted):
        await push_week_async(
            config=fake_config,
            plan_id="demo",
            week_id="2026-W18",
            planned=planned,
        )


@pytest.mark.asyncio
async def test_push_force_overwrite_proceeds(monkeypatch, fake_config, tmp_path: Path):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    existing = [_make_event(id=99, start_date_local="2026-04-27", name="Manual", external_id=None)]
    fake = _FakeClient(initial=existing)
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27", sport="run")]
    result = await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
        force_overwrite=True,
    )
    assert len(result.conflicts) == 1  # surfaced for log even though we proceed
    assert result.created_count == 1


@pytest.mark.asyncio
async def test_push_dry_run_reports_conflicts_without_aborting(
    monkeypatch, fake_config, tmp_path: Path
):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    existing = [_make_event(id=99, start_date_local="2026-04-27", name="Manual", external_id=None)]
    fake = _FakeClient(initial=existing)
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27", sport="run")]
    result = await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
        dry_run=True,
    )
    assert result.dry_run
    assert len(result.conflicts) == 1
    assert fake.created == []  # nothing written


@pytest.mark.asyncio
async def test_push_prompt_callback_path(monkeypatch, fake_config, tmp_path: Path):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    existing = [_make_event(id=99, start_date_local="2026-04-27", external_id=None)]
    fake = _FakeClient(initial=existing)
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    planned = [PlannedSession(id="s1", plan_id="demo", date="2026-04-27", sport="run")]

    prompts = []

    def _cb(conflicts: list[Conflict]) -> bool:
        prompts.append(conflicts)
        return True

    result = await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
        on_conflict_prompt=_cb,
    )
    assert prompts and len(prompts[0]) == 1
    assert result.created_count == 1


def test_load_planned_sessions(tmp_data_dir: Path):
    from tempo.db import connect, init_schema
    from tempo.push import load_planned_sessions

    c = connect()
    init_schema(c)
    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, "
        "target_duration_s) VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", "demo", "2026-W18", "2026-04-27", "run", 2700),
    )
    rows = load_planned_sessions(c, week_id="2026-W18")
    assert len(rows) == 1
    assert rows[0].external_id == "demo/s1"
    c.close()
    _ = _date  # ensure import is exercised across the test set


def test_planned_session_event_payload_shape():
    s = PlannedSession(
        id="s1",
        plan_id="demo",
        date="2026-04-27",
        sport="run",
        target_duration_s=2700,
        target_tss=42,
        library_ref="long_run_z2",
        notes="Z2 chatty pace",
    )
    payload = s.to_event_payload()
    assert payload["external_id"] == "demo/s1"
    assert payload["category"] == "WORKOUT"
    assert payload["type"] == "Run"
    assert payload["moving_time"] == 2700
    assert payload["icu_training_load"] == 42
    assert payload["description"].startswith("[tempo] plan=demo session=s1")
    assert "Z2 chatty pace" in payload["description"]


def test_to_event_payload_attaches_workout_id_when_set():
    s = PlannedSession(
        id="s1",
        plan_id="demo",
        date="2026-04-27",
        sport="bike",
        target_duration_s=3600,
        target_tss=80,
        library_ref="tempo_bike_block",
        intervals_workout_id=42,
    )
    payload = s.to_event_payload()
    assert payload["plan_workout_id"] == 42


def test_to_event_payload_omits_workout_id_when_unset():
    s = PlannedSession(
        id="s1",
        plan_id="demo",
        date="2026-04-27",
        sport="bike",
    )
    payload = s.to_event_payload()
    assert "plan_workout_id" not in payload


def test_load_planned_sessions_attaches_mapped_workout_id(tmp_data_dir: Path):
    from tempo.db import connect, init_schema
    from tempo.library_map import upsert_mapping
    from tempo.push import load_planned_sessions

    c = connect()
    init_schema(c)
    upsert_mapping(
        c,
        library_ref="tempo_bike_block",
        intervals_workout_id=42,
        intervals_name="Tempo block v1",
        intervals_folder_id=None,
        sport="bike",
    )
    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_duration_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("s1", "demo", "2026-W18", "2026-04-27", "bike", "tempo_bike_block", 3600),
    )
    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("s2", "demo", "2026-W18", "2026-04-28", "run", "easy_aerobic_run"),
    )
    rows = load_planned_sessions(c, week_id="2026-W18")
    by_id = {r.id: r for r in rows}
    assert by_id["s1"].intervals_workout_id == 42
    # Unmapped ref falls back cleanly.
    assert by_id["s2"].intervals_workout_id is None
    c.close()


@pytest.mark.asyncio
async def test_push_payload_carries_mapped_workout_id(monkeypatch, fake_config, tmp_data_dir: Path):
    """End-to-end: a mapping → push sends ``plan_workout_id`` on the event payload."""
    from tempo.db import connect, init_schema
    from tempo.library_map import upsert_mapping
    from tempo.push import load_planned_sessions, push_week_async

    fake = _FakeClient(initial=[])
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    c = connect()
    init_schema(c)
    upsert_mapping(
        c,
        library_ref="tempo_bike_block",
        intervals_workout_id=42,
        intervals_name="Tempo block v1",
        intervals_folder_id=None,
        sport="bike",
    )
    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_duration_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("s1", "demo", "2026-W18", "2026-04-27", "bike", "tempo_bike_block", 3600),
    )
    planned = load_planned_sessions(c, week_id="2026-W18")

    await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
        verify=False,
    )

    assert len(fake.created) == 1
    assert fake.created[0]["plan_workout_id"] == 42
    c.close()


@pytest.mark.asyncio
async def test_push_payload_omits_workout_id_when_unmapped(monkeypatch, fake_config, tmp_data_dir: Path):
    """Fallback path: no mapping → no ``plan_workout_id`` field on the payload."""
    from tempo.db import connect, init_schema
    from tempo.push import load_planned_sessions, push_week_async

    fake = _FakeClient(initial=[])
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    c = connect()
    init_schema(c)
    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_duration_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("s1", "demo", "2026-W18", "2026-04-27", "bike", "tempo_bike_block", 3600),
    )
    planned = load_planned_sessions(c, week_id="2026-W18")
    await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
        verify=False,
    )
    assert len(fake.created) == 1
    assert "plan_workout_id" not in fake.created[0]
    c.close()


@pytest.mark.asyncio
async def test_push_marks_sessions_pushed(monkeypatch, fake_config, tmp_data_dir: Path):
    """``mark_pushed_conn`` should set pushed_to_intervals + intervals_event_id."""
    from tempo.db import connect, init_schema
    from tempo.push import push_week_async

    fake = _FakeClient(initial=[])
    monkeypatch.setattr("tempo.push.ICUClient", lambda cfg: fake)

    c = connect()
    init_schema(c)
    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, target_duration_s) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", "demo", "2026-W18", "2026-04-27", "run", 2700),
    )

    planned = [
        PlannedSession(
            id="s1", plan_id="demo", date="2026-04-27", sport="run", target_duration_s=2700
        )
    ]
    await push_week_async(
        config=fake_config,
        plan_id="demo",
        week_id="2026-W18",
        planned=planned,
        mark_pushed_conn=c,
    )

    row = c.execute(
        "SELECT pushed_to_intervals, intervals_event_id FROM sessions_planned WHERE id = 's1'"
    ).fetchone()
    assert row["pushed_to_intervals"] == 1
    assert row["intervals_event_id"] is not None
    c.close()
