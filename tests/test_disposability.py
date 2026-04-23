"""Integration test: delete coach.db, re-run sync+derive, assert identical state.

This is milestone criterion #3 for Phase 1 (see plan / tempo-wns.8):
``coach.db`` is disposable — every byte is reconstructible from raw API
responses.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import respx
from httpx import Response
from intervals_icu_mcp.auth import ICUConfig

from tempo.db import connect
from tempo.derive import derive
from tempo.paths import coach_db_path
from tempo.sync import sync

_BASE_URL = "https://intervals.icu/api/v1"


def _fixture_activities():
    return [
        {
            "id": f"a{i}",
            "start_date_local": f"2026-04-{i + 1:02d}T06:00:00",
            "type": "Ride" if i % 2 == 0 else "Run",
            "distance": 25000.0 + i * 500,
            "moving_time": 3600 + i * 120,
            "icu_training_load": 60 + i * 2,
            "icu_intensity": 0.72,
            "average_heartrate": 140 + i,
            "normalized_power": 200 + i,
            "total_elevation_gain": 200.0 + i * 5,
        }
        for i in range(20)
    ]


def _fixture_wellness():
    return [
        {
            "id": f"2026-04-{i + 1:02d}",
            "hrv": 60.0 + i * 0.1,
            "restingHR": 48,
            "sleepSecs": 27000 + i * 60,
            "sleepScore": 82.0,
            "readiness": 75.0,
            "weight": 72.0 + i * 0.01,
        }
        for i in range(20)
    ]


def _snapshot(conn):
    return {
        "activities": [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM activities ORDER BY id"
            ).fetchall()
        ],
        "wellness": [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM wellness_daily ORDER BY date"
            ).fetchall()
        ],
        "load": [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM load_daily ORDER BY date"
            ).fetchall()
        ],
    }


async def test_disposable_db_reconstructs_identically(
    tmp_data_dir: Path, respx_mock: respx.MockRouter
):
    config = ICUConfig(
        intervals_icu_athlete_id="i42",
        intervals_icu_api_key="secret",
    )
    respx_mock.get(f"{_BASE_URL}/athlete/i42/activities").mock(
        return_value=Response(200, json=_fixture_activities())
    )
    respx_mock.get(f"{_BASE_URL}/athlete/i42/wellness").mock(
        return_value=Response(200, json=_fixture_wellness())
    )

    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)

    # Run 1: sync + derive
    await sync(days=30, config=config, now=now)
    derive(now=now)

    db_path = coach_db_path()
    conn = connect(db_path)
    try:
        before = _snapshot(conn)
    finally:
        conn.close()

    assert len(before["activities"]) == 20
    assert len(before["wellness"]) == 20
    assert len(before["load"]) > 0

    # Nuke the DB + WAL sidecars
    db_path.unlink()
    for side in (".wal", ".shm"):
        p = db_path.with_suffix(db_path.suffix + side)
        if p.exists():
            p.unlink()
    assert not db_path.exists()

    # Run 2: sync + derive with identical upstream state
    await sync(days=30, config=config, now=now)
    derive(now=now)

    conn = connect(db_path)
    try:
        after = _snapshot(conn)
    finally:
        conn.close()

    assert len(after["activities"]) == len(before["activities"])
    assert len(after["wellness"]) == len(before["wellness"])
    assert len(after["load"]) == len(before["load"])

    # Activities and wellness should match exactly (string/int compare).
    assert after["activities"] == before["activities"]
    assert after["wellness"] == before["wellness"]

    # Load series is float — compare within epsilon.
    for a, b in zip(after["load"], before["load"], strict=True):
        assert a["date"] == b["date"]
        for col in ("ctl", "atl", "tsb", "ctl_bike", "ctl_run", "ctl_swim", "ramp_7d"):
            av = a[col] or 0.0
            bv = b[col] or 0.0
            assert math.isclose(av, bv, abs_tol=1e-9), f"{col} drift @ {a['date']}"
