"""Integration tests for tempo.briefs — the brief composers Phase 4 skills call."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempo import briefs


def _seed_repo(tmp_path: Path, *, race: bool = True) -> None:
    """Stand up a minimal athlete/ + knowledge/methodology/ tree."""
    (tmp_path / "athlete").mkdir()
    (tmp_path / "athlete" / "profile.yaml").write_text(
        "athlete:\n  name: Sean\n  weight_kg: 75\n"
        "thresholds:\n  ftp_w: 265\n  lthr_bpm: 168\n"
        "strengths: [aerobic]\nlimiters: [run durability]\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n_No active flags._\n\n## Resolved\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Hard constraints\n\n"
        "- Respect active injury flags.\n"
        "- Long-run progression max +10%/wk.\n",
        encoding="utf-8",
    )
    if race:
        (tmp_path / "athlete" / "race-calendar.yaml").write_text(
            "races:\n"
            "  - id: 2026-im-lake-placid\n"
            "    name: Ironman Lake Placid\n"
            "    date: 2099-12-01\n"  # far future so weeks_until stays positive regardless of today
            "    distance: ironman\n"
            "    priority: A\n"
            "    location: Lake Placid, NY\n",
            encoding="utf-8",
        )
        (tmp_path / "athlete" / "goals.yaml").write_text("goals: []\n", encoding="utf-8")
    else:
        (tmp_path / "athlete" / "race-calendar.yaml").write_text(
            "races: []\n", encoding="utf-8"
        )
        (tmp_path / "athlete" / "goals.yaml").write_text(
            "goals:\n"
            "  - id: 2099-open-base\n"
            "    title: Build year-round base\n",
            encoding="utf-8",
        )

    methodology = tmp_path / "knowledge" / "methodology"
    methodology.mkdir(parents=True)
    (methodology / "phases.yaml").write_text(
        "ironman_full_24wk:\n"
        "  total_weeks: 24\n"
        "  phases:\n"
        "    - id: base\n      weeks: 8\n"
        "rolling_base_block_12wk:\n"
        "  total_weeks: 12\n",
        encoding="utf-8",
    )


def _patch_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tempo.athlete.repo_root", lambda: tmp_path)
    monkeypatch.setattr("tempo.plans.repo_root", lambda: tmp_path)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))


def test_bootstrap_brief_race_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_repo(tmp_path, race=True)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2026-im-lake-placid")
    assert brief["goal"]["kind"] == "race"
    assert brief["goal"]["distance"] == "ironman"
    assert brief["goal"]["target_date"] == "2099-12-01"
    assert brief["applicable_phase_template"]["key"] == "ironman_full_24wk"
    assert brief["athlete_state"]["ftp_w"] == 265
    assert brief["athlete_state"]["limiters"] == ["run durability"]
    assert brief["active_injuries"] == []
    assert len(brief["hard_constraints"]) == 2
    assert brief["existing_plan"] is False
    # No load history seeded — recent_load should say so gracefully.
    assert brief["recent_load"]["samples_days"] == 0


def test_bootstrap_brief_non_race_rolling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=False)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2099-open-base")
    assert brief["goal"]["kind"] == "non_race"
    assert brief["goal"]["target_date"] is None
    assert brief["weeks_until_target"] is None
    # No target date → rolling template chosen.
    assert brief["applicable_phase_template"]["key"] == "rolling_base_block_12wk"
    # Untyped non-race goal defaults to maintenance.
    assert brief["goal"]["type"] == "maintenance"
    assert brief["goal"]["schema_error"] is None


def test_bootstrap_brief_performance_target_surfaces_metric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A goals.yaml entry with type=performance_target surfaces metric/current/target
    so the skill can route to compose_for_goal correctly."""
    (tmp_path / "athlete").mkdir()
    (tmp_path / "athlete" / "profile.yaml").write_text(
        "athlete:\n  name: Sean\nthresholds:\n  ftp_w: 248\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "race-calendar.yaml").write_text("races: []\n", encoding="utf-8")
    (tmp_path / "athlete" / "goals.yaml").write_text(
        "goals:\n"
        "  - id: 2026-ftp-280\n"
        "    type: performance_target\n"
        "    metric: ftp_w\n"
        "    current: 248\n"
        "    target: 280\n"
        "    by_date: 2099-12-01\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Hard constraints\n\n- Long-run progression max +10%/wk.\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n_No active flags._\n", encoding="utf-8"
    )
    (tmp_path / "knowledge" / "methodology").mkdir(parents=True)
    (tmp_path / "knowledge" / "methodology" / "phases.yaml").write_text(
        "ftp_target_16wk:\n  total_weeks: 16\n", encoding="utf-8"
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2026-ftp-280")
    assert brief["goal"]["type"] == "performance_target"
    assert brief["goal"]["metric"] == "ftp_w"
    assert brief["goal"]["current"] == 248
    assert brief["goal"]["target"] == 280
    assert brief["goal"]["target_date"] == "2099-12-01"
    assert brief["goal"]["schema_error"] is None


def test_bootstrap_brief_unknown_goal_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.UnknownGoalError) as excinfo:
        briefs.bootstrap_plan_brief("nonsense")
    assert "2026-im-lake-placid" in excinfo.value.known


def test_bootstrap_brief_active_injury_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)
    # Overwrite injury-log with an active flag.
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n"
        "### 2026-04-15 — calf strain — 3\n"
        "- Status: active\n- Constraints: no >Z3 run\n\n"
        "## Resolved\n",
        encoding="utf-8",
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2026-im-lake-placid")
    assert len(brief["active_injuries"]) == 1
    assert "calf strain" in brief["active_injuries"][0]


def test_bootstrap_brief_existing_plan_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)
    plan_dir = tmp_path / "plans" / "2026-im-lake-placid"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text("plan_id: 2026-im-lake-placid\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.bootstrap_plan_brief("2026-im-lake-placid")
    assert brief["existing_plan"] is True


# --- plan_week_brief --------------------------------------------------------

_PLAN_WITH_PHASES = """\
plan_id: 2026-im-lake-placid
goal_ref: 2026-im-lake-placid
template: ironman_full_24wk
start_date: 2026-03-02
target_date: 2026-07-26
total_weeks: 24
phases:
  - id: base
    start_week: 2026-W10
    weeks: 4
    weekly_tss_target: [350, 450]
    intensity_distribution: { z1_z2: 85, z3: 10, z4_plus: 5 }
    key_sessions: [long_ride_z2, long_run_z2]
  - id: build
    start_week: 2026-W14
    weeks: 6
    weekly_tss_target: [600, 750]
    intensity_distribution: { z1_z2: 75, z3: 15, z4_plus: 10 }
    key_sessions: [race_pace_bike, threshold_run]
"""


def _seed_week_plan(tmp_path: Path) -> None:
    """Seed athlete/ + a single plan with phases for plan_week_brief tests."""
    _seed_repo(tmp_path, race=True)
    plan_dir = tmp_path / "plans" / "2026-im-lake-placid"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text(_PLAN_WITH_PHASES, encoding="utf-8")


def test_plan_week_brief_resolves_single_plan_and_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W15")
    assert brief["week_id"] == "2026-W15"
    assert brief["week_start"] == "2026-04-06"
    assert brief["week_end"] == "2026-04-12"
    assert brief["plan"]["plan_id"] == "2026-im-lake-placid"
    assert brief["plan"]["phase"]["id"] == "build"
    assert brief["plan"]["week_of_phase"] == 2
    assert brief["plan"]["weeks_remaining_in_phase"] == 4
    assert brief["plan"]["weekly_tss_target_mid"] == 675


def test_plan_week_brief_default_week_is_one_week_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    from datetime import date, timedelta

    from tempo.plans import week_id_for

    expected = week_id_for(date.today() + timedelta(days=7))
    brief = briefs.plan_week_brief()
    assert brief["week_id"] == expected


def test_plan_week_brief_no_plan_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)  # no plans/ dir seeded
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.NoActivePlanError):
        briefs.plan_week_brief(week_id="2026-W15")


def test_plan_week_brief_multiple_plans_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    # Second plan under plans/
    other = tmp_path / "plans" / "2027-other"
    other.mkdir(parents=True)
    (other / "plan.yaml").write_text("plan_id: 2027-other\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    from tempo.plans import MultiplePlansError

    with pytest.raises(MultiplePlansError):
        briefs.plan_week_brief(week_id="2026-W15")


def test_plan_week_brief_explicit_plan_id_bypasses_autodetect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    # Ambiguous plans/ on purpose — explicit plan_id resolves it.
    other = tmp_path / "plans" / "2027-other"
    other.mkdir(parents=True)
    (other / "plan.yaml").write_text("plan_id: 2027-other\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(
        week_id="2026-W15", plan_id="2026-im-lake-placid"
    )
    assert brief["plan"]["plan_id"] == "2026-im-lake-placid"


def test_plan_week_brief_week_outside_any_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W05")  # before base starts
    assert brief["plan"]["phase"] is None
    assert brief["plan"]["week_of_phase"] is None
    assert brief["plan"]["weekly_tss_target_mid"] is None


def test_plan_week_brief_flags_existing_week_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    weeks = tmp_path / "plans" / "2026-im-lake-placid" / "weeks"
    weeks.mkdir(parents=True)
    (weeks / "2026-W15.md").write_text("# drafted\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W15")
    assert brief["week_already_drafted"] is True


def test_plan_week_brief_surfaces_active_injuries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n"
        "### 2026-04-15 — calf strain — 3\n"
        "- Status: active\n- Constraints: no >Z3 run\n\n"
        "## Resolved\n",
        encoding="utf-8",
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.plan_week_brief(week_id="2026-W15")
    assert len(brief["active_injuries"]) == 1
    assert "calf strain" in brief["active_injuries"][0]


# --- review_week_brief -----------------------------------------------------

def test_review_week_brief_resolves_phase_and_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.review_week_brief(week_id="2026-W15")
    assert brief["week_id"] == "2026-W15"
    assert brief["week_start"] == "2026-04-06"
    assert brief["week_end"] == "2026-04-12"
    assert brief["plan"]["plan_id"] == "2026-im-lake-placid"
    assert brief["plan"]["phase"]["id"] == "build"
    assert brief["plan"]["weekly_tss_target_mid"] == 675
    # DB empty but shape present.
    assert brief["adherence"]["planned_count"] == 0
    assert brief["deltas"] == []
    assert brief["per_sport_tss"] == {}
    assert brief["wellness_trend"] == []
    assert brief["load_trajectory"]["daily"] == []
    assert brief["load_trajectory"]["start_ctl"] is None
    assert brief["week_file_exists"] is False


def test_review_week_brief_defaults_to_last_completed_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    from datetime import date, timedelta

    from tempo.plans import week_id_for

    expected = week_id_for(date.today() - timedelta(days=7))
    brief = briefs.review_week_brief()
    assert brief["week_id"] == expected


def test_review_week_brief_no_plan_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_repo(tmp_path, race=True)
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.NoActivePlanError):
        briefs.review_week_brief(week_id="2026-W15")


def test_review_week_brief_flags_existing_week_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    weeks = tmp_path / "plans" / "2026-im-lake-placid" / "weeks"
    weeks.mkdir(parents=True)
    (weeks / "2026-W15.md").write_text("# planned\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.review_week_brief(week_id="2026-W15")
    assert brief["week_file_exists"] is True


def test_review_week_brief_populates_from_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_week_plan(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    # Seed DB with a minimal planned-vs-actual + wellness + load for W15.
    import sqlite3

    from tempo.db import connect, init_schema

    conn = connect()
    try:
        init_schema(conn)
        # planned session + adherence + activity
        conn.execute(
            "INSERT INTO sessions_planned "
            "(id, plan_id, week_id, date, sport, library_ref, target_tss, target_duration_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("sp1", "2026-im-lake-placid", "2026-W15", "2026-04-08", "ride",
             "long_ride_z2", 200.0, 10800),
        )
        conn.execute(
            "INSERT INTO activities (id, start_date, sport, duration_s, tss) "
            "VALUES (?, ?, ?, ?, ?)",
            ("act1", "2026-04-08T07:00:00", "ride", 10500, 185.0),
        )
        conn.execute(
            "INSERT INTO adherence (planned_session_id, activity_id, completed, "
            "tss_delta, duration_delta_s, reason) VALUES (?, ?, ?, ?, ?, ?)",
            ("sp1", "act1", 1, -15.0, -300, "completed"),
        )
        # wellness across the week
        conn.execute(
            "INSERT INTO wellness_daily (date, sleep_h, hrv, rhr, readiness) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-04-06", 7.5, 65.0, 50, 8),
        )
        conn.execute(
            "INSERT INTO wellness_daily (date, sleep_h, hrv, rhr, readiness) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-04-12", 6.8, 60.0, 53, 6),
        )
        # load trajectory
        conn.execute(
            "INSERT INTO load_daily (date, ctl, atl, tsb) VALUES (?, ?, ?, ?)",
            ("2026-04-06", 70.0, 60.0, 10.0),
        )
        conn.execute(
            "INSERT INTO load_daily (date, ctl, atl, tsb) VALUES (?, ?, ?, ?)",
            ("2026-04-12", 72.0, 85.0, -13.0),
        )
    finally:
        conn.close()
    assert isinstance(sqlite3.version, str)  # keep import used

    brief = briefs.review_week_brief(week_id="2026-W15")
    assert brief["adherence"]["planned_count"] == 1
    assert brief["adherence"]["completed_count"] == 1
    assert brief["per_sport_tss"] == {"ride": {"planned": 200.0, "actual": 185.0}}
    assert len(brief["deltas"]) == 1
    assert brief["deltas"][0]["tss_delta"] == pytest.approx(-15.0)
    assert len(brief["wellness_trend"]) == 2
    assert brief["load_trajectory"]["start_ctl"] == pytest.approx(70.0)
    assert brief["load_trajectory"]["end_ctl"] == pytest.approx(72.0)
    assert brief["load_trajectory"]["peak_atl"] == pytest.approx(85.0)
    assert brief["load_trajectory"]["low_tsb"] == pytest.approx(-13.0)


# --- ingest_research_brief --------------------------------------------------

_INGEST_HTML = """\
<!doctype html>
<html>
<head>
  <title>Asker Jeukendrup on multi-transportable carbohydrates</title>
  <meta name="author" content="Asker Jeukendrup">
  <meta property="article:published_time" content="2014-05-12">
</head>
<body>
  <article>
    <p>Combining glucose and fructose pushes carb oxidation above
    the single-transporter ceiling.</p>
  </article>
</body>
</html>
"""


def _seed_sources(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(exist_ok=True)
    (knowledge / "sources.yaml").write_text(
        "sources:\n"
        "  - id: jeukendrup-mysportscience\n"
        "    name: Asker Jeukendrup — mysportscience.com\n"
        "    type: expert_blog\n"
        "    credibility: peer_reviewed\n"
        "    topics: [nutrition, carb_loading, in_race_fueling]\n"
        "  - id: friel-blog\n"
        "    name: Joe Friel's Blog\n"
        "    credibility: expert_practitioner\n"
        "    topics: [periodization]\n",
        encoding="utf-8",
    )


def test_ingest_brief_local_pdf_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end on a real PDF — write one with pypdf, ingest it."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    _seed_sources(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    pdf_path = tmp_path / "friel-periodization.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": "Friel periodization primer"})
    with pdf_path.open("wb") as f:
        writer.write(f)

    from datetime import date
    brief = briefs.ingest_research_brief(str(pdf_path), today=date(2026, 4, 25))

    assert brief["source_kind"] == "pdf"
    assert brief["detected_title"] == "Friel periodization primer"
    assert brief["matched_source"]["id"] == "friel-blog"  # title token "friel"
    assert brief["suggested_slug"] == "friel-periodization-primer"
    assert brief["target_path"].endswith(".md")
    assert "knowledge/research/" in brief["target_path"]
    assert brief["duplicate_of"] is None
    assert len(brief["source_sha256"]) == 64
    assert brief["ingested"] == "2026-04-25"


def test_ingest_brief_url_uses_mocked_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL path — patch fetch_url to avoid live HTTP."""
    _seed_sources(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "tempo.research.fetch_url",
        lambda url, timeout=30.0: (_INGEST_HTML, "text/html"),
    )

    from datetime import date
    brief = briefs.ingest_research_brief(
        "https://www.mysportscience.com/post/multi-transportable-carbs",
        today=date(2026, 4, 25),
    )

    assert brief["source_kind"] == "url"
    assert brief["detected_title"] == "Asker Jeukendrup on multi-transportable carbohydrates"
    assert brief["matched_source"]["id"] == "jeukendrup-mysportscience"
    assert brief["matched_source"]["credibility"] == "peer_reviewed"
    # Detected article date drives the YYYY/MM target dir.
    assert "2014/05" in brief["target_path"]
    assert brief["duplicate_of"] is None


def test_ingest_brief_unvetted_when_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sources(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "tempo.research.fetch_url",
        lambda url, timeout=30.0: (
            "<html><head><title>Random Marketing</title></head>"
            "<body><p>Buy our shaker bottle.</p></body></html>",
            "text/html",
        ),
    )

    brief = briefs.ingest_research_brief("https://nope.example/x")
    assert brief["matched_source"] is None


def test_ingest_brief_detects_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sources(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "tempo.research.fetch_url",
        lambda url, timeout=30.0: (_INGEST_HTML, "text/html"),
    )

    from datetime import date
    first = briefs.ingest_research_brief(
        "https://www.mysportscience.com/post/x",
        today=date(2026, 4, 25),
    )
    # Plant a note containing that sha as if previously ingested.
    note_dir = tmp_path / "knowledge" / "research" / "2014" / "05"
    note_dir.mkdir(parents=True)
    note_path = note_dir / "earlier.md"
    note_path.write_text(
        f"---\nsource_sha256: {first['source_sha256']}\n---\n# Earlier\n",
        encoding="utf-8",
    )

    second = briefs.ingest_research_brief(
        "https://www.mysportscience.com/post/x",
        today=date(2026, 4, 25),
    )
    assert second["duplicate_of"] == "knowledge/research/2014/05/earlier.md"


def test_ingest_brief_rejects_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sources(tmp_path)
    _patch_roots(monkeypatch, tmp_path)
    with pytest.raises(briefs.IngestSourceError, match="file not found"):
        briefs.ingest_research_brief(str(tmp_path / "no-such.pdf"))


def test_ingest_brief_rejects_unsupported_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sources(tmp_path)
    _patch_roots(monkeypatch, tmp_path)
    junk = tmp_path / "notes.txt"
    junk.write_text("hi", encoding="utf-8")
    with pytest.raises(briefs.IngestSourceError, match="unsupported local source"):
        briefs.ingest_research_brief(str(junk))


def test_ingest_brief_rejects_remote_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sources(tmp_path)
    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "tempo.research.fetch_url",
        lambda url, timeout=30.0: ("%PDF-1.4 ...", "application/pdf"),
    )
    with pytest.raises(briefs.IngestSourceError, match="remote PDF"):
        briefs.ingest_research_brief("https://example.com/paper.pdf")


# --- race_plan_brief --------------------------------------------------------

_PLAN_WITH_PEAK_TAPER = """\
plan_id: 2026-im-lake-placid
template: ironman_full_24wk
start_date: 2026-03-02
target_date: 2026-07-26
total_weeks: 24
phases:
  - id: base
    start_week: 2026-W10
    weeks: 4
    weekly_tss_target: [350, 450]
  - id: build
    start_week: 2026-W14
    weeks: 6
    weekly_tss_target: [600, 750]
  - id: peak
    start_week: 2026-W20
    weeks: 3
    weekly_tss_target: [700, 800]
  - id: taper
    start_week: 2026-W23
    weeks: 3
    weekly_tss_target: [400, 500]
"""


def _seed_race_plan_repo(
    tmp_path: Path,
    *,
    race_date: str,
    priority: str = "A",
) -> None:
    """Seed athlete + plan files for race_plan_brief tests."""
    (tmp_path / "athlete").mkdir()
    (tmp_path / "athlete" / "profile.yaml").write_text(
        "athlete:\n  name: Sean\n  weight_kg: 75\n"
        "thresholds:\n  ftp_w: 265\n  lthr_bpm: 168\n"
        "  run_threshold_pace: '6:30'\n  swim_css_pace: '1:35'\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n_No active flags._\n", encoding="utf-8"
    )
    (tmp_path / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Hard constraints\n- Respect injury flags.\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "race-calendar.yaml").write_text(
        f"races:\n"
        f"  - id: 2026-im-lake-placid\n"
        f"    name: Ironman Lake Placid\n"
        f"    date: {race_date}\n"
        f"    distance: ironman\n"
        f"    priority: {priority}\n"
        f"    location: Lake Placid, NY\n"
        f"    expected_conditions: warm/humid\n",
        encoding="utf-8",
    )
    (tmp_path / "athlete" / "goals.yaml").write_text("goals: []\n", encoding="utf-8")
    plan_dir = tmp_path / "plans" / "2026-im-lake-placid"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text(_PLAN_WITH_PEAK_TAPER, encoding="utf-8")


def _seed_athlete_tested(tmp_path: Path) -> None:
    nutrition = tmp_path / "knowledge" / "nutrition"
    nutrition.mkdir(parents=True, exist_ok=True)
    (nutrition / "athlete-tested.yaml").write_text(
        "entries:\n"
        "  - date: 2026-03-15\n"
        "    session_type: race_sim_brick\n"
        "    duration_h: 4.5\n"
        "    products:\n"
        "      - name: Maurten Gel 160\n"
        "        qty: 6\n"
        "    totals:\n"
        "      carbs_g: 240\n"
        "      carbs_g_per_hr: 80\n"
        "      sodium_mg: 1000\n"
        "    gut_response: 5\n"
        "  - date: 2026-04-01\n"
        "    session_type: long_ride_z2\n"
        "    products:\n"
        "      - name: SiS Beta Fuel\n"
        "    totals:\n"
        "      carbs_g_per_hr: 100\n"
        "    gut_response: 2\n",
        encoding="utf-8",
    )


def test_race_plan_brief_explicit_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date
    _seed_race_plan_repo(tmp_path, race_date="2026-07-26")
    _seed_athlete_tested(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    today = date(2026, 6, 28)  # ~4 weeks out
    brief = briefs.race_plan_brief("2026-im-lake-placid", today=today)

    assert brief["race"]["id"] == "2026-im-lake-placid"
    assert brief["race"]["date"] == "2026-07-26"
    assert brief["weeks_until_race"] == 4
    assert brief["plan"]["plan_id"] == "2026-im-lake-placid"
    assert brief["plan"]["taper_phase"]["id"] == "taper"
    assert brief["plan"]["peak_phase"]["id"] == "peak"
    # athlete-tested summary feeds the binding constraint logic.
    assert brief["nutrition"]["entries_count"] == 2
    assert brief["nutrition"]["tolerated_max_carbs_g_per_hr"] == 80.0  # gut_response=5 entry
    assert "SiS Beta Fuel" in brief["nutrition"]["failed_products"]  # gut_response=2
    assert brief["race_plan_path"].endswith("race-day-plan.md")
    assert brief["race_plan_exists"] is False


def test_race_plan_brief_auto_picks_next_a_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date
    _seed_race_plan_repo(tmp_path, race_date="2026-07-26")
    _seed_athlete_tested(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    today = date(2026, 7, 5)  # 21 days out, within 28-day default
    brief = briefs.race_plan_brief(today=today)
    assert brief["race"]["id"] == "2026-im-lake-placid"
    assert brief["days_until_race"] == 21


def test_race_plan_brief_no_race_within_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date
    _seed_race_plan_repo(tmp_path, race_date="2099-01-01")  # too far out
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.NoUpcomingRaceError, match="no A-priority race"):
        briefs.race_plan_brief(today=date(2026, 4, 25))


def test_race_plan_brief_skips_non_a_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date
    _seed_race_plan_repo(tmp_path, race_date="2026-05-15", priority="B")
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.NoUpcomingRaceError):
        briefs.race_plan_brief(today=date(2026, 4, 25))


def test_race_plan_brief_unknown_race_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_race_plan_repo(tmp_path, race_date="2026-07-26")
    _patch_roots(monkeypatch, tmp_path)
    with pytest.raises(briefs.NoUpcomingRaceError, match="not found"):
        briefs.race_plan_brief("2026-bogus-race")


def test_race_plan_brief_existing_plan_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date
    _seed_race_plan_repo(tmp_path, race_date="2026-07-26")
    _seed_athlete_tested(tmp_path)
    plan_dir = tmp_path / "plans" / "2026-im-lake-placid"
    (plan_dir / "race-day-plan.md").write_text("# existing\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.race_plan_brief("2026-im-lake-placid", today=date(2026, 6, 28))
    assert brief["race_plan_exists"] is True


def test_race_plan_brief_handles_missing_athlete_tested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date
    _seed_race_plan_repo(tmp_path, race_date="2026-07-26")
    # No knowledge/nutrition/athlete-tested.yaml seeded.
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.race_plan_brief("2026-im-lake-placid", today=date(2026, 6, 28))
    assert brief["nutrition"]["exists"] is False
    assert brief["nutrition"]["entries"] == []


# --- midpoint_review_brief ---------------------------------------------------

_PLAN_FOR_MIDPOINT = """\
plan_id: 2026-mid-test
goal_ref: 2026-mid-test
template: ironman_full_24wk
start_date: 2026-03-30
target_date: 2026-12-01
total_weeks: 24
phases:
  - id: rehab
    start_week: 2026-W14
    weeks: 4
    weekly_tss_target: [200, 280]
    sport_focus: { bike: 0.85, swim: 0.0, run: 0.0, strength: 0.15 }
    intensity_distribution: { z1_z2: 90, z3: 8, z4_plus: 2 }
  - id: base
    start_week: 2026-W18
    weeks: 6
    weekly_tss_target: [600, 750]
    sport_focus: { bike: 0.5, swim: 0.2, run: 0.3 }
    intensity_distribution: { z1_z2: 80, z3: 15, z4_plus: 5 }
"""


def _seed_midpoint_repo(tmp_path: Path, *, profile: dict | None = None) -> None:
    """Seed athlete + plan tuned for midpoint-review tests."""
    _seed_repo(tmp_path, race=True)
    if profile is not None:
        import yaml as _y
        (tmp_path / "athlete" / "profile.yaml").write_text(
            _y.safe_dump(profile), encoding="utf-8"
        )
    plan_dir = tmp_path / "plans" / "2026-mid-test"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text(_PLAN_FOR_MIDPOINT, encoding="utf-8")
    # Drop the seeded race-plan plan so auto-detect picks ours.
    other = tmp_path / "plans" / "2026-im-lake-placid"
    if other.is_dir():
        import shutil
        shutil.rmtree(other)


def test_midpoint_review_brief_resolves_phase_and_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    _seed_midpoint_repo(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    # Week 3 of the 4-week rehab phase (2026-W14 + 2 weeks = 2026-W16).
    brief = briefs.midpoint_review_brief(
        week_id="2026-W16", today=date(2026, 4, 19)
    )
    assert brief["plan"]["plan_id"] == "2026-mid-test"
    assert brief["plan"]["phase_id"] == "rehab"
    assert brief["plan"]["week_in_phase"] == 3
    assert brief["plan"]["weeks_remaining_in_phase"] == 1
    assert brief["plan"]["total_phase_weeks"] == 4
    assert brief["plan"]["weekly_tss_target_mid"] == 240
    assert brief["plan"]["sport_focus"]["bike"] == 0.85
    # Phase window covers W14, W15, W16.
    assert brief["phase_window"]["start_week_id"] == "2026-W14"
    assert brief["phase_window"]["end_week_id"] == "2026-W16"
    assert brief["phase_window"]["weeks_elapsed"] == 3


def test_midpoint_review_brief_empty_db_safe_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    # Fresh struct thresholds so the empty DB signal list is genuinely empty
    # (not contaminated with default-profile stale flags).
    _seed_midpoint_repo(
        tmp_path,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {
                "ftp_w": {"value": 280, "set_at": "2026-04-15", "source": "field_test"},
                "lthr_bpm": {"value": 168, "set_at": "2026-04-01", "source": "field_test"},
                "run_threshold_pace": {
                    "value": "4:15/km", "set_at": "2026-04-01", "source": "race_result",
                },
                "swim_css_pace": {
                    "value": "1:35/100m", "set_at": "2026-04-01", "source": "field_test",
                },
                "max_hr": {"value": 188, "set_at": "2026-01-15", "source": "field_test"},
            },
        },
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.midpoint_review_brief(
        week_id="2026-W16", today=date(2026, 4, 19)
    )
    assert brief["adherence_phase"]["planned_count"] == 0
    assert brief["wellness_phase"]["samples_days"] == 0
    assert brief["target_vs_actual"]["ctl_overall"]["actual"] is None
    assert brief["signals"] == []
    assert brief["recent_decisions"] == []
    assert brief["review_already_exists"] is False
    assert brief["review_path"].endswith("plans/2026-mid-test/reviews/midpoint-2026-W16.md")


def test_midpoint_review_brief_aggregates_adherence_by_sport_and_weekday(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    _seed_midpoint_repo(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    from tempo.db import connect, init_schema

    conn = connect()
    try:
        init_schema(conn)
        # Two ride sessions in W14 (Mon planned+done, Wed planned+missed),
        # one swim in W15 (Tue planned+done).
        rows = [
            ("sp1", "2026-mid-test", "2026-W14", "2026-03-30", "ride", "long_ride", 100.0, 3600),
            ("sp2", "2026-mid-test", "2026-W14", "2026-04-01", "ride", "z2_ride", 60.0, 2700),
            ("sp3", "2026-mid-test", "2026-W15", "2026-04-07", "swim", "css_swim", 40.0, 2400),
        ]
        for r in rows:
            conn.execute(
                "INSERT INTO sessions_planned "
                "(id, plan_id, week_id, date, sport, library_ref, target_tss, target_duration_s) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                r,
            )
        conn.execute(
            "INSERT INTO activities (id, start_date, sport, duration_s, tss) VALUES (?, ?, ?, ?, ?)",
            ("a1", "2026-03-30T07:00:00", "ride", 3600, 95.0),
        )
        conn.execute(
            "INSERT INTO activities (id, start_date, sport, duration_s, tss) VALUES (?, ?, ?, ?, ?)",
            ("a3", "2026-04-07T06:30:00", "swim", 2300, 38.0),
        )
        conn.execute(
            "INSERT INTO adherence (planned_session_id, activity_id, completed, reason) VALUES (?, ?, ?, ?)",
            ("sp1", "a1", 1, "completed"),
        )
        conn.execute(
            "INSERT INTO adherence (planned_session_id, activity_id, completed, reason) VALUES (?, ?, ?, ?)",
            ("sp2", None, 0, "skipped: travel"),
        )
        conn.execute(
            "INSERT INTO adherence (planned_session_id, activity_id, completed, reason) VALUES (?, ?, ?, ?)",
            ("sp3", "a3", 1, "completed"),
        )
    finally:
        conn.close()

    brief = briefs.midpoint_review_brief(
        week_id="2026-W16", today=date(2026, 4, 19)
    )
    a = brief["adherence_phase"]
    assert a["planned_count"] == 3
    assert a["completed_count"] == 2
    assert a["skipped_count"] == 1
    assert a["completion_pct"] == round(100.0 * 2 / 3, 1)
    assert a["by_sport"]["ride"]["planned"] == 2
    assert a["by_sport"]["ride"]["completed"] == 1
    assert a["by_sport"]["ride"]["actual_tss"] == 95.0
    assert a["by_sport"]["swim"]["completed"] == 1
    # 2026-03-30 is Monday, 2026-04-01 Wed, 2026-04-07 Tue.
    assert a["by_weekday"]["Mon"]["planned"] == 1
    assert a["by_weekday"]["Mon"]["completed"] == 1
    assert a["by_weekday"]["Wed"]["planned"] == 1
    assert a["by_weekday"]["Wed"]["completed"] == 0
    assert a["by_weekday"]["Tue"]["planned"] == 1


def test_midpoint_review_brief_threshold_provenance_marks_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    _seed_midpoint_repo(
        tmp_path,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {
                "ftp_w": {"value": 280, "set_at": "2026-04-15", "source": "field_test"},
                "lthr_bpm": {"value": 168, "set_at": "2026-04-01", "source": "race_result"},
                "run_threshold_pace": {
                    "value": "4:42/km",
                    "set_at": "2025-12-01",
                    "source": "race_result",
                },
            },
        },
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.midpoint_review_brief(
        week_id="2026-W16", today=date(2026, 4, 19)
    )
    th = brief["thresholds"]
    assert th["ftp_w"]["is_stale"] is False
    assert th["ftp_w"]["age_days"] == 4
    assert th["run_threshold_pace"]["is_stale"] is True
    assert "stale_threshold:run_threshold_pace" in brief["signals"]


def test_midpoint_review_brief_signals_compute_deterministic_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    _seed_midpoint_repo(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    from tempo.db import connect, init_schema

    conn = connect()
    try:
        init_schema(conn)
        # Two consecutive weeks with low completion (50% each) — triggers
        # adherence_below_70_consec_2 and tss_under_target.
        for wid, when in [("2026-W14", "2026-03-30"), ("2026-W15", "2026-04-06")]:
            for i in range(4):
                sid = f"{wid}-s{i}"
                conn.execute(
                    "INSERT INTO sessions_planned (id, plan_id, week_id, date, sport, target_tss) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sid, "2026-mid-test", wid, when, "ride", 100.0),
                )
                completed = 1 if i < 2 else 0
                conn.execute(
                    "INSERT INTO adherence (planned_session_id, completed, reason) VALUES (?, ?, ?)",
                    (sid, completed, "completed" if completed else "skipped"),
                )
        # Latest CTL well below target.
        conn.execute(
            "INSERT INTO load_daily (date, ctl, atl, tsb) VALUES (?, ?, ?, ?)",
            ("2026-04-19", 20.0, 25.0, -5.0),
        )
    finally:
        conn.close()

    brief = briefs.midpoint_review_brief(
        week_id="2026-W16", today=date(2026, 4, 19)
    )
    sigs = brief["signals"]
    # Phase target_mid 240 → target_ss_ctl ≈ 34.3. Actual CTL 20 → -42% delta.
    assert "ctl_below_target_pct_15" in sigs
    assert "phase_completion_below_70" in sigs
    assert "adherence_below_70_consec_2" in sigs
    assert "tss_under_target" in sigs


def test_midpoint_review_brief_default_week_is_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    _seed_midpoint_repo(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    today = date(2026, 4, 19)  # 2026-W16
    brief = briefs.midpoint_review_brief(today=today)
    assert brief["week_id"] == "2026-W16"


def test_midpoint_review_brief_week_outside_phase_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    _seed_midpoint_repo(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    with pytest.raises(briefs.NoActivePlanError, match="not contained in any phase"):
        briefs.midpoint_review_brief(week_id="2026-W05", today=date(2026, 2, 1))


def test_midpoint_review_brief_idempotency_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same week always resolves to the same review_path; existing file flagged."""
    from datetime import date

    _seed_midpoint_repo(tmp_path)
    _patch_roots(monkeypatch, tmp_path)

    review_dir = tmp_path / "plans" / "2026-mid-test" / "reviews"
    review_dir.mkdir(parents=True)
    (review_dir / "midpoint-2026-W16.md").write_text("# prior run\n", encoding="utf-8")

    brief = briefs.midpoint_review_brief(
        week_id="2026-W16", today=date(2026, 4, 19)
    )
    assert brief["review_already_exists"] is True
    assert brief["review_path"] == "plans/2026-mid-test/reviews/midpoint-2026-W16.md"


def test_midpoint_review_brief_surfaces_calibration_debt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    _seed_midpoint_repo(
        tmp_path,
        profile={
            "athlete": {"weight_kg": 75},
            "thresholds": {
                # Blank FTP → "not set" debt; stale run pace → freshness debt.
                "ftp_w": None,
                "lthr_bpm": {"value": 168, "set_at": "2026-04-01", "source": "field_test"},
                "run_threshold_pace": {
                    "value": "4:42/km",
                    "set_at": "2025-08-01",
                    "source": "race_result",
                },
            },
        },
    )
    _patch_roots(monkeypatch, tmp_path)

    brief = briefs.midpoint_review_brief(
        week_id="2026-W16", today=date(2026, 4, 19)
    )
    fields = {d["field"] for d in brief["calibration_debt"]}
    assert "athlete.profile.thresholds.ftp_w" in fields
    assert "athlete.profile.thresholds.run_threshold_pace.set_at" in fields
