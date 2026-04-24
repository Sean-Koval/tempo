"""Morning wellness capture — write to ``coach.db`` + push to intervals.icu.

The CLI verb (``coach check-in``) handles the interactive prompt; this
module is the pure data path so it's testable without a TTY. The intervals
push is best-effort: DB write is the source of truth, intervals is the
downstream cache.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Any

from .db import connect, init_schema
from .events import log_event


@dataclass(slots=True)
class CheckInInput:
    """Structured wellness capture payload for one date."""

    for_date: str  # ISO date YYYY-MM-DD
    sleep_h: float | None = None
    sleep_score: int | None = None
    hrv: float | None = None
    rhr: int | None = None
    readiness: int | None = None
    body_weight_kg: float | None = None
    soreness: str | None = None  # free text; numeric pushed to intervals
    notes: str | None = None


@dataclass(slots=True)
class CheckInResult:
    for_date: str
    db_written: bool
    intervals_pushed: bool
    intervals_error: str | None = None


def _upsert_db(conn: sqlite3.Connection, d: CheckInInput) -> None:
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
            d.for_date, d.sleep_h, d.sleep_score, d.hrv, d.rhr, d.readiness,
            d.body_weight_kg, d.soreness, d.notes,
        ),
    )


def to_intervals_payload(d: CheckInInput) -> dict[str, Any]:
    """Translate a CheckInInput into the intervals.icu wellness dict.

    Uses camelCase keys per the Wellness API aliases. Numeric soreness gets
    forwarded; free-text soreness stays in coach.db only.
    """
    payload: dict[str, Any] = {"id": d.for_date}
    if d.sleep_h is not None:
        payload["sleepSecs"] = int(d.sleep_h * 3600)
    if d.sleep_score is not None:
        payload["sleepScore"] = d.sleep_score
    if d.hrv is not None:
        payload["hrv"] = d.hrv
    if d.rhr is not None:
        payload["restingHR"] = d.rhr
    if d.readiness is not None:
        payload["readiness"] = d.readiness
    if d.body_weight_kg is not None:
        payload["weight"] = d.body_weight_kg
    if d.notes:
        payload["comments"] = d.notes
    if d.soreness is not None:
        try:
            payload["soreness"] = int(d.soreness)
        except ValueError:
            pass
    return payload


async def _push_to_intervals(d: CheckInInput) -> None:
    from intervals_icu_mcp.auth import load_config
    from intervals_icu_mcp.client import ICUClient

    cfg = load_config()
    async with ICUClient(cfg) as client:
        await client.update_wellness_by_date(d.for_date, to_intervals_payload(d))


def check_in(data: CheckInInput, *, push: bool = True) -> CheckInResult:
    """Upsert wellness to coach.db, optionally push to intervals.icu.

    Always writes to coach.db. Intervals push is best-effort: any failure
    is captured in ``intervals_error`` rather than raised, so the local
    write is never rolled back. Set ``push=False`` to skip entirely.
    """
    conn = connect()
    try:
        init_schema(conn)
        with conn:
            _upsert_db(conn, data)
    finally:
        conn.close()

    result = CheckInResult(
        for_date=data.for_date, db_written=True, intervals_pushed=False
    )
    if push:
        try:
            asyncio.run(_push_to_intervals(data))
            result.intervals_pushed = True
        except Exception as e:
            result.intervals_error = str(e)

    log_event(
        "check_in",
        {
            "date": data.for_date,
            "push": push,
            "intervals_pushed": result.intervals_pushed,
        },
    )
    return result


__all__ = [
    "CheckInInput",
    "CheckInResult",
    "check_in",
    "to_intervals_payload",
]
