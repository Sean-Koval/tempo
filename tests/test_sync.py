"""Tests for the intervals → coach.db sync."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from httpx import Response
from intervals_icu_mcp.auth import ICUConfig

from tempo.db import connect, init_schema
from tempo.paths import coach_db_path
from tempo.sync import sync

_BASE_URL = "https://intervals.icu/api/v1"


@pytest.fixture
def config():
    return ICUConfig(
        intervals_icu_athlete_id="i42",
        intervals_icu_api_key="secret",
    )


def _activity(
    id_: str,
    date_iso: str,
    type_: str = "Ride",
    load: int = 80,
    distance: float = 30000.0,
    moving: int = 3600,
):
    return {
        "id": id_,
        "start_date_local": date_iso,
        "type": type_,
        "distance": distance,
        "moving_time": moving,
        "icu_training_load": load,
        "icu_intensity": 0.75,
        "average_heartrate": 142,
        "normalized_power": 210,
        "total_elevation_gain": 300.0,
    }


def _wellness(date_iso: str, hrv: float = 65.0, rhr: int = 48):
    return {
        "id": date_iso,
        "hrv": hrv,
        "restingHR": rhr,
        "sleepSecs": 28800,
        "sleepScore": 85.0,
        "readiness": 78.0,
        "weight": 72.5,
        "soreness": 2,
        "comments": "easy day",
    }


async def test_happy_path_upserts_rows(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
):
    respx_mock.get(f"{_BASE_URL}/athlete/i42/activities").mock(
        return_value=Response(
            200,
            json=[
                _activity("a1", "2026-04-22T06:00:00"),
                _activity("a2", "2026-04-21T07:30:00", type_="Run", load=50),
            ],
        )
    )
    respx_mock.get(f"{_BASE_URL}/athlete/i42/wellness").mock(
        return_value=Response(200, json=[_wellness("2026-04-22"), _wellness("2026-04-21")])
    )

    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    stats = await sync(days=7, config=config, now=now)

    assert stats.activities_upserted == 2
    assert stats.wellness_upserted == 2
    assert stats.oldest == "2026-04-16"
    assert stats.newest == "2026-04-23"

    # DB rows
    conn = connect(coach_db_path())
    init_schema(conn)
    try:
        rows = conn.execute(
            "SELECT id, sport, tss FROM activities ORDER BY id"
        ).fetchall()
        assert [(r["id"], r["sport"], r["tss"]) for r in rows] == [
            ("a1", "bike", 80.0),
            ("a2", "run", 50.0),
        ]
        wrows = conn.execute(
            "SELECT date, hrv, rhr, sleep_h FROM wellness_daily ORDER BY date"
        ).fetchall()
        assert [(str(r["date"]), r["hrv"], r["rhr"], r["sleep_h"]) for r in wrows] == [
            ("2026-04-21", 65.0, 48, 8.0),
            ("2026-04-22", 65.0, 48, 8.0),
        ]
    finally:
        conn.close()

    # Raw trail exists and contains both endpoints
    day_file = tmp_data_dir / "raw" / "intervals" / "2026-04-23.jsonl"
    assert day_file.exists()
    lines = day_file.read_text(encoding="utf-8").splitlines()
    endpoints = [json.loads(ln)["endpoint"] for ln in lines]
    assert endpoints == ["/activities", "/wellness"]

    # Events log
    evt = tmp_data_dir / "events.jsonl"
    assert evt.exists()
    line = json.loads(evt.read_text(encoding="utf-8").splitlines()[-1])
    assert line["command"] == "sync"
    assert line["summary"]["activities_upserted"] == 2


async def test_idempotent_second_run(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
):
    respx_mock.get(f"{_BASE_URL}/athlete/i42/activities").mock(
        return_value=Response(200, json=[_activity("a1", "2026-04-22T06:00:00")])
    )
    respx_mock.get(f"{_BASE_URL}/athlete/i42/wellness").mock(
        return_value=Response(200, json=[_wellness("2026-04-22")])
    )

    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await sync(days=7, config=config, now=now)
    await sync(days=7, config=config, now=now)

    conn = connect(coach_db_path())
    init_schema(conn)
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM activities").fetchone()["c"]
        wcount = conn.execute("SELECT COUNT(*) AS c FROM wellness_daily").fetchone()["c"]
    finally:
        conn.close()
    assert count == 1
    assert wcount == 1


async def test_updates_on_upstream_change(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
):
    # First sync: load=80
    respx_mock.get(f"{_BASE_URL}/athlete/i42/activities").mock(
        return_value=Response(200, json=[_activity("a1", "2026-04-22T06:00:00", load=80)])
    )
    respx_mock.get(f"{_BASE_URL}/athlete/i42/wellness").mock(
        return_value=Response(200, json=[])
    )
    await sync(days=7, config=config, now=datetime(2026, 4, 23, tzinfo=UTC))

    # Re-point mock to new load=120, same id
    respx_mock.get(f"{_BASE_URL}/athlete/i42/activities").mock(
        return_value=Response(200, json=[_activity("a1", "2026-04-22T06:00:00", load=120)])
    )
    await sync(days=7, config=config, now=datetime(2026, 4, 23, tzinfo=UTC))

    conn = connect(coach_db_path())
    try:
        tss = conn.execute("SELECT tss FROM activities WHERE id='a1'").fetchone()["tss"]
    finally:
        conn.close()
    assert tss == 120.0


async def test_sport_normalization(
    tmp_data_dir: Path, config: ICUConfig, respx_mock: respx.MockRouter
):
    respx_mock.get(f"{_BASE_URL}/athlete/i42/activities").mock(
        return_value=Response(
            200,
            json=[
                _activity("a1", "2026-04-22T06:00:00", type_="VirtualRide"),
                _activity("a2", "2026-04-22T07:00:00", type_="TrailRun"),
                _activity("a3", "2026-04-22T08:00:00", type_="Swim"),
                _activity("a4", "2026-04-22T09:00:00", type_="WeightTraining"),
                _activity("a5", "2026-04-22T10:00:00", type_="Yoga"),
            ],
        )
    )
    respx_mock.get(f"{_BASE_URL}/athlete/i42/wellness").mock(return_value=Response(200, json=[]))

    await sync(days=1, config=config, now=datetime(2026, 4, 23, tzinfo=UTC))

    conn = connect(coach_db_path())
    try:
        rows = conn.execute("SELECT id, sport FROM activities ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [(r["id"], r["sport"]) for r in rows] == [
        ("a1", "bike"),
        ("a2", "run"),
        ("a3", "swim"),
        ("a4", "strength"),
        ("a5", "other"),
    ]
