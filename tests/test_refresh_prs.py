"""Tests for ``tempo.refresh_prs`` — coach.db activities → athlete/profile.yaml PRs.

The DB is a real on-disk sqlite (the ``tmp_data_dir`` fixture redirects
``TEMPO_DATA_DIR``); we hand-insert synthetic activities to exercise the
matching logic. No network, no streams.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tempo.db import connect, init_schema
from tempo.refresh_prs import (
    _format_duration,
    _parse_duration,
    refresh_prs,
    render_summary_rows,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BASE_PROFILE = """\
athlete:
  name: Sean Koval
thresholds:
  ftp_w:
    value: 250
    set_at: '2026-05-01'
    source: intervals_import
prs:
  5k_run: '24:24'
  10k_run: '49:49'
  half_marathon: null
  marathon: null
  20min_power: null
  ftp_bike: null
  half_ironman: null
  full_ironman: null
  other_prs:
    400m_run: '1:13'
    800m_run: '3:25'
    1k_run: '4:17'
    1mi_run: '7:04'
    2mi_run: '15:32'
    15k_run: '1:15:23'
    10mi_run: '1:22:07'
"""


def _seed_profile(tmp_path: Path, body: str = _BASE_PROFILE) -> Path:
    (tmp_path / "athlete").mkdir(parents=True, exist_ok=True)
    p = tmp_path / "athlete" / "profile.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _insert_activity(
    db_path: Path,
    *,
    id_: str,
    sport: str,
    distance_m: float,
    duration_s: int,
    start_date: str = "2026-04-15T07:30:00",
    np: float | None = None,
    intensity_factor: float | None = None,
) -> None:
    conn = connect(db_path)
    try:
        init_schema(conn)
        conn.execute(
            """
            INSERT INTO activities
                (id, start_date, sport, duration_s, distance_m, np, intensity_factor)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (id_, start_date, sport, duration_s, distance_m, np, intensity_factor),
        )
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data" / "coach.db"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def test_format_duration_short():
    assert _format_duration(87) == "1:27"


def test_format_duration_long():
    assert _format_duration(3725) == "1:02:05"


def test_parse_duration_round_trip():
    assert _parse_duration("24:24") == 24 * 60 + 24
    assert _parse_duration("1:22:07") == 1 * 3600 + 22 * 60 + 7
    assert _parse_duration(None) is None
    assert _parse_duration("garbage") is None


# ---------------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------------


def test_no_activities_short_circuits(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    # Initialize empty schema so the "no activities" path is what we hit.
    conn = connect(db_path)
    try:
        init_schema(conn)
    finally:
        conn.close()

    result = refresh_prs(root=tmp_path, db_path=db_path)

    assert result.no_activities is True
    assert result.activities_considered == 0
    assert result.changed is False
    assert (tmp_path / "athlete" / "profile.yaml").read_text() == _BASE_PROFILE


# ---------------------------------------------------------------------------
# Distance matching → PR derivation
# ---------------------------------------------------------------------------


def test_5k_inside_tolerance_is_picked(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    # 4.95 km in 23:30 — inside ±5%, faster than the manual 24:24.
    _insert_activity(
        db_path,
        id_="i12345",
        sport="run",
        distance_m=4950.0,
        duration_s=23 * 60 + 30,
        start_date="2026-04-15T07:30:00",
    )
    result = refresh_prs(root=tmp_path, db_path=db_path)

    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["5k_run"] == "23:30"
    assert doc["prs_meta"]["5k_run"]["source"] == "intervals_activity:i12345"
    assert doc["prs_meta"]["5k_run"]["set_at"] == "2026-04-15"
    assert result.changed is True
    assert any(u.action == "improved" and u.key == "5k_run" for u in result.updates)


def test_distance_outside_tolerance_is_rejected(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    # 5.30 km — outside +5% upper bound (5.25 km). Should NOT match 5k.
    _insert_activity(
        db_path,
        id_="i999",
        sport="run",
        distance_m=5300.0,
        duration_s=20 * 60,
    )
    result = refresh_prs(root=tmp_path, db_path=db_path)

    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    # Manual 24:24 survives.
    assert doc["prs"]["5k_run"] == "24:24"
    five_k = next(u for u in result.updates if u.key == "5k_run")
    assert five_k.action == "no_data"


def test_lower_tolerance_boundary_4750m_matches_5k(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path,
        id_="iLow",
        sport="run",
        distance_m=4750.0,  # exactly -5%
        duration_s=22 * 60,
    )
    result = refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["5k_run"] == "22:00"
    assert result.changed is True


def test_other_pr_slot_is_written_under_other_prs(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    # Sub-1:13 400m — beats Sean's manual 1:13.
    _insert_activity(
        db_path,
        id_="i400",
        sport="run",
        distance_m=400.0,
        duration_s=70,
    )
    refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["other_prs"]["400m_run"] == "1:10"
    assert doc["prs_meta"]["400m_run"]["source"] == "intervals_activity:i400"


# ---------------------------------------------------------------------------
# Conflict resolution: manual faster than activities
# ---------------------------------------------------------------------------


def test_manual_pr_faster_is_preserved(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    # Activity is 25:00 — slower than manual 24:24.
    _insert_activity(
        db_path,
        id_="iSlow",
        sport="run",
        distance_m=5000.0,
        duration_s=25 * 60,
    )
    result = refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["5k_run"] == "24:24"
    five_k = next(u for u in result.updates if u.key == "5k_run")
    assert five_k.action == "kept_manual_faster"
    assert "manual 24:24 stands" in five_k.notes


def test_force_overwrites_manual_pr(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path,
        id_="iSlow",
        sport="run",
        distance_m=5000.0,
        duration_s=25 * 60,
    )
    refresh_prs(root=tmp_path, db_path=db_path, force=True)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    # With --force-from-activities, the slower activity wins.
    assert doc["prs"]["5k_run"] == "25:00"
    assert doc["prs_meta"]["5k_run"]["source"] == "intervals_activity:iSlow"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_rerun_is_byte_identical(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path,
        id_="i12345",
        sport="run",
        distance_m=5000.0,
        duration_s=23 * 60 + 30,
    )
    p = tmp_path / "athlete" / "profile.yaml"
    refresh_prs(root=tmp_path, db_path=db_path)
    text_after_first = p.read_text(encoding="utf-8")

    result2 = refresh_prs(root=tmp_path, db_path=db_path)
    text_after_second = p.read_text(encoding="utf-8")

    assert text_after_first == text_after_second
    assert result2.changed is False


# ---------------------------------------------------------------------------
# Bike heuristic
# ---------------------------------------------------------------------------


def test_bike_ftp_derived_from_60min_high_if_ride(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path,
        id_="iFTP",
        sport="bike",
        distance_m=30000.0,
        duration_s=60 * 60,
        np=265.0,
        intensity_factor=0.96,
    )
    refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["ftp_bike"] == 265
    assert doc["prs_meta"]["ftp_bike"]["source"] == "intervals_activity:iFTP"


def test_bike_low_intensity_ride_does_not_set_ftp(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path,
        id_="iZ2",
        sport="bike",
        distance_m=30000.0,
        duration_s=60 * 60,
        np=180.0,
        intensity_factor=0.72,  # endurance ride — well below FTP-test territory
    )
    refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["ftp_bike"] is None


def test_bike_20min_power_picks_short_hard_effort(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path,
        id_="i20",
        sport="bike",
        distance_m=10000.0,
        duration_s=20 * 60,
        np=290.0,
        intensity_factor=1.05,
    )
    refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["20min_power"] == 290


# ---------------------------------------------------------------------------
# Multiple candidates → fastest wins
# ---------------------------------------------------------------------------


def test_fastest_5k_among_many_wins(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(db_path, id_="iA", sport="run", distance_m=5000.0, duration_s=24 * 60)
    _insert_activity(db_path, id_="iB", sport="run", distance_m=5000.0, duration_s=22 * 60)
    _insert_activity(db_path, id_="iC", sport="run", distance_m=5000.0, duration_s=23 * 60)
    refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["5k_run"] == "22:00"
    assert doc["prs_meta"]["5k_run"]["source"] == "intervals_activity:iB"


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write_file(tmp_path: Path, db_path: Path):
    p = _seed_profile(tmp_path)
    original = p.read_text()
    _insert_activity(
        db_path, id_="iDR", sport="run", distance_m=5000.0, duration_s=22 * 60
    )
    result = refresh_prs(root=tmp_path, db_path=db_path, dry_run=True)
    assert p.read_text() == original
    assert result.changed is True  # would-have-changed signal
    assert any(u.action == "improved" and u.key == "5k_run" for u in result.updates)


# ---------------------------------------------------------------------------
# Sport filtering — bike activity at 5k distance should NOT set 5k_run.
# ---------------------------------------------------------------------------


def test_bike_activity_does_not_match_run_distance(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path,
        id_="iBike5k",
        sport="bike",
        distance_m=5000.0,
        duration_s=8 * 60,  # would crush any 5k run PR
    )
    result = refresh_prs(root=tmp_path, db_path=db_path)
    doc = yaml.safe_load((tmp_path / "athlete" / "profile.yaml").read_text())
    assert doc["prs"]["5k_run"] == "24:24"
    five_k = next(u for u in result.updates if u.key == "5k_run")
    assert five_k.action == "no_data"
    # No improvement expected — the bike activity is sport-mismatched.
    assert not any(u.key == "5k_run" and u.action in {"set", "improved"} for u in result.updates)


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def test_summary_rows_include_status_and_value(tmp_path: Path, db_path: Path):
    _seed_profile(tmp_path)
    _insert_activity(
        db_path, id_="iSum", sport="run", distance_m=5000.0, duration_s=22 * 60
    )
    result = refresh_prs(root=tmp_path, db_path=db_path)
    rows = render_summary_rows(result)
    five_k_row = next(r for r in rows if r[1] == "5k_run")
    assert five_k_row[0] == "improved"
    assert five_k_row[2] == "22:00"
    assert "intervals_activity:iSum" in five_k_row[3]
