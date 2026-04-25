"""Render tests for ``coach dashboard decisions`` (tempo-mvh.3)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempo import plans
from tempo.cli import app
from tempo.dashboards import render_decisions
from tempo.db import connect, init_schema

WEEK_ID = "2026-W17"


def _seed_decisions(conn: sqlite3.Connection, today: date) -> None:
    week_scope = f"week:{WEEK_ID}"
    rows = [
        (
            (today - timedelta(days=2)).isoformat() + "T08:30:00",
            week_scope,
            "adjust",
            "Cut Tuesday tempo run — calf flare-up.",
            json.dumps([f"plans/2026-im-test/weeks/{WEEK_ID}.md"]),
        ),
        (
            (today - timedelta(days=5)).isoformat() + "T20:00:00",
            "plan:2026-im-test",
            "review",
            "Bumped target weekly TSS by 5% — CTL drift -8.",
            json.dumps(["plans/2026-im-test/plan.yaml", "plans/2026-im-test/changelog.md"]),
        ),
        (
            (today - timedelta(days=40)).isoformat() + "T07:00:00",
            week_scope,
            "observation",
            "OLD entry that should be filtered by --since.",
            None,
        ),
    ]
    for ts, scope, kind, rationale, files in rows:
        conn.execute(
            "INSERT INTO decisions(timestamp, scope, kind, rationale, changed_files) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, scope, kind, rationale, files),
        )


def _seed_supporting_data(conn: sqlite3.Connection, today: date) -> None:
    """A wellness row + a planned session so the evidence panel can populate."""
    conn.execute(
        "INSERT INTO wellness_daily(date, sleep_h, hrv, rhr, readiness) "
        "VALUES (?, ?, ?, ?, ?)",
        ((today - timedelta(days=2)).isoformat(), 7.4, 62.0, 49, 75),
    )
    monday = plans.week_start(WEEK_ID)
    conn.execute(
        "INSERT INTO sessions_planned(id, plan_id, week_id, date, sport, library_ref, "
        "target_tss, target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp-d1", "2026-im-test", WEEK_ID, monday.isoformat(),
         "bike", "endurance-2h", 95.0, 7200, "aerobic_base"),
    )
    conn.execute(
        "INSERT INTO adherence(planned_session_id, completed) VALUES (?, ?)",
        ("sp-d1", 1),
    )


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("tempo.plans.repo_root", lambda: tmp_path)
    monkeypatch.setattr("tempo.dashboards.common.repo_root", lambda: tmp_path)
    today = date(2026, 4, 24)  # fixed so timestamp filtering is stable
    conn = connect()
    init_schema(conn)
    _seed_decisions(conn, today)
    _seed_supporting_data(conn, today)
    yield tmp_path, conn, today
    conn.close()


def test_render_decisions_default_window_excludes_old(seeded):
    _, conn, today = seeded
    html = render_decisions(conn=conn, today=today)
    assert "Cut Tuesday tempo run" in html
    assert "Bumped target weekly TSS" in html
    assert "OLD entry" not in html


def test_render_decisions_scope_filter(seeded):
    _, conn, today = seeded
    html = render_decisions(scope=f"week:{WEEK_ID}", conn=conn, today=today)
    assert "Cut Tuesday tempo run" in html
    assert "Bumped target weekly TSS" not in html


def test_render_decisions_evidence_panel(seeded):
    _, conn, today = seeded
    html = render_decisions(conn=conn, today=today)
    # Wellness latest values
    assert "sleep 7.4h" in html
    assert "HRV 62" in html
    # Adherence summary for the week-scoped decision
    assert "1/1 completed" in html
    # Files listed
    assert f"plans/2026-im-test/weeks/{WEEK_ID}.md" in html
    assert "plans/2026-im-test/plan.yaml" in html


def test_render_decisions_escapes_rationale(seeded):
    _, conn, today = seeded
    conn.execute(
        "INSERT INTO decisions(timestamp, scope, kind, rationale, changed_files) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            (today - timedelta(days=1)).isoformat() + "T09:00:00",
            "session:abc",
            "adjust",
            "<script>alert(1)</script>",
            None,
        ),
    )
    html = render_decisions(conn=conn, today=today)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_decisions_empty_window(seeded):
    _, conn, today = seeded
    html = render_decisions(scope="week:1999-W01", conn=conn, today=today)
    assert "No decisions" in html


def test_render_decisions_since_override(seeded):
    _, conn, today = seeded
    # Reach back far enough to include the old entry.
    far_back = (today - timedelta(days=90)).isoformat()
    html = render_decisions(since=far_back, conn=conn, today=today)
    assert "OLD entry" in html


def test_render_decisions_handles_malformed_changed_files(seeded):
    _, conn, today = seeded
    conn.execute(
        "INSERT INTO decisions(timestamp, scope, kind, rationale, changed_files) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            (today - timedelta(days=1)).isoformat() + "T11:00:00",
            "plan:2026-im-test",
            "adjust",
            "Tested malformed JSON",
            "not-json-at-all",
        ),
    )
    html = render_decisions(conn=conn, today=today)
    # Falls back to rendering the raw string rather than crashing.
    assert "not-json-at-all" in html


def test_cli_dashboard_decisions_writes_file(seeded, tmp_path: Path):
    _, conn, _ = seeded
    conn.close()
    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "decisions"])
    assert result.exit_code == 0, result.stdout
    htmls = list((tmp_path / "dashboards").glob("decisions-*.html"))
    assert len(htmls) == 1
    body = htmls[0].read_text(encoding="utf-8")
    assert "Decisions" in body
