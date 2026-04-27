"""Snapshot-level unit tests for ``tempo.status``.

Exercises the row-building logic in isolation from the CLI render path,
so we can assert severity hinting per row without parsing Rich output.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tempo.db import connect, init_schema
from tempo.events import log_event
from tempo.status import build_snapshot


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect plans / athlete reads to ``tmp_path`` and DB writes to ``tmp_path/data``."""
    from tempo import athlete as athlete_mod
    from tempo import calibration as calibration_mod
    from tempo import plans as plans_mod

    monkeypatch.setattr(athlete_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(plans_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(calibration_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def empty_db(isolated_root: Path):
    c = connect()
    init_schema(c)
    yield c
    c.close()


def _row(snap, label: str):
    for row in snap.rows:
        if row.label == label:
            return row
    raise AssertionError(f"row {label!r} missing — got {[r.label for r in snap.rows]}")


def test_snapshot_no_plan_yields_warn_row(empty_db, isolated_root: Path):
    snap = build_snapshot(conn=empty_db)
    assert snap.plan_id is None
    assert _row(snap, "Plan").severity == "warn"


def test_snapshot_no_load_yields_warn_row(empty_db):
    snap = build_snapshot(conn=empty_db)
    assert _row(snap, "Load").severity == "warn"


def test_snapshot_includes_load_when_present(empty_db):
    today = date.today().isoformat()
    empty_db.execute(
        "INSERT INTO load_daily(date, ctl, atl, tsb, ramp_7d, ctl_bike, ctl_run, ctl_swim) "
        "VALUES (?, 60, 55, 5, 0.5, 30, 20, 10)",
        (today,),
    )
    snap = build_snapshot(conn=empty_db)
    assert snap.load is not None
    assert snap.load.ctl == 60
    assert _row(snap, "Load").severity == "ok"


def test_snapshot_flags_stale_sync(empty_db, isolated_root: Path):
    long_ago = datetime.now(UTC) - timedelta(hours=80)
    log_event("sync", {"days": 90}, now=long_ago)
    snap = build_snapshot(conn=empty_db)
    sync_row = _row(snap, "Sync")
    assert sync_row.severity == "alert"


def test_snapshot_flags_warn_sync(empty_db, isolated_root: Path):
    moderate = datetime.now(UTC) - timedelta(hours=30)
    log_event("sync", {"days": 90}, now=moderate)
    snap = build_snapshot(conn=empty_db)
    assert _row(snap, "Sync").severity == "warn"


def test_snapshot_fresh_sync_is_ok(empty_db, isolated_root: Path):
    log_event("sync", {"days": 90})
    snap = build_snapshot(conn=empty_db)
    assert _row(snap, "Sync").severity == "ok"


def test_snapshot_json_round_trip(empty_db):
    snap = build_snapshot(conn=empty_db)
    payload = json.loads(snap.to_json())
    assert payload["as_of"] == snap.as_of
    assert "rows" not in payload  # presentation only


def test_snapshot_active_injury_renders_alert(isolated_root: Path, empty_db):
    athlete = isolated_root / "athlete"
    athlete.mkdir()
    (athlete / "injury-log.md").write_text(
        "# Injury Log\n\n## Active\n\n### 2026-04-25 — left tibia (BSI grade 2) — severity 4\n"
        "- onset: 2026-04-20\n",
        encoding="utf-8",
    )
    snap = build_snapshot(conn=empty_db, root=isolated_root)
    assert snap.active_injury_flags
    inj_row = _row(snap, "Injuries")
    assert inj_row.severity == "alert"


def test_snapshot_phase_target_ctl_derived(isolated_root: Path, empty_db):
    plans_dir = isolated_root / "plans" / "demo"
    plans_dir.mkdir(parents=True)
    today = date.today()
    week_id = f"{today.isocalendar()[0]:04d}-W{today.isocalendar()[1]:02d}"
    (plans_dir / "plan.yaml").write_text(
        f"""\
plan_id: demo
goal_ref: demo
template: marathon_18wk
start_date: {today.isoformat()}
target_date: {(today + timedelta(days=120)).isoformat()}
total_weeks: 18
phases:
  - id: aerobic_base_1
    start_week: {week_id}
    weeks: 4
    weekly_tss_target: [350, 490]
""",
        encoding="utf-8",
    )
    snap = build_snapshot(conn=empty_db, root=isolated_root)
    # Midpoint 420 / 7 ≈ 60.0 steady-state CTL target
    assert snap.target_ctl == pytest.approx(60.0, abs=0.5)
    assert snap.phase_id == "aerobic_base_1"
    assert snap.phase_week_index == 1
