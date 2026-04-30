"""Aerobic decoupling computation from intervals.icu activity streams.

Decoupling = how much HR drifted upward relative to a sustained external
intensity (power on the bike, pace on the run). The TrainingPeaks
convention is used:

    drift_pct = (first_half_ratio - second_half_ratio) / first_half_ratio * 100

where ``ratio`` is mean(intensity) / mean(HR) over moving samples.
Positive % => HR climbed faster than the work output (aerobic strain).
Literature flags >5% on a steady endurance effort as a meaningful signal.

Pure functions only. The CLI/derive layer fetches streams; this module
just consumes already-fetched ``ActivityStreams``.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime

# Activities shorter than this are noisy — recovery rides, drills,
# race-pace 5Ks. Decoupling on those does not generalise to fitness.
MIN_MOVING_SECONDS = 45 * 60


@dataclass(slots=True, frozen=True)
class DecouplingResult:
    """Outcome of a single computation."""

    pct: float | None
    """Drift percent, or None if not computable."""

    method: str
    """``pw_hr`` (power vs HR), ``pa_hr`` (speed vs HR), or ``skipped``."""

    reason: str | None = None
    """Why we returned None — for diagnostic logging."""

    samples_used: int = 0
    """Count of moving samples that contributed to the calculation."""


def _filter_moving(
    intensity: list[float | int | None],
    heartrate: list[int | None],
    moving: list[bool | None] | None,
) -> tuple[list[float], list[float]]:
    """Align two parallel streams; drop samples missing either side or not moving.

    A sample is kept iff:
    - HR is present and > 0
    - intensity is present and > 0 (power=0 = coasting; speed=0 = stopped)
    - ``moving[i]`` is True (or moving stream absent => assume moving)
    """
    n = min(len(intensity), len(heartrate))
    if moving is not None:
        n = min(n, len(moving))

    out_i: list[float] = []
    out_hr: list[float] = []
    for i in range(n):
        if moving is not None and not moving[i]:
            continue
        v = intensity[i]
        h = heartrate[i]
        if v is None or h is None:
            continue
        vf = float(v)
        hf = float(h)
        if vf <= 0.0 or hf <= 0.0:
            continue
        out_i.append(vf)
        out_hr.append(hf)
    return out_i, out_hr


def _drift_pct(intensity: list[float], heartrate: list[float]) -> float | None:
    """Two-half mean(intensity)/mean(HR) drift, in percent.

    Returns None if either half is empty after splitting.
    """
    n = len(intensity)
    if n < 2:
        return None

    mid = n // 2
    first_i, first_h = intensity[:mid], heartrate[:mid]
    second_i, second_h = intensity[mid:], heartrate[mid:]

    if not first_i or not second_i or not first_h or not second_h:
        return None

    first_ratio = (sum(first_i) / len(first_i)) / (sum(first_h) / len(first_h))
    second_ratio = (sum(second_i) / len(second_i)) / (sum(second_h) / len(second_h))
    if first_ratio == 0:
        return None

    return (first_ratio - second_ratio) / first_ratio * 100.0


def compute_decoupling(
    sport: str,
    duration_s: int | None,
    *,
    watts: list[int | None] | None = None,
    velocity_smooth: list[float | None] | None = None,
    heartrate: list[int | None] | None = None,
    moving: list[bool | None] | None = None,
) -> DecouplingResult:
    """Compute aerobic decoupling for one activity.

    Sport routing:
    - ``bike`` -> Pw:HR; falls back to Pa:HR if no watts.
    - ``run`` (or ``walk``) -> Pa:HR.
    - anything else (swim, strength, other) -> skipped.

    Returns DecouplingResult with pct=None when not computable; the caller
    distinguishes "tried and failed" from "did not try" via ``method``.
    """
    if sport not in ("bike", "run"):
        return DecouplingResult(pct=None, method="skipped", reason=f"sport={sport}")

    if duration_s is not None and duration_s < MIN_MOVING_SECONDS:
        return DecouplingResult(
            pct=None, method="skipped", reason=f"duration<{MIN_MOVING_SECONDS}s"
        )

    if not heartrate:
        return DecouplingResult(pct=None, method="skipped", reason="no_hr_stream")

    if sport == "bike" and watts:
        intensity, hr = _filter_moving(watts, heartrate, moving)
        method = "pw_hr"
    elif velocity_smooth:
        intensity, hr = _filter_moving(velocity_smooth, heartrate, moving)
        method = "pa_hr"
    else:
        return DecouplingResult(
            pct=None, method="skipped", reason="no_intensity_stream"
        )

    pct = _drift_pct(intensity, hr)
    if pct is None:
        return DecouplingResult(
            pct=None,
            method=method,
            reason="insufficient_samples",
            samples_used=len(intensity),
        )

    return DecouplingResult(pct=pct, method=method, samples_used=len(intensity))


# ---- Backfill loop ----------------------------------------------------------
#
# Decoupling is computed lazily, not during sync, because each activity needs
# its full HR/power streams (separate API call). We keep sync O(activities)
# and add a separate verb that walks ``decoupling IS NULL`` rows.


@dataclass(slots=True)
class BackfillStats:
    """Result of one backfill run."""

    candidates: int = 0
    """Activities that matched the WHERE clause before the limit."""

    fetched: int = 0
    """Activities for which we successfully fetched streams."""

    computed: int = 0
    """Activities that yielded a non-null decoupling value."""

    skipped: int = 0
    """Activities the pure function declined to score (no HR, too short, …)."""

    errors: int = 0
    """Stream fetches that raised."""

    duration_ms: int = 0


def _select_candidates(
    conn: sqlite3.Connection,
    *,
    recompute: bool,
    limit: int,
) -> list[tuple[str, str, int | None]]:
    """Pick (id, sport, duration_s) rows to process, oldest first.

    Oldest-first lets a paused/resumed backfill make even progress across
    history rather than hammering the most recent week.
    """
    where = "" if recompute else "WHERE decoupling IS NULL"
    sport_filter = "sport IN ('bike', 'run')"
    where = f"{where} AND {sport_filter}" if where else f"WHERE {sport_filter}"
    rows = conn.execute(
        f"SELECT id, sport, duration_s FROM activities "
        f"{where} "
        f"AND duration_s IS NOT NULL AND duration_s >= ? "
        f"ORDER BY start_date ASC LIMIT ?",
        (MIN_MOVING_SECONDS, limit),
    ).fetchall()
    return [(r["id"], r["sport"], r["duration_s"]) for r in rows]


async def backfill(
    *,
    limit: int = 50,
    sleep_s: float = 0.25,
    recompute: bool = False,
    config=None,
    now: datetime | None = None,
) -> BackfillStats:
    """Walk activities with NULL decoupling and populate it from streams.

    Args:
        limit: Max candidates to process this run. Tune for rate-limit
            tolerance — the intervals.icu free tier is generous but not
            unlimited.
        sleep_s: Pause between stream fetches.
        recompute: When True, recompute even if already populated.
        config: Optional pre-loaded ``ICUConfig`` (tests inject this).
        now: Timestamp override for events log.

    Returns:
        BackfillStats with fetch / compute / error counters.
    """
    # Imported here to keep the module importable for unit tests that
    # never hit the network.
    from intervals_icu_mcp.auth import load_config
    from intervals_icu_mcp.client import ICUClient

    from .db import connect, init_schema
    from .events import log_event
    from .raw import append_raw

    start = time.monotonic()
    ts_now = now or datetime.now(UTC)
    cfg = config or load_config()

    stats = BackfillStats()

    conn = connect()
    try:
        init_schema(conn)
        candidates = _select_candidates(conn, recompute=recompute, limit=limit)
        stats.candidates = len(candidates)

        if not candidates:
            stats.duration_ms = int((time.monotonic() - start) * 1000)
            return stats

        async with ICUClient(cfg) as client:
            for activity_id, sport, duration_s in candidates:
                try:
                    streams = await client.get_activity_streams(
                        activity_id,
                        streams=["watts", "heartrate", "velocity_smooth", "moving"],
                    )
                except Exception:
                    stats.errors += 1
                    if sleep_s:
                        await asyncio.sleep(sleep_s)
                    continue

                stats.fetched += 1
                append_raw(
                    "intervals",
                    f"/activity/{activity_id}/streams",
                    streams.model_dump(mode="json"),
                    params={"activity_id": activity_id},
                    now=ts_now,
                )

                result = compute_decoupling(
                    sport=sport,
                    duration_s=duration_s,
                    watts=streams.watts,
                    velocity_smooth=streams.velocity_smooth,
                    heartrate=streams.heartrate,
                    moving=streams.moving,
                )

                if result.pct is None:
                    stats.skipped += 1
                else:
                    stats.computed += 1
                    with conn:
                        conn.execute(
                            "UPDATE activities SET decoupling = ? WHERE id = ?",
                            (result.pct, activity_id),
                        )

                if sleep_s:
                    await asyncio.sleep(sleep_s)
    finally:
        conn.close()

    stats.duration_ms = int((time.monotonic() - start) * 1000)
    log_event(
        "decoupling_backfill",
        {
            "candidates": stats.candidates,
            "fetched": stats.fetched,
            "computed": stats.computed,
            "skipped": stats.skipped,
            "errors": stats.errors,
            "limit": limit,
            "recompute": recompute,
            "duration_ms": stats.duration_ms,
        },
        now=ts_now,
    )
    return stats
