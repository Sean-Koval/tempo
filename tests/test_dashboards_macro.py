"""Render tests for ``coach dashboard macro`` (tempo-mvh.2)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempo import plans
from tempo.cli import app
from tempo.dashboards import render_macro
from tempo.db import connect, init_schema

PLAN_ID = "2026-im-test"
START_WEEK = "2026-W10"  # Mon 2026-03-02
PHASES_YAML = (
    f"plan_id: {PLAN_ID}\n"
    "template: ironman_full_24wk\n"
    "start_date: 2026-03-02\n"
    "target_date: 2026-04-13\n"
    "total_weeks: 6\n"
    "weekly_hours_budget: 12\n"
    "phases:\n"
    f"  - id: base\n    start_week: {START_WEEK}\n    weeks: 3\n"
    "    weekly_tss_target: [350, 450]\n"
    f"  - id: build\n    start_week: 2026-W13\n    weeks: 3\n"
    "    weekly_tss_target: [450, 550]\n"
    "race_markers:\n"
    "  - week_id: 2026-W15\n    kind: A\n    note: spring tune-up\n"
)


def _seed_plan(tmp_path: Path) -> Path:
    pdir = tmp_path / "plans" / PLAN_ID
    pdir.mkdir(parents=True)
    (pdir / "plan.yaml").write_text(PHASES_YAML, encoding="utf-8")
    return pdir


def _seed_load(conn: sqlite3.Connection, today: date) -> None:
    for i in range(3):
        d = today - timedelta(days=i)
        conn.execute(
            "INSERT INTO load_daily(date, ctl, atl, tsb, ramp_7d) VALUES (?, ?, ?, ?, ?)",
            (d.isoformat(), 55.0 - i * 0.5, 50.0 - i * 0.3, 5.0, 0.5),
        )


def _seed_activities(conn: sqlite3.Connection) -> None:
    """Two completed weeks worth of TSS so the weekly-actual table populates."""
    week_a_mon = plans.week_start(START_WEEK)
    week_b_mon = plans.week_start(plans.shift_week(START_WEEK, weeks=1))
    for label, wstart, tss in (
        ("a1", week_a_mon, 380.0),
        ("b1", week_b_mon, 410.0),
    ):
        conn.execute(
            "INSERT INTO activities(id, start_date, sport, duration_s, tss) "
            "VALUES (?, ?, ?, ?, ?)",
            (label, wstart.isoformat() + "T07:00:00", "bike", 3600, tss),
        )


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("tempo.plans.repo_root", lambda: tmp_path)
    monkeypatch.setattr("tempo.dashboards.common.repo_root", lambda: tmp_path)
    _seed_plan(tmp_path)
    today = plans.week_start(plans.shift_week(START_WEEK, weeks=1))  # mid-plan
    conn = connect()
    init_schema(conn)
    _seed_load(conn, today)
    _seed_activities(conn)
    yield tmp_path, conn, today
    conn.close()


def test_render_macro_header_and_template(seeded):
    _, conn, today = seeded
    html = render_macro(plan_id=PLAN_ID, conn=conn, today=today)
    assert PLAN_ID in html
    assert "ironman_full_24wk" in html
    assert "2026-03-02" in html  # start_date
    assert "2026-04-13" in html  # target_date


def test_render_macro_gantt_lists_each_phase(seeded):
    _, conn, today = seeded
    html = render_macro(plan_id=PLAN_ID, conn=conn, today=today)
    assert "<pre class='mermaid'>" in html
    # Phase ids appear in the Gantt source.
    assert "base" in html
    assert "build" in html
    # Phase durations: 3 weeks → 21 days.
    assert "21d" in html


def test_render_macro_current_position_shows_phase(seeded):
    _, conn, today = seeded
    html = render_macro(plan_id=PLAN_ID, conn=conn, today=today)
    # today sits in the second week of base.
    week_id = plans.week_id_for(today)
    assert week_id in html
    assert "Current position" in html
    assert "base" in html
    assert "Target weekly TSS" in html


def test_render_macro_weekly_tss_table_includes_actuals(seeded):
    _, conn, today = seeded
    html = render_macro(plan_id=PLAN_ID, conn=conn, today=today)
    assert "Weekly TSS" in html
    # First-week actual (380) and second-week actual (410) appear.
    assert "380" in html
    assert "410" in html
    # Mid-target (400 for base [350,450]) appears.
    assert "400" in html


def test_render_macro_race_markers_section(seeded):
    _, conn, today = seeded
    html = render_macro(plan_id=PLAN_ID, conn=conn, today=today)
    assert "Race markers" in html
    assert "spring tune-up" in html
    # Mermaid milestone for the A race should be in the gantt source.
    assert "milestone" in html


def test_render_macro_missing_plan_renders_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("tempo.plans.repo_root", lambda: tmp_path)
    monkeypatch.setattr("tempo.dashboards.common.repo_root", lambda: tmp_path)
    html = render_macro(plan_id="nonexistent")
    assert "not found" in html
    assert "nonexistent" in html


def test_render_macro_mermaid_script_present(seeded):
    _, conn, today = seeded
    html = render_macro(plan_id=PLAN_ID, conn=conn, today=today)
    assert "mermaid.initialize" in html
    assert "cdn.jsdelivr.net/npm/mermaid" in html


def test_cli_dashboard_macro_writes_file(seeded, tmp_path: Path):
    _, conn, _ = seeded
    conn.close()
    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "macro", "--plan-id", PLAN_ID])
    assert result.exit_code == 0, result.stdout
    htmls = list((tmp_path / "dashboards").glob("macro-*.html"))
    assert len(htmls) == 1
    body = htmls[0].read_text(encoding="utf-8")
    assert PLAN_ID in body
    assert "Weekly TSS" in body
