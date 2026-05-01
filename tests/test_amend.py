"""Unit tests for ``tempo.amend`` — the atomic plan/week amendment surface.

Each test seeds a synthetic plan under a tmp root and asserts both the
file changes (target_date, phase shifts, changelog appends) and the
side-effects (decisions row, sessions_planned upserts).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tempo.db import connect, init_schema


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tempo import amend as amend_mod
    from tempo import athlete as athlete_mod
    from tempo import composition as comp_mod
    from tempo import plans as plans_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(plans_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(amend_mod, "_athlete", athlete_mod, raising=False)
    monkeypatch.setattr(comp_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def db(isolated_root: Path):
    c = connect()
    init_schema(c)
    yield c
    c.close()


def _seed_plan(root: Path, *, plan_id: str = "demo", target_date: str = "2026-12-12") -> Path:
    plan_dir = root / "plans" / plan_id
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text(
        f"""\
plan_id: {plan_id}
goal_ref: {plan_id}
template: marathon_18wk
start_date: 2026-08-01
target_date: {target_date}        # PLACEHOLDER race date — confirm and re-bootstrap if it shifts
total_weeks: 18

phases:

  - id: aerobic_base_1
    start_week: 2026-W31
    weeks: 6
    weekly_tss_target: [350, 490]

  - id: build_run
    start_week: 2026-W37
    weeks: 6
    weekly_tss_target: [430, 580]

  - id: peak_run
    start_week: 2026-W43
    weeks: 4
    weekly_tss_target: [430, 540]

  - id: taper_run
    start_week: 2026-W47
    weeks: 2
    weekly_tss_target: [220, 320]
""",
        encoding="utf-8",
    )
    (plan_dir / "goal.yaml").write_text(
        f"""\
id: {plan_id}
name: "Demo Marathon"
date: {target_date}
distance: marathon
priority: A
location: "TBD"
""",
        encoding="utf-8",
    )
    return plan_dir


# ---------------------------------------------------------------------------
# shift-target
# ---------------------------------------------------------------------------


def test_shift_target_dry_run_does_not_write(isolated_root: Path, db):
    from tempo.amend import shift_target

    plan_dir = _seed_plan(isolated_root)
    before_plan = (plan_dir / "plan.yaml").read_text(encoding="utf-8")

    result = shift_target(
        "demo",
        days_delta=7,
        reason="weather move",
        dry_run=True,
        today=date(2026, 7, 27),
    )

    assert not result.applied
    # Plan file untouched on disk.
    assert (plan_dir / "plan.yaml").read_text(encoding="utf-8") == before_plan
    # But the dry-run still reports the planned diff.
    plan_change = next(c for c in result.files if c.label == "plan.yaml")
    assert "2026-12-19" in plan_change.after  # +7d
    assert "2026-W32" in plan_change.after  # phase shifted by 1 week


def test_shift_target_applies_when_not_dry_run(isolated_root: Path, db):
    from tempo.amend import shift_target

    plan_dir = _seed_plan(isolated_root)

    result = shift_target(
        "demo",
        days_delta=14,
        reason="course closure",
        dry_run=False,
        today=date(2026, 7, 27),
    )

    assert result.applied
    plan_text = (plan_dir / "plan.yaml").read_text(encoding="utf-8")
    goal_text = (plan_dir / "goal.yaml").read_text(encoding="utf-8")
    changelog_text = (plan_dir / "changelog.md").read_text(encoding="utf-8")

    assert "target_date: 2026-12-26" in plan_text
    # The original placeholder comment must survive — that's the whole point.
    assert "# PLACEHOLDER race date" in plan_text
    assert "date: 2026-12-26" in goal_text
    assert "shift-target" in changelog_text
    assert "course closure" in changelog_text

    # Decisions row written.
    rows = db.execute("SELECT scope, kind, rationale FROM decisions").fetchall()
    assert len(rows) == 1
    assert rows[0]["scope"] == "plan:demo"
    assert "shift-target" in rows[0]["rationale"]


def test_shift_target_explicit_date(isolated_root: Path, db):
    from tempo.amend import shift_target

    _seed_plan(isolated_root)
    result = shift_target(
        "demo",
        target="2027-01-09",
        reason="rescheduled",
        dry_run=True,
        today=date(2026, 7, 27),
    )
    plan_change = next(c for c in result.files if c.label == "plan.yaml")
    assert "2027-01-09" in plan_change.after


def test_shift_target_zero_delta_raises(isolated_root: Path, db):
    from tempo.amend import AmendError, shift_target

    _seed_plan(isolated_root)
    with pytest.raises(AmendError, match="0 days"):
        shift_target("demo", days_delta=0, reason="x", dry_run=True, today=date(2026, 7, 27))


def test_shift_target_only_shifts_future_phases(isolated_root: Path, db):
    """A phase that's already started should retain its start_week."""
    from tempo.amend import shift_target

    _seed_plan(isolated_root)
    # Today is in the middle of the first phase (W31 + 4 = W35).
    result = shift_target(
        "demo",
        days_delta=7,
        reason="x",
        dry_run=True,
        today=date.fromisocalendar(2026, 35, 1),
    )
    plan_change = next(c for c in result.files if c.label == "plan.yaml")
    after = plan_change.after
    # First phase still W31 (in the past — shouldn't shift)
    assert "start_week: 2026-W31" in after
    # build_run was W37, becomes W38; peak W43 → W44; taper W47 → W48
    assert "start_week: 2026-W38" in after
    assert "start_week: 2026-W44" in after
    assert "start_week: 2026-W48" in after


# ---------------------------------------------------------------------------
# week amend-session
# ---------------------------------------------------------------------------


def test_amend_session_creates_block(isolated_root: Path, db):
    from tempo.amend import amend_session

    plan_dir = _seed_plan(isolated_root)
    result = amend_session(
        "demo",
        week_id="2026-W18",
        day="sat",
        duration="14km",
        zone="z1",
        reason="calf flag",
        dry_run=False,
        today=date(2026, 5, 3),
    )
    assert result.applied
    week_file = plan_dir / "weeks" / "2026-W18.md"
    text = week_file.read_text(encoding="utf-8")
    assert "## Amendments" in text
    assert "calf flag" in text
    assert "**Duration:** 14km" in text
    assert "**Zone:** z1" in text
    # Changelog touched
    assert "amend" in (plan_dir / "changelog.md").read_text(encoding="utf-8")
    # Decisions row
    rows = db.execute("SELECT scope FROM decisions").fetchall()
    assert any(r["scope"] == "week:2026-W18" for r in rows)


def test_amend_session_appends_to_existing_section(isolated_root: Path, db):
    from tempo.amend import amend_session

    plan_dir = _seed_plan(isolated_root)
    week_file = plan_dir / "weeks" / "2026-W18.md"
    week_file.parent.mkdir(parents=True)
    # ISO 2026-W18 spans Mon 2026-04-27 .. Sun 2026-05-03.
    week_file.write_text(
        "# Week 2026-W18\n\n## Amendments\n\n### 2026-04-27 (Monday)\n- existing\n",
        encoding="utf-8",
    )
    amend_session(
        "demo",
        week_id="2026-W18",
        day="sat",
        zone="z1",
        reason="calf",
        dry_run=False,
        today=date(2026, 5, 3),
    )
    text = week_file.read_text(encoding="utf-8")
    # Existing entry preserved; new one added
    assert text.count("## Amendments") == 1
    assert "### 2026-04-27" in text
    assert "### 2026-05-02" in text  # Sat of W18 (ISO)


def test_amend_session_requires_some_change(isolated_root: Path, db):
    from tempo.amend import AmendError, amend_session

    _seed_plan(isolated_root)
    with pytest.raises(AmendError, match="at least one"):
        amend_session(
            "demo",
            week_id="2026-W18",
            day="sat",
            reason="x",
        )


def test_amend_session_updates_db_row(isolated_root: Path, db):
    from tempo.amend import amend_session

    _seed_plan(isolated_root)
    # ISO 2026-W18 Saturday = 2026-05-02.
    db.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_duration_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess1", "demo", "2026-W18", "2026-05-02", "run", "long-run", 5400),
    )
    amend_session(
        "demo",
        week_id="2026-W18",
        day="sat",
        duration="60min",
        reason="calf",
        dry_run=False,
        today=date(2026, 5, 3),
    )
    row = db.execute("SELECT target_duration_s FROM sessions_planned WHERE id='sess1'").fetchone()
    assert row["target_duration_s"] == 3600


# ---------------------------------------------------------------------------
# insert-test
# ---------------------------------------------------------------------------


def test_insert_test_creates_session_and_followup(isolated_root: Path, db):
    from tempo.amend import insert_test

    plan_dir = _seed_plan(isolated_root)
    result = insert_test(
        "demo",
        slot="2026-W22-Wed",
        kind="5k_tt",
        reason="recalibrate run threshold",
        dry_run=False,
        today=date(2026, 5, 25),
    )
    assert result.applied
    # Week file created with structured block
    week_text = (plan_dir / "weeks" / "2026-W22.md").read_text(encoding="utf-8")
    assert "## Inserted test — 5k_tt" in week_text
    # Calibration follow-up registered
    followups = (plan_dir / "calibration_followups.md").read_text(encoding="utf-8")
    assert "5k_tt" in followups
    # Sessions row inserted
    row = db.execute(
        "SELECT sport, library_ref FROM sessions_planned WHERE id LIKE 'demo-2026-05-27-5k_tt'"
    ).fetchone()
    assert row is not None
    assert row["sport"] == "run"
    assert row["library_ref"] == "5k_time_trial"


def test_insert_test_rejects_unknown_kind(isolated_root: Path, db):
    from tempo.amend import AmendError, insert_test

    _seed_plan(isolated_root)
    with pytest.raises(AmendError, match="unknown test kind"):
        insert_test(
            "demo",
            slot="2026-W22-Wed",
            kind="bench_press",  # type: ignore[arg-type]
            reason="x",
        )


def test_insert_test_rejects_bad_slot(isolated_root: Path, db):
    from tempo.amend import AmendError, insert_test

    _seed_plan(isolated_root)
    with pytest.raises(AmendError, match="must be like"):
        insert_test(
            "demo",
            slot="2026-W22 Wednesday",
            kind="ftp_test",
            reason="x",
        )


# ---------------------------------------------------------------------------
# switch-target
# ---------------------------------------------------------------------------


def _seed_athlete(root: Path, races: list[dict]) -> None:
    athlete_dir = root / "athlete"
    athlete_dir.mkdir(parents=True, exist_ok=True)
    import yaml as _y

    (athlete_dir / "race-calendar.yaml").write_text(
        _y.safe_dump({"races": races}, sort_keys=False),
        encoding="utf-8",
    )


def test_switch_target_same_distance_shifts_dates(isolated_root: Path, db):
    from tempo.amend import switch_target

    plan_dir = _seed_plan(isolated_root, target_date="2026-12-12")
    _seed_athlete(
        isolated_root,
        races=[
            {"id": "demo", "type": "marathon", "date": "2026-12-12", "priority": "A"},
            {
                "id": "nyc-2026",
                "type": "marathon",
                "name": "NYC Marathon",
                "date": "2026-11-01",
                "priority": "A",
                "location": "New York, NY",
            },
        ],
    )

    result = switch_target(
        "demo",
        new_race_id="nyc-2026",
        reason="travel forced switch",
        dry_run=False,
        today=date(2026, 7, 27),
    )

    assert result.applied
    goal_text = (plan_dir / "goal.yaml").read_text(encoding="utf-8")
    plan_text = (plan_dir / "plan.yaml").read_text(encoding="utf-8")
    assert "id: nyc-2026" in goal_text
    assert "date: 2026-11-01" in goal_text
    assert "target_date: 2026-11-01" in plan_text


def test_switch_target_unknown_race_raises(isolated_root: Path, db):
    from tempo.amend import AmendError, switch_target

    _seed_plan(isolated_root)
    _seed_athlete(isolated_root, races=[{"id": "demo", "date": "2026-12-12"}])
    with pytest.raises(AmendError, match="not found"):
        switch_target("demo", new_race_id="ghost-race", reason="x", dry_run=True)


def test_switch_target_refuses_cancelled_race(isolated_root: Path, db):
    """tempo-wk7: re-anchoring onto a cancelled race is a hard refusal."""
    from tempo.amend import AmendError, switch_target

    _seed_plan(isolated_root, target_date="2026-12-12")
    _seed_athlete(
        isolated_root,
        races=[
            {"id": "demo", "type": "marathon", "date": "2026-12-12", "priority": "A"},
            {
                "id": "dead-race",
                "type": "marathon",
                "name": "Cancelled Marathon",
                "date": "2026-11-01",
                "priority": "A",
                "status": "cancelled",
                "cancelled_reason": "course closure",
            },
        ],
    )
    with pytest.raises(AmendError, match="cancelled"):
        switch_target("demo", new_race_id="dead-race", reason="x", dry_run=True)
