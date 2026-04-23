"""Smoke tests for the `coach` CLI.

Full `coach sync` hits the intervals API — we stub that via the same
respx pattern used in test_sync.py. `coach status` and `coach push-week`
are exercised against a seeded SQLite DB.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempo.cli import app
from tempo.db import connect, init_schema

runner = CliRunner()


@pytest.fixture
def seeded_db(tmp_data_dir: Path):
    c = connect()
    init_schema(c)
    _seed_week_progress(c)
    _seed_load_and_wellness(c)
    yield c
    c.close()


def _seed_week_progress(c: sqlite3.Connection) -> None:
    iso_year, iso_week, _ = date.today().isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"
    monday = date.fromisocalendar(iso_year, iso_week, 1)

    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_tss, target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp1", "plan-a", week_id, monday.isoformat(), "bike", "endurance-2h", 95, 7200, "aerobic_base"),
    )
    c.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_tss, target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp2", "plan-a", week_id, (monday + timedelta(days=1)).isoformat(), "run", "z2-45min", 40, 2700, "aerobic_base"),
    )
    c.execute(
        "INSERT INTO adherence(planned_session_id, completed) VALUES ('sp1', 1)"
    )


def _seed_load_and_wellness(c: sqlite3.Connection) -> None:
    today = date.today().isoformat()
    c.execute(
        "INSERT INTO load_daily(date, ctl, atl, tsb, ctl_bike, ctl_run, ctl_swim, ramp_7d) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (today, 62.5, 55.0, 7.5, 42.1, 15.4, 5.0, 1.8),
    )
    c.execute(
        "INSERT INTO wellness_daily(date, sleep_h, sleep_score, hrv, rhr, readiness) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (today, 7.8, 85, 64.2, 48, 78),
    )


def test_status_reports_ctl(seeded_db):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "CTL" in result.stdout
    assert "62.5" in result.stdout
    assert "7.5" in result.stdout or "+7.5" in result.stdout  # TSB


def test_status_shows_week_progress(seeded_db):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    iso_year, iso_week, _ = date.today().isocalendar()
    assert f"{iso_year}-W{iso_week:02d}" in result.stdout
    assert "1/2 completed" in result.stdout


def test_status_without_load(tmp_data_dir: Path):
    c = connect()
    init_schema(c)
    c.close()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "No load data" in result.stdout


def test_push_week_dry_runs(seeded_db, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    iso_year, iso_week, _ = date.today().isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"
    result = runner.invoke(app, ["push-week", week_id])
    assert result.exit_code == 0
    assert "DRY RUN" in result.stdout
    assert "endurance-2h" in result.stdout
    assert "dry-run" in result.stdout.lower()


def test_push_week_empty(tmp_data_dir: Path):
    c = connect()
    init_schema(c)
    c.close()
    result = runner.invoke(app, ["push-week", "2099-W01"])
    assert result.exit_code == 0
    assert "No planned sessions" in result.stdout


def test_help_lists_three_verbs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for verb in ("sync", "status", "push-week"):
        assert verb in result.stdout
