"""Tests for ``tempo.calibration`` — the plan calibration-debt detector."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from tempo import calibration


def _seed_plan(root: Path, *, plan_id: str = "p1") -> None:
    pdir = root / "plans" / plan_id
    pdir.mkdir(parents=True)
    (pdir / "plan.yaml").write_text(
        yaml.safe_dump({"plan_id": plan_id, "goal_id": "race-1", "phases": []}),
        encoding="utf-8",
    )


def _seed_athlete(
    root: Path,
    *,
    profile: dict | None = None,
    races: list[dict] | None = None,
    preferences: str | None = None,
    injury_log: str | None = None,
) -> None:
    adir = root / "athlete"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "profile.yaml").write_text(
        yaml.safe_dump(profile or {}), encoding="utf-8"
    )
    if races is not None:
        (adir / "race-calendar.yaml").write_text(
            yaml.safe_dump({"races": races}), encoding="utf-8"
        )
    if preferences is not None:
        (adir / "preferences.md").write_text(preferences, encoding="utf-8")
    if injury_log is not None:
        (adir / "injury-log.md").write_text(injury_log, encoding="utf-8")


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch all calibration submodules to read from a tmp project root."""
    from tempo import athlete as athlete_mod
    from tempo import plans as plans_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(plans_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(calibration, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_no_plan_returns_empty(project_root: Path) -> None:
    assert calibration.calibration_debt() == []


def test_full_state_emits_no_debts(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={
            "athlete": {"weight_kg": 75, "name": "Tester"},
            "thresholds": {
                "ftp_w": {"value": 280, "set_at": "2026-04-15", "source": "field_test"},
                "lthr_bpm": {"value": 168, "set_at": "2026-04-01", "source": "field_test"},
                "max_hr": {"value": 188, "set_at": "2026-01-01", "source": "field_test"},
            },
        },
        races=[
            {
                "id": "race-1",
                "name": "Local 70.3",
                "date": "2027-05-01",
                "location": "Lake Placid, NY",
                "goals": {"finish_time": "5:30:00"},
            }
        ],
        preferences=(
            "## Schedule & logistics\n\n"
            "- Typical weekly training hours: 10\n"
            "- Available days for long sessions: Sat + Sun\n"
            "- Preferred hard-day pattern: Tue + Thu + Sat\n"
        ),
    )

    # Seed 30 days of load history.
    from datetime import date, timedelta

    from tempo.db import connect, init_schema

    conn = connect()
    try:
        init_schema(conn)
        for i in range(30):
            d = (date.today() - timedelta(days=i)).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO load_daily(date, ctl, atl, tsb) VALUES (?, ?, ?, ?)",
                (d, 50.0, 45.0, 5.0),
            )
    finally:
        conn.close()

    debts = calibration.calibration_debt(conn=None, today=date(2026, 5, 1))
    assert debts == []


def test_empty_profile_emits_ftp_lthr_weight_debts(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(project_root, profile={}, races=[])

    debts = calibration.calibration_debt()
    fields = {d.field for d in debts}
    assert "athlete.profile.thresholds.ftp_w" in fields
    assert "athlete.profile.thresholds.lthr_bpm" in fields
    assert "athlete.profile.athlete.weight_kg" in fields
    # FTP debt is severity warn (not fail) — still planable.
    ftp_debt = next(d for d in debts if d.field == "athlete.profile.thresholds.ftp_w")
    assert ftp_debt.severity == "warn"


def test_tbd_race_location_is_fail_severity(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {"ftp_w": 280, "lthr_bpm": 168},
        },
        races=[
            {
                "id": "race-1",
                "name": "Half Ironman (TBD venue)",
                "date": "2027-05-01",
                "location": "TBD",
                "goals": {"finish_time": "TBD"},
            }
        ],
    )
    debts = calibration.calibration_debt()
    location_debts = [d for d in debts if "location" in d.field]
    assert len(location_debts) == 1
    assert location_debts[0].severity == "fail"


def test_placeholder_preferences_detected(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={"thresholds": {"ftp_w": 280, "lthr_bpm": 168}, "athlete": {"weight_kg": 75}},
        races=[],
        preferences=(
            "## Schedule & logistics\n\n"
            "- Typical weekly training hours: # TODO\n"
            "- Available days for long sessions: # e.g. Sat + Sun\n"
            "- Preferred hard-day pattern: Tue + Thu\n"  # this one is filled
        ),
    )
    debts = calibration.calibration_debt()
    fields = {d.field for d in debts}
    assert "athlete.preferences.weekly_hours" in fields
    assert "athlete.preferences.long_day_pattern" in fields
    assert "athlete.preferences.hard_day_pattern" not in fields


def test_active_injury_with_empty_research_corpus(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={"thresholds": {"ftp_w": 280, "lthr_bpm": 168}, "athlete": {"weight_kg": 75}},
        races=[],
        injury_log=(
            "# Injury Log\n\n"
            "## Active\n\n"
            "### 2026-04-25 — left tibia (BSI grade 2) — severity 4\n\n"
            "- Status: active\n"
        ),
    )
    debts = calibration.calibration_debt()
    fields = {d.field for d in debts}
    assert "knowledge.research.injury" in fields


def test_active_injury_with_research_present_no_debt(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={"thresholds": {"ftp_w": 280, "lthr_bpm": 168}, "athlete": {"weight_kg": 75}},
        races=[],
        injury_log=(
            "# Injury Log\n\n"
            "## Active\n\n"
            "### 2026-04-25 — left tibia (BSI grade 2) — severity 4\n\n"
        ),
    )
    research_dir = project_root / "knowledge" / "research" / "2026" / "04"
    research_dir.mkdir(parents=True)
    (research_dir / "warden-2014.md").write_text("# Warden 2014\n\nplaceholder", encoding="utf-8")

    debts = calibration.calibration_debt()
    fields = {d.field for d in debts}
    assert "knowledge.research.injury" not in fields


def test_zone_provenance_struct_no_debt_when_fresh(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {
                "ftp_w": {"value": 280, "set_at": "2026-04-15", "source": "field_test"},
                "lthr_bpm": {"value": 168, "set_at": "2026-03-01", "source": "race_result"},
            },
        },
        races=[],
    )
    debts = calibration.calibration_debt(today=date(2026, 5, 1))
    fields = {d.field for d in debts}
    assert "athlete.profile.thresholds.ftp_w.set_at" not in fields
    assert "athlete.profile.thresholds.lthr_bpm.set_at" not in fields
    assert "athlete.profile.thresholds.ftp_w" not in fields  # value is set


def test_zone_provenance_run_pace_stale_at_91_days(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {
                "ftp_w": {"value": 280, "set_at": "2026-04-15", "source": "field_test"},
                "lthr_bpm": {"value": 168, "set_at": "2026-03-01", "source": "race_result"},
                "run_threshold_pace": {
                    "value": "4:15/km",
                    "set_at": "2026-01-30",
                    "source": "race_result",
                },
            },
        },
        races=[],
    )
    # 91d after 2026-01-30 = 2026-05-01.
    debts = calibration.calibration_debt(today=date(2026, 5, 1))
    stale = [d for d in debts if d.field == "athlete.profile.thresholds.run_threshold_pace.set_at"]
    assert len(stale) == 1
    assert stale[0].severity == "warn"
    assert "stale" in stale[0].message.lower()


def test_zone_provenance_run_pace_fresh_at_30_days(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {
                "ftp_w": {"value": 280, "set_at": "2026-04-15", "source": "field_test"},
                "lthr_bpm": {"value": 168, "set_at": "2026-03-01", "source": "race_result"},
                "run_threshold_pace": {
                    "value": "4:15/km",
                    "set_at": "2026-04-01",
                    "source": "race_result",
                },
            },
        },
        races=[],
    )
    debts = calibration.calibration_debt(today=date(2026, 5, 1))
    fields = {d.field for d in debts}
    assert "athlete.profile.thresholds.run_threshold_pace.set_at" not in fields


def test_zone_provenance_legacy_scalar_treated_as_stale(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={
            "athlete": {"weight_kg": 75},
            # All scalars (legacy shape) — no set_at known anywhere.
            "thresholds": {
                "ftp_w": 280,
                "lthr_bpm": 168,
                "run_threshold_pace": "4:15/km",
                "swim_css_pace": "1:35/100m",
                "max_hr": 188,
            },
        },
        races=[],
    )
    debts = calibration.calibration_debt(today=date(2026, 5, 1))
    stale_fields = {
        d.field for d in debts if d.field.startswith("athlete.profile.thresholds.")
        and d.field.endswith(".set_at")
    }
    # Every populated zone with a freshness window emits a stale debt.
    assert "athlete.profile.thresholds.ftp_w.set_at" in stale_fields
    assert "athlete.profile.thresholds.lthr_bpm.set_at" in stale_fields
    assert "athlete.profile.thresholds.run_threshold_pace.set_at" in stale_fields
    assert "athlete.profile.thresholds.swim_css_pace.set_at" in stale_fields
    assert "athlete.profile.thresholds.max_hr.set_at" in stale_fields


def test_zone_provenance_blank_value_no_stale_debt(project_root: Path) -> None:
    """A zone that's never been set shouldn't double-report as stale."""
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {
                "ftp_w": {"value": None, "set_at": None, "source": "manual_estimate"},
                "lthr_bpm": 168,
            },
        },
        races=[],
    )
    debts = calibration.calibration_debt(today=date(2026, 5, 1))
    fields = {d.field for d in debts}
    # "Not set" debt fires:
    assert "athlete.profile.thresholds.ftp_w" in fields
    # Stale debt does NOT fire — the value is empty:
    assert "athlete.profile.thresholds.ftp_w.set_at" not in fields


def test_load_history_below_threshold_emits_warn(project_root: Path) -> None:
    _seed_plan(project_root)
    _seed_athlete(
        project_root,
        profile={"thresholds": {"ftp_w": 280, "lthr_bpm": 168}, "athlete": {"weight_kg": 75}},
        races=[],
    )

    from tempo.db import connect, init_schema

    conn = connect()
    try:
        init_schema(conn)
    finally:
        conn.close()

    debts = calibration.calibration_debt()
    load_debts = [d for d in debts if d.field == "coach.db.load_daily"]
    assert len(load_debts) == 1
    assert load_debts[0].severity == "warn"
    assert "0 day" in load_debts[0].message
