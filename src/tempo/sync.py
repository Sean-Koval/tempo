"""Deterministic intervals.icu → coach.db sync.

Pulls activities + wellness over a date window, appends every raw
response to ``data/raw/intervals/YYYY-MM-DD.jsonl``, and upserts the
normalized rows into ``coach.db``.

Not agentic — pure ETL. Idempotent: re-running with the same upstream
state produces the same DB state.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from intervals_icu_mcp.auth import ICUConfig, load_config
from intervals_icu_mcp.client import ICUClient
from intervals_icu_mcp.models import ActivitySummary, Wellness

from .db import connect, init_schema
from .events import log_event
from .raw import append_raw

# Generous page size — the submodule client clips at `limit`, so we
# pass a ceiling big enough that the date window is the real selector.
_ACTIVITY_FETCH_LIMIT = 1000


_SPORT_MAP: dict[str, str] = {
    "Ride": "bike",
    "VirtualRide": "bike",
    "GravelRide": "bike",
    "MountainBikeRide": "bike",
    "Run": "run",
    "TrailRun": "run",
    "VirtualRun": "run",
    "Swim": "swim",
    "WeightTraining": "strength",
    "Workout": "strength",
}


def _normalize_sport(raw: str | None) -> str:
    if not raw:
        return "other"
    return _SPORT_MAP.get(raw, "other")


@dataclass(slots=True)
class SyncStats:
    activities_upserted: int = 0
    wellness_upserted: int = 0
    duration_ms: int = 0
    days: int = 0
    oldest: str = ""
    newest: str = ""


def _upsert_activity(
    conn: sqlite3.Connection,
    a: ActivitySummary,
    raw_path: str,
) -> None:
    conn.execute(
        """
        INSERT INTO activities (
            id, start_date, sport, duration_s, distance_m,
            tss, np, intensity_factor,
            avg_hr, elevation_gain_m, raw_json_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            start_date = excluded.start_date,
            sport = excluded.sport,
            duration_s = excluded.duration_s,
            distance_m = excluded.distance_m,
            tss = excluded.tss,
            np = excluded.np,
            intensity_factor = excluded.intensity_factor,
            avg_hr = excluded.avg_hr,
            elevation_gain_m = excluded.elevation_gain_m,
            raw_json_path = excluded.raw_json_path
        """,
        (
            a.id,
            a.start_date_local.isoformat() if a.start_date_local else None,
            _normalize_sport(a.type),
            a.moving_time,
            a.distance,
            float(a.icu_training_load) if a.icu_training_load is not None else None,
            float(a.normalized_power) if a.normalized_power is not None else None,
            a.icu_intensity,
            a.average_heartrate,
            a.total_elevation_gain,
            raw_path,
        ),
    )


def _upsert_wellness(conn: sqlite3.Connection, w: Wellness) -> None:
    sleep_h = (w.sleep_secs / 3600.0) if w.sleep_secs is not None else None
    sleep_score = int(w.sleep_score) if w.sleep_score is not None else None
    readiness = int(w.readiness) if w.readiness is not None else None

    conn.execute(
        """
        INSERT INTO wellness_daily (
            date, sleep_h, sleep_score, hrv, rhr, readiness,
            body_weight_kg, soreness, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            sleep_h = excluded.sleep_h,
            sleep_score = excluded.sleep_score,
            hrv = excluded.hrv,
            rhr = excluded.rhr,
            readiness = excluded.readiness,
            body_weight_kg = excluded.body_weight_kg,
            soreness = excluded.soreness,
            notes = excluded.notes
        """,
        (
            w.id,
            sleep_h,
            sleep_score,
            w.hrv,
            w.resting_hr,
            readiness,
            w.weight,
            str(w.soreness) if w.soreness is not None else None,
            w.comments,
        ),
    )


async def sync(
    days: int = 90,
    config: ICUConfig | None = None,
    now: datetime | None = None,
) -> SyncStats:
    """Pull ``days`` days of activities + wellness from intervals → coach.db."""
    start = time.monotonic()
    ts_now = now or datetime.now(UTC)
    today: date = ts_now.date()
    oldest = (today - timedelta(days=days)).isoformat()
    newest = today.isoformat()

    cfg = config or load_config()

    stats = SyncStats(days=days, oldest=oldest, newest=newest)

    conn = connect()
    try:
        init_schema(conn)

        async with ICUClient(cfg) as client:
            activities = await client.get_activities(
                oldest=oldest, newest=newest, limit=_ACTIVITY_FETCH_LIMIT
            )
            activities_raw = [a.model_dump(mode="json", by_alias=True) for a in activities]
            activity_path = append_raw(
                "intervals",
                "/activities",
                activities_raw,
                params={"oldest": oldest, "newest": newest},
                now=ts_now,
            )

            wellness = await client.get_wellness(oldest=oldest, newest=newest)
            wellness_raw = [w.model_dump(mode="json", by_alias=True) for w in wellness]
            append_raw(
                "intervals",
                "/wellness",
                wellness_raw,
                params={"oldest": oldest, "newest": newest},
                now=ts_now,
            )

        with conn:
            for a in activities:
                _upsert_activity(conn, a, str(activity_path))
                stats.activities_upserted += 1
            for w in wellness:
                _upsert_wellness(conn, w)
                stats.wellness_upserted += 1
    finally:
        conn.close()

    stats.duration_ms = int((time.monotonic() - start) * 1000)
    log_event(
        "sync",
        {
            "days": days,
            "oldest": oldest,
            "newest": newest,
            "activities_upserted": stats.activities_upserted,
            "wellness_upserted": stats.wellness_upserted,
            "duration_ms": stats.duration_ms,
        },
        now=ts_now,
    )
    return stats
