"""Tests for tempo.decoupling — pure compute + the backfill loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from httpx import Response
from intervals_icu_mcp.auth import ICUConfig

from tempo.db import connect, init_schema
from tempo.decoupling import (
    MIN_MOVING_SECONDS,
    backfill,
    compute_decoupling,
)
from tempo.paths import coach_db_path

_BASE_URL = "https://intervals.icu/api/v1"


# ---- Pure-function tests ----------------------------------------------------


def test_skips_swim() -> None:
    r = compute_decoupling(
        sport="swim",
        duration_s=4000,
        watts=[100, 100],
        heartrate=[150, 150],
    )
    assert r.pct is None
    assert r.method == "skipped"
    assert r.reason and "sport" in r.reason


def test_skips_short_activity() -> None:
    r = compute_decoupling(
        sport="bike",
        duration_s=600,
        watts=[200] * 600,
        heartrate=[150] * 600,
    )
    assert r.pct is None
    assert r.method == "skipped"


def test_skips_when_no_hr() -> None:
    r = compute_decoupling(
        sport="bike",
        duration_s=MIN_MOVING_SECONDS + 60,
        watts=[200] * 100,
        heartrate=None,
    )
    assert r.pct is None
    assert r.reason == "no_hr_stream"


def test_pwhr_steady_returns_zero() -> None:
    """Constant power and HR through the activity => 0% drift."""
    n = 4000
    r = compute_decoupling(
        sport="bike",
        duration_s=n,
        watts=[200] * n,
        heartrate=[150] * n,
    )
    assert r.method == "pw_hr"
    assert r.pct is not None
    assert abs(r.pct) < 1e-6


def test_pwhr_positive_drift_when_hr_climbs() -> None:
    """Same power, HR climbs in second half => positive decoupling."""
    half = 2000
    watts = [200] * (half * 2)
    heartrate = [140] * half + [160] * half
    r = compute_decoupling(
        sport="bike",
        duration_s=half * 2,
        watts=watts,
        heartrate=heartrate,
    )
    # first ratio: 200/140 = 1.4286; second: 200/160 = 1.25
    # drift = (1.4286 - 1.25) / 1.4286 * 100 = 12.5%
    assert r.method == "pw_hr"
    assert r.pct is not None
    assert 12.0 < r.pct < 13.0


def test_pwhr_negative_when_power_climbs() -> None:
    """Power climbs faster than HR => negative drift (the opposite of decoupling)."""
    half = 2000
    watts = [180] * half + [220] * half
    heartrate = [150] * (half * 2)
    r = compute_decoupling(
        sport="bike",
        duration_s=half * 2,
        watts=watts,
        heartrate=heartrate,
    )
    assert r.pct is not None
    assert r.pct < 0


def test_run_uses_pace_when_no_watts() -> None:
    half = 2000
    velocity = [3.0] * (half * 2)
    heartrate = [150] * half + [165] * half
    r = compute_decoupling(
        sport="run",
        duration_s=half * 2,
        watts=None,
        velocity_smooth=velocity,
        heartrate=heartrate,
    )
    assert r.method == "pa_hr"
    assert r.pct is not None
    assert r.pct > 0


def test_bike_falls_back_to_pace_without_watts() -> None:
    half = 2000
    r = compute_decoupling(
        sport="bike",
        duration_s=half * 2,
        watts=None,
        velocity_smooth=[8.0] * (half * 2),
        heartrate=[150] * half + [160] * half,
    )
    assert r.method == "pa_hr"
    assert r.pct is not None


def test_filters_non_moving_samples() -> None:
    """When the moving stream marks samples False, they are dropped."""
    # Samples 0..199: stopped (would skew first half down)
    # Samples 200..3999: real ride at 200W / 150bpm steady
    n = 4000
    watts = [0] * 200 + [200] * (n - 200)
    heartrate = [80] * 200 + [150] * (n - 200)
    moving = [False] * 200 + [True] * (n - 200)
    r = compute_decoupling(
        sport="bike",
        duration_s=n,
        watts=watts,
        heartrate=heartrate,
        moving=moving,
    )
    # Steady once you drop the not-moving prefix: ~0%
    assert r.pct is not None
    assert abs(r.pct) < 0.5
    assert r.samples_used == n - 200


def test_drops_zero_power_samples() -> None:
    """Power=0 indicates coasting; should not anchor the first-half mean down."""
    half = 2000
    # Coast pattern is symmetric across halves so it shouldn't bias drift.
    watts = ([0, 200] * half)
    heartrate = [150] * (half * 2)
    r = compute_decoupling(
        sport="bike",
        duration_s=half * 2,
        watts=watts,
        heartrate=heartrate,
    )
    assert r.pct is not None
    # All retained samples are 200W / 150bpm => 0% drift exactly.
    assert abs(r.pct) < 1e-6


# ---- Backfill integration ---------------------------------------------------


@pytest.fixture
def config() -> ICUConfig:
    return ICUConfig(
        intervals_icu_athlete_id="i42",
        intervals_icu_api_key="secret",
    )


def _seed_activity(
    *, id_: str, start: str, sport: str, duration_s: int, decoupling: float | None = None
) -> tuple:
    return (id_, start, sport, duration_s, decoupling)


async def test_backfill_populates_null_decoupling(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
) -> None:
    # Seed two eligible activities (NULL decoupling) + one ineligible (swim).
    conn = connect(coach_db_path())
    init_schema(conn)
    try:
        with conn:
            for row in (
                _seed_activity(
                    id_="a1",
                    start="2026-04-21T06:00:00",
                    sport="bike",
                    duration_s=4800,
                ),
                _seed_activity(
                    id_="a2",
                    start="2026-04-22T07:00:00",
                    sport="run",
                    duration_s=3600,
                ),
                _seed_activity(
                    id_="a3",
                    start="2026-04-22T08:00:00",
                    sport="swim",
                    duration_s=3600,
                ),
            ):
                conn.execute(
                    "INSERT INTO activities (id, start_date, sport, duration_s, decoupling) "
                    "VALUES (?, ?, ?, ?, ?)",
                    row,
                )
    finally:
        conn.close()

    half = 2000
    bike_streams = {
        "watts": [200] * half + [200] * half,
        "heartrate": [140] * half + [160] * half,
        "velocity_smooth": [8.0] * (half * 2),
        "moving": [True] * (half * 2),
    }
    run_streams = {
        "watts": None,
        "heartrate": [150] * half + [165] * half,
        "velocity_smooth": [3.0] * (half * 2),
        "moving": [True] * (half * 2),
    }

    respx_mock.get(f"{_BASE_URL}/activity/a1/streams").mock(
        return_value=Response(200, json=bike_streams)
    )
    respx_mock.get(f"{_BASE_URL}/activity/a2/streams").mock(
        return_value=Response(200, json=run_streams)
    )

    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    stats = await backfill(
        limit=10, sleep_s=0.0, config=config, now=now
    )

    # Swim was filtered out by the SQL pre-filter (sport not in allowed set).
    assert stats.candidates == 2
    assert stats.fetched == 2
    assert stats.computed == 2
    assert stats.errors == 0

    conn = connect(coach_db_path())
    try:
        rows = conn.execute(
            "SELECT id, decoupling FROM activities ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    by_id = {r["id"]: r["decoupling"] for r in rows}
    assert by_id["a3"] is None  # swim untouched
    assert by_id["a1"] is not None and by_id["a1"] > 0  # HR drifted up vs. power
    assert by_id["a2"] is not None and by_id["a2"] > 0

    # Audit trail captured the streams payload for rebuildability.
    raw_file = tmp_data_dir / "raw" / "intervals" / "2026-04-23.jsonl"
    assert raw_file.exists()
    endpoints = [
        json.loads(ln)["endpoint"]
        for ln in raw_file.read_text(encoding="utf-8").splitlines()
    ]
    assert "/activity/a1/streams" in endpoints
    assert "/activity/a2/streams" in endpoints

    # events.jsonl has the summary line
    evt_lines = (tmp_data_dir / "events.jsonl").read_text("utf-8").splitlines()
    last = json.loads(evt_lines[-1])
    assert last["command"] == "decoupling_backfill"
    assert last["summary"]["computed"] == 2


async def test_backfill_idempotent_skips_already_populated(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
) -> None:
    """Second run with a populated row touches no streams API."""
    conn = connect(coach_db_path())
    init_schema(conn)
    try:
        with conn:
            conn.execute(
                "INSERT INTO activities (id, start_date, sport, duration_s, decoupling) "
                "VALUES (?, ?, ?, ?, ?)",
                ("a1", "2026-04-21T06:00:00", "bike", 4800, 4.2),
            )
    finally:
        conn.close()

    # If the loop tried to fetch this, the unmocked request would explode.
    stats = await backfill(limit=10, sleep_s=0.0, config=config)
    assert stats.candidates == 0
    assert stats.fetched == 0


async def test_backfill_recompute_overwrites(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
) -> None:
    conn = connect(coach_db_path())
    init_schema(conn)
    try:
        with conn:
            conn.execute(
                "INSERT INTO activities (id, start_date, sport, duration_s, decoupling) "
                "VALUES (?, ?, ?, ?, ?)",
                ("a1", "2026-04-21T06:00:00", "bike", 4800, 99.0),
            )
    finally:
        conn.close()

    half = 2000
    respx_mock.get(f"{_BASE_URL}/activity/a1/streams").mock(
        return_value=Response(
            200,
            json={
                "watts": [200] * (half * 2),
                "heartrate": [150] * (half * 2),
                "moving": [True] * (half * 2),
            },
        )
    )

    stats = await backfill(limit=10, sleep_s=0.0, recompute=True, config=config)
    assert stats.computed == 1

    conn = connect(coach_db_path())
    try:
        v = conn.execute("SELECT decoupling FROM activities WHERE id='a1'").fetchone()[
            "decoupling"
        ]
    finally:
        conn.close()
    # Steady streams => ~0%, definitely not the seeded 99.0.
    assert v is not None
    assert abs(v) < 1e-6


async def test_backfill_continues_past_stream_errors(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
) -> None:
    conn = connect(coach_db_path())
    init_schema(conn)
    try:
        with conn:
            for row in (
                ("a1", "2026-04-21T06:00:00", "bike", 4800, None),
                ("a2", "2026-04-22T06:00:00", "bike", 4800, None),
            ):
                conn.execute(
                    "INSERT INTO activities (id, start_date, sport, duration_s, decoupling) "
                    "VALUES (?, ?, ?, ?, ?)",
                    row,
                )
    finally:
        conn.close()

    # a1 fails; a2 succeeds.
    respx_mock.get(f"{_BASE_URL}/activity/a1/streams").mock(
        return_value=Response(500, json={"error": "boom"})
    )
    half = 2000
    respx_mock.get(f"{_BASE_URL}/activity/a2/streams").mock(
        return_value=Response(
            200,
            json={
                "watts": [200] * (half * 2),
                "heartrate": [140] * half + [160] * half,
                "moving": [True] * (half * 2),
            },
        )
    )

    stats = await backfill(limit=10, sleep_s=0.0, config=config)
    assert stats.fetched == 1
    assert stats.errors == 1
    assert stats.computed == 1
