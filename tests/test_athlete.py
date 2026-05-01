"""Tests for tempo.athlete read helpers."""

from __future__ import annotations

from pathlib import Path

from tempo import athlete


def _seed(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / "athlete").mkdir(exist_ok=True)
    (tmp_path / "athlete" / name).write_text(content, encoding="utf-8")


def test_load_profile_returns_empty_when_missing(tmp_path: Path) -> None:
    assert athlete.load_profile(root=tmp_path) == {}


def test_load_profile_parses(tmp_path: Path) -> None:
    _seed(tmp_path, "profile.yaml", "athlete:\n  name: Sean\nthresholds:\n  ftp_w: 260\n")
    got = athlete.load_profile(root=tmp_path)
    assert got["athlete"]["name"] == "Sean"
    assert got["thresholds"]["ftp_w"] == 260


def test_load_goals_and_races_empty_on_stub(tmp_path: Path) -> None:
    _seed(tmp_path, "goals.yaml", "goals: []\n")
    _seed(tmp_path, "race-calendar.yaml", "races: []\n")
    assert athlete.load_goals(root=tmp_path) == []
    assert athlete.load_races(root=tmp_path) == []


def test_find_goal_matches_race_first(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "race-calendar.yaml",
        "races:\n"
        "  - id: 2026-im-lake-placid\n"
        "    name: Ironman Lake Placid\n"
        "    date: 2026-07-26\n"
        "    distance: ironman\n"
        "    priority: A\n",
    )
    _seed(tmp_path, "goals.yaml", "goals: []\n")
    match = athlete.find_goal("2026-im-lake-placid", root=tmp_path)
    assert match is not None
    assert match.kind == "race"
    assert match.data["distance"] == "ironman"


def test_find_goal_falls_through_to_non_race(tmp_path: Path) -> None:
    _seed(tmp_path, "race-calendar.yaml", "races: []\n")
    _seed(
        tmp_path,
        "goals.yaml",
        "goals:\n"
        "  - id: 2026-ftp-300w\n"
        "    title: Raise FTP to 300W\n"
        "    target_date: 2026-09-01\n",
    )
    match = athlete.find_goal("2026-ftp-300w", root=tmp_path)
    assert match is not None
    assert match.kind == "non_race"


def test_find_goal_unknown_returns_none(tmp_path: Path) -> None:
    _seed(tmp_path, "goals.yaml", "goals: []\n")
    _seed(tmp_path, "race-calendar.yaml", "races: []\n")
    assert athlete.find_goal("nope", root=tmp_path) is None


def test_all_goal_ids_unions_sources(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "race-calendar.yaml",
        "races:\n  - id: r1\n    date: 2026-05-01\n",
    )
    _seed(
        tmp_path,
        "goals.yaml",
        "goals:\n  - id: g1\n    title: t\n",
    )
    ids = athlete.all_goal_ids(root=tmp_path)
    assert set(ids) == {"r1", "g1"}


def test_active_injury_flags_empty_on_placeholder(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "injury-log.md",
        "# Log\n\n## Active\n\n_No active flags._\n\n## Resolved\n",
    )
    assert athlete.active_injury_flags(root=tmp_path) == []


def test_active_injury_flags_picks_up_entries(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "injury-log.md",
        "# Log\n\n## Active\n\n"
        "### 2026-04-10 — calf strain — 3\n"
        "- Status: active\n"
        "- Constraints: no >Z3 run\n\n"
        "### 2026-04-15 — mild knee — 2\n"
        "- Status: active\n\n"
        "## Resolved\n",
    )
    flags = athlete.active_injury_flags(root=tmp_path)
    assert len(flags) == 2
    assert flags[0].startswith("2026-04-10")
    assert "calf strain" in flags[0]


def test_active_injury_flags_missing_file(tmp_path: Path) -> None:
    assert athlete.active_injury_flags(root=tmp_path) == []


def test_hard_constraints_parses_bullets(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "preferences.md",
        "# Prefs\n\n## Hard constraints\n\n"
        "- Respect injury-log active flags without exception.\n"
        "- HRV down 3+ days and TSB < -20 → cut next hard session.\n\n"
        "## Soft preferences\n\n"
        "- Outdoor long rides on weekends.\n",
    )
    got = athlete.hard_constraints(root=tmp_path)
    assert len(got) == 2
    assert got[0].startswith("Respect injury-log")
    assert "HRV" in got[1]


def test_hard_constraints_missing_section(tmp_path: Path) -> None:
    _seed(tmp_path, "preferences.md", "# Prefs\n\n## Soft preferences\n\n- Foo\n")
    assert athlete.hard_constraints(root=tmp_path) == []


# --- Race calendar schema (tempo-wk7) -------------------------------------


def test_load_races_defaults_status_to_confirmed(tmp_path: Path) -> None:
    """Existing entries without an explicit status are read as confirmed."""
    _seed(
        tmp_path,
        "race-calendar.yaml",
        "races:\n"
        "  - id: legacy-race\n"
        "    date: 2026-09-01\n"
        "    distance: marathon\n"
        "    priority: A\n",
    )
    races = athlete.load_races(root=tmp_path)
    assert len(races) == 1
    assert races[0]["status"] == "confirmed"


def test_load_races_validates_status_value(tmp_path: Path) -> None:
    import pytest

    _seed(
        tmp_path,
        "race-calendar.yaml",
        "races:\n"
        "  - id: r1\n"
        "    date: 2026-09-01\n"
        "    status: maybe\n",
    )
    with pytest.raises(athlete.RaceCalendarError, match="status="):
        athlete.load_races(root=tmp_path)


def test_load_races_requires_cancelled_reason(tmp_path: Path) -> None:
    import pytest

    _seed(
        tmp_path,
        "race-calendar.yaml",
        "races:\n"
        "  - id: r1\n"
        "    date: 2026-09-01\n"
        "    status: cancelled\n",
    )
    with pytest.raises(athlete.RaceCalendarError, match="cancelled_reason"):
        athlete.load_races(root=tmp_path)


def test_load_races_validates_priority_value(tmp_path: Path) -> None:
    import pytest

    _seed(
        tmp_path,
        "race-calendar.yaml",
        "races:\n"
        "  - id: r1\n"
        "    date: 2026-09-01\n"
        "    priority: D\n",
    )
    with pytest.raises(athlete.RaceCalendarError, match="priority="):
        athlete.load_races(root=tmp_path)


def test_selectable_races_filters_cancelled(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "race-calendar.yaml",
        "races:\n"
        "  - id: live-race\n"
        "    date: 2026-09-01\n"
        "    priority: A\n"
        "  - id: dead-race\n"
        "    date: 2026-10-01\n"
        "    priority: A\n"
        "    status: cancelled\n"
        "    cancelled_reason: travel conflict\n"
        "  - id: maybe-race\n"
        "    date: 2026-11-01\n"
        "    priority: B\n"
        "    status: tentative\n",
    )
    selectable = athlete.selectable_races(root=tmp_path)
    ids = {r["id"] for r in selectable}
    assert ids == {"live-race", "maybe-race"}
