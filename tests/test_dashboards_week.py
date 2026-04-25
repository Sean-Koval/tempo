"""Render tests for ``coach dashboard week`` (tempo-mvh.1)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempo import plans
from tempo.cli import app
from tempo.dashboards import render_week
from tempo.db import connect, init_schema

WEEK_ID = "2026-W17"
PLAN_ID = "2026-im-test"


def _seed_planned(conn: sqlite3.Connection) -> None:
    monday = plans.week_start(WEEK_ID)
    conn.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_tss, target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp1", PLAN_ID, WEEK_ID, monday.isoformat(), "bike",
         "endurance-2h", 95.0, 7200, "aerobic_base"),
    )
    conn.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_tss, target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp2", PLAN_ID, WEEK_ID, (monday + timedelta(days=2)).isoformat(),
         "run", "tempo-45min", 55.0, 2700, "tempo"),
    )
    conn.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_tss, target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp3", PLAN_ID, WEEK_ID, (monday + timedelta(days=4)).isoformat(),
         "swim", "css-3000m", 45.0, 3600, "threshold"),
    )

    # Activity matched to sp1 (completed)
    conn.execute(
        "INSERT INTO activities(id, start_date, sport, duration_s, tss) VALUES (?, ?, ?, ?, ?)",
        ("a1", monday.isoformat() + "T07:00:00", "bike", 7300, 102.0),
    )
    conn.execute(
        "INSERT INTO adherence(planned_session_id, activity_id, completed, tss_delta, "
        "duration_delta_s) VALUES (?, ?, ?, ?, ?)",
        ("sp1", "a1", 1, 7.0, 100),
    )
    # sp2 skipped, sp3 left untouched (pending)
    conn.execute(
        "INSERT INTO adherence(planned_session_id, completed, reason) VALUES (?, ?, ?)",
        ("sp2", 0, "skipped: tweaked calf"),
    )


def _seed_wellness_and_load(conn: sqlite3.Connection) -> None:
    monday = plans.week_start(WEEK_ID)
    for i in range(7):
        d = monday + timedelta(days=i)
        conn.execute(
            "INSERT INTO wellness_daily(date, sleep_h, hrv, rhr, readiness) "
            "VALUES (?, ?, ?, ?, ?)",
            (d.isoformat(), 7.0 + i * 0.1, 60.0 + i, 50 - i, 70 + i),
        )
        conn.execute(
            "INSERT INTO load_daily(date, ctl, atl, tsb, ramp_7d) VALUES (?, ?, ?, ?, ?)",
            (d.isoformat(), 60.0 + i * 0.5, 55.0 + i * 0.3, 5.0 - i * 0.2, 0.4),
        )


def _seed_plan_yaml(tmp_path: Path) -> None:
    pdir = tmp_path / "plans" / PLAN_ID
    pdir.mkdir(parents=True)
    monday = plans.week_start(WEEK_ID)
    (pdir / "plan.yaml").write_text(
        "plan_id: " + PLAN_ID + "\n"
        "template: ironman_full_24wk\n"
        "phases:\n"
        f"  - id: build\n    start_week: {WEEK_ID}\n    weeks: 4\n"
        "    weekly_tss_target: [400, 500]\n",
        encoding="utf-8",
    )
    # Use the seeded monday so changelog parses cleanly even if WEEK_ID drifts.
    _ = monday


def _seed_changelog(tmp_path: Path) -> None:
    pdir = tmp_path / "plans" / PLAN_ID
    monday = plans.week_start(WEEK_ID)
    in_window = (monday + timedelta(days=2)).isoformat()
    out_window = (monday - timedelta(days=10)).isoformat()
    (pdir / "changelog.md").write_text(
        f"# Changelog\n\n## {in_window}\n\nMoved sp2 to Thursday — calf flare-up.\n\n"
        f"## {out_window}\n\nUnrelated earlier entry.\n",
        encoding="utf-8",
    )


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("tempo.plans.repo_root", lambda: tmp_path)
    monkeypatch.setattr("tempo.dashboards.common.repo_root", lambda: tmp_path)
    _seed_plan_yaml(tmp_path)
    _seed_changelog(tmp_path)
    conn = connect()
    init_schema(conn)
    _seed_planned(conn)
    _seed_wellness_and_load(conn)
    yield tmp_path, conn
    conn.close()


def test_render_week_includes_header_and_sessions(seeded):
    _, conn = seeded
    html = render_week(week_id=WEEK_ID, plan_id=PLAN_ID, conn=conn)

    assert "<!doctype html>" in html.lower()
    assert WEEK_ID in html
    assert PLAN_ID in html
    # Each library_ref appears in the sessions table.
    assert "endurance-2h" in html
    assert "tempo-45min" in html
    assert "css-3000m" in html
    # Completion summary mentions 1/3 completed.
    assert "1/3 completed" in html


def test_render_week_status_classes(seeded):
    _, conn = seeded
    html = render_week(week_id=WEEK_ID, plan_id=PLAN_ID, conn=conn)
    assert "class='completed'" in html or 'class="completed"' in html
    assert "class='skipped'" in html or 'class="skipped"' in html
    assert "class='pending'" in html or 'class="pending"' in html
    # Reason for skipped session surfaces.
    assert "tweaked calf" in html


def test_render_week_wellness_sparkline_has_polyline(seeded):
    _, conn = seeded
    html = render_week(week_id=WEEK_ID, plan_id=PLAN_ID, conn=conn)
    # Each of four metrics gets a sparkline.
    assert html.count("<polyline") >= 4
    assert "Sleep (h)" in html
    assert "HRV" in html
    assert "RHR" in html
    assert "Readiness" in html


def test_render_week_load_summary(seeded):
    _, conn = seeded
    html = render_week(week_id=WEEK_ID, plan_id=PLAN_ID, conn=conn)
    assert "Start CTL" in html
    assert "End CTL" in html
    assert "Peak ATL" in html
    assert "Low TSB" in html


def test_render_week_changelog_filters_to_window(seeded):
    _, conn = seeded
    html = render_week(week_id=WEEK_ID, plan_id=PLAN_ID, conn=conn)
    assert "Moved sp2 to Thursday" in html
    assert "Unrelated earlier entry" not in html


def test_render_week_no_data_renders_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("tempo.plans.repo_root", lambda: tmp_path)
    monkeypatch.setattr("tempo.dashboards.common.repo_root", lambda: tmp_path)
    conn = connect()
    init_schema(conn)
    try:
        html = render_week(week_id="2099-W01", plan_id=None, conn=conn)
    finally:
        conn.close()
    assert "No planned sessions" in html or "No data" in html.lower() or "no " in html.lower()
    assert "2099-W01" in html


def test_render_week_escapes_user_strings(seeded):
    _, conn = seeded
    conn.execute(
        "UPDATE adherence SET reason = ? WHERE planned_session_id = 'sp2'",
        ("skipped: <script>alert(1)</script>",),
    )
    html = render_week(week_id=WEEK_ID, plan_id=PLAN_ID, conn=conn)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_cli_dashboard_week_writes_file(seeded, tmp_path: Path):
    _, conn = seeded
    conn.close()  # CLI opens its own connection.
    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "week", WEEK_ID, "--plan-id", PLAN_ID])
    assert result.exit_code == 0, result.stdout
    out_dir = tmp_path / "dashboards"
    htmls = list(out_dir.glob("week-*.html"))
    assert len(htmls) == 1
    body = htmls[0].read_text(encoding="utf-8")
    assert WEEK_ID in body
    assert "endurance-2h" in body


def test_cli_dashboard_week_default_week(seeded, tmp_path: Path):
    """No week_id arg → defaults to today minus 7 days. Must not crash."""
    _, conn = seeded
    conn.close()
    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "week", "--plan-id", PLAN_ID])
    assert result.exit_code == 0, result.stdout
    htmls = list((tmp_path / "dashboards").glob("week-*.html"))
    assert len(htmls) == 1
    # Default week = today - 7d. Just verify the file's title block has *some* week id.
    body = htmls[0].read_text(encoding="utf-8")
    today = date.today()
    default_wid = plans.week_id_for(today - timedelta(days=7))
    assert default_wid in body
