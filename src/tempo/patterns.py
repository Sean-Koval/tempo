"""Adherence pattern miner — surface systematic completion drop-offs.

Looks at the last ``window_weeks`` of ``sessions_planned`` + ``adherence`` and
flags buckets whose completion rate is meaningfully below the overall baseline:

- weekday (Mon..Sun)
- sport (ride/run/swim/...)
- session-type (purpose, falling back to library_ref bucket)
- context (travel-week vs non-travel — derived from journal text + adherence
  reasons)

Significance gate: a bucket is flagged when its sample size is at least
``min_samples`` AND its z-score against the overall completion rate is at
or below ``-sigma_threshold`` (one-sided; we only care about drop-offs).
False negatives are preferable to noise — preflight signals must be
trustworthy or skills will learn to ignore them.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import plans
from .paths import repo_root

_WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Travel markers in journal text. Word-boundary anchored so "traveler" but not
# "untravelled" patterns also match — false positives here are OK because we
# require a separate sigma gate downstream.
_TRAVEL_PATTERN = re.compile(
    r"\b(travel(?:ing|ed|led)?|trip|flight|out of town|on the road|away)\b",
    re.IGNORECASE,
)
_JOURNAL_FILENAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


@dataclass(frozen=True)
class PatternSignal:
    """A single drop-off finding produced by :func:`adherence_patterns`."""

    dimension: str  # "weekday" | "sport" | "session_type" | "context"
    value: str
    completion_rate: float
    baseline: float
    samples: int
    z_score: float
    severity: str  # "warn"
    message: str


def adherence_patterns(
    conn: sqlite3.Connection,
    *,
    window_weeks: int = 8,
    end_date: date | None = None,
    min_samples: int = 4,
    sigma_threshold: float = 1.5,
    journal_dir: Path | None = None,
    repo_root_override: Path | None = None,
) -> dict[str, Any]:
    """Mine ``window_weeks`` of session data for systematic adherence drop-offs.

    Args:
        conn: Open ``coach.db`` connection.
        window_weeks: How many ISO weeks back from ``end_date`` to analyze.
        end_date: Last day in the window. Defaults to today.
        min_samples: Minimum bucket size before a deviation is flagged.
        sigma_threshold: Minimum |z-score| below baseline to flag (positive).
        journal_dir: Override for ``journal/`` (test isolation). Defaults to
            ``repo_root() / "journal"``.
        repo_root_override: Override for the repo root used when
            ``journal_dir`` isn't given.

    Returns:
        A dict with keys ``status`` ("ok" or "insufficient_data"),
        ``weeks_analyzed``, ``baseline_completion_rate``, ``signals``, and
        diagnostic counters. ``signals`` is a list of PatternSignal dicts;
        empty if no bucket cleared the gate.
    """
    if end_date is None:
        end_date = date.today()
    start_date = end_date - timedelta(weeks=window_weeks)
    items = _fetch_items(conn, start_d=start_date, end_d=end_date)

    weeks_observed = len({i["week_id"] for i in items if i["week_id"]})
    if weeks_observed < window_weeks:
        return {
            "status": "insufficient_data",
            "window_weeks": window_weeks,
            "weeks_observed": weeks_observed,
            "weeks_required": window_weeks,
            "weeks_analyzed": [],
            "total_planned": len(items),
            "total_completed": sum(1 for i in items if i["completed"]),
            "baseline_completion_rate": None,
            "signals": [],
            "reason": (
                f"only {weeks_observed} weeks of session data in window "
                f"(need >= {window_weeks})"
            ),
        }

    total_planned = len(items)
    total_completed = sum(1 for i in items if i["completed"])
    baseline = total_completed / total_planned if total_planned else 0.0

    travel_weeks = _detect_travel_weeks(
        items,
        journal_dir=journal_dir
        or ((repo_root_override or repo_root()) / "journal"),
        start_d=start_date,
        end_d=end_date,
    )

    signals: list[PatternSignal] = []
    signals.extend(
        _bucket_signals(
            items,
            dimension="weekday",
            key_fn=lambda i: _weekday_label(i["date"]),
            baseline=baseline,
            min_samples=min_samples,
            sigma=sigma_threshold,
        )
    )
    signals.extend(
        _bucket_signals(
            items,
            dimension="sport",
            key_fn=lambda i: i["sport"] or "unknown",
            baseline=baseline,
            min_samples=min_samples,
            sigma=sigma_threshold,
        )
    )
    signals.extend(
        _bucket_signals(
            items,
            dimension="session_type",
            key_fn=_session_type_key,
            baseline=baseline,
            min_samples=min_samples,
            sigma=sigma_threshold,
        )
    )
    signals.extend(
        _bucket_signals(
            items,
            dimension="context",
            key_fn=lambda i: "travel_week" if i["week_id"] in travel_weeks else "home_week",
            baseline=baseline,
            min_samples=min_samples,
            sigma=sigma_threshold,
            # Don't emit "home_week" — it'd just restate the baseline.
            value_filter=lambda v: v == "travel_week",
        )
    )

    weeks_analyzed = sorted({i["week_id"] for i in items if i["week_id"]})
    return {
        "status": "ok",
        "window_weeks": window_weeks,
        "weeks_observed": weeks_observed,
        "weeks_analyzed": weeks_analyzed,
        "weeks_required": window_weeks,
        "total_planned": total_planned,
        "total_completed": total_completed,
        "baseline_completion_rate": round(baseline, 4),
        "travel_weeks": sorted(travel_weeks),
        "signals": [asdict(s) for s in signals],
    }


def _fetch_items(
    conn: sqlite3.Connection, *, start_d: date, end_d: date
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sp.id          AS planned_session_id,
               sp.week_id     AS week_id,
               sp.date        AS date,
               sp.sport       AS sport,
               sp.library_ref AS library_ref,
               sp.purpose     AS purpose,
               ad.completed   AS completed,
               ad.reason      AS reason
        FROM sessions_planned sp
        LEFT JOIN adherence ad ON ad.planned_session_id = sp.id
        WHERE sp.date >= ? AND sp.date <= ?
        ORDER BY sp.date
        """,
        (start_d.isoformat(), end_d.isoformat()),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "planned_session_id": r["planned_session_id"],
                "week_id": r["week_id"],
                "date": r["date"],
                "sport": r["sport"],
                "library_ref": r["library_ref"],
                "purpose": r["purpose"],
                "completed": bool(r["completed"]) if r["completed"] is not None else False,
                "reason": r["reason"],
            }
        )
    return items


def _weekday_label(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        return _WEEKDAYS[date.fromisoformat(date_str).weekday()]
    except (ValueError, IndexError):
        return None


def _session_type_key(item: dict[str, Any]) -> str | None:
    """Bucket a session by its purpose (preferred) or library_ref shape.

    Purpose is the planner's authored intent ("long_ride_endurance"); library_ref
    is the templated session id ("ride_z2_long"). Either works; purpose wins
    when both exist because it's closer to athlete-facing language.
    """
    purpose = item.get("purpose")
    if purpose:
        return _normalize_session_type(purpose)
    lib = item.get("library_ref")
    if lib:
        return _normalize_session_type(lib)
    return None


def _normalize_session_type(s: str) -> str:
    return s.strip().lower().replace(" ", "_")


def _bucket_signals(
    items: list[dict[str, Any]],
    *,
    dimension: str,
    key_fn: Any,
    baseline: float,
    min_samples: int,
    sigma: float,
    value_filter: Any | None = None,
) -> list[PatternSignal]:
    """Aggregate ``items`` by ``key_fn`` and emit drop-off signals."""
    buckets: dict[str, dict[str, int]] = {}
    for item in items:
        k = key_fn(item)
        if k is None:
            continue
        if value_filter is not None and not value_filter(k):
            continue
        b = buckets.setdefault(k, {"planned": 0, "completed": 0})
        b["planned"] += 1
        if item["completed"]:
            b["completed"] += 1

    out: list[PatternSignal] = []
    for value, b in buckets.items():
        n = b["planned"]
        if n < min_samples:
            continue
        rate = b["completed"] / n
        z = _binomial_z(rate=rate, baseline=baseline, n=n)
        if z is None or z > -sigma:
            continue
        out.append(
            PatternSignal(
                dimension=dimension,
                value=value,
                completion_rate=round(rate, 4),
                baseline=round(baseline, 4),
                samples=n,
                z_score=round(z, 2),
                severity="warn",
                message=(
                    f"{value} adherence {rate*100:.0f}% vs {baseline*100:.0f}% "
                    f"baseline ({n} sessions, z={z:.1f})"
                ),
            )
        )
    out.sort(key=lambda s: s.z_score)
    return out


def _binomial_z(*, rate: float, baseline: float, n: int) -> float | None:
    """One-sample z-score against the baseline proportion. Returns ``None`` if
    the baseline is degenerate (0 or 1) — those buckets carry no information."""
    if n <= 0 or baseline <= 0.0 or baseline >= 1.0:
        return None
    sd = math.sqrt(baseline * (1.0 - baseline) / n)
    if sd == 0.0:
        return None
    return (rate - baseline) / sd


def _detect_travel_weeks(
    items: list[dict[str, Any]],
    *,
    journal_dir: Path,
    start_d: date,
    end_d: date,
) -> set[str]:
    """Identify ISO weeks tagged as travel.

    Two evidence sources:
      1. ``journal/YYYY-MM-DD.md`` — date in window, body matches the travel
         regex. Each tagged day promotes its containing ISO week.
      2. ``adherence.reason`` — any session in a week whose reason contains
         "travel" promotes that week.
    """
    travel_weeks: set[str] = set()

    if journal_dir.is_dir():
        for entry in journal_dir.iterdir():
            if not entry.is_file():
                continue
            m = _JOURNAL_FILENAME.match(entry.name)
            if not m:
                continue
            try:
                d = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if d < start_d or d > end_d:
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            if _TRAVEL_PATTERN.search(text):
                travel_weeks.add(plans.week_id_for(d))

    for item in items:
        reason = item.get("reason")
        if reason and "travel" in reason.lower() and item.get("week_id"):
            travel_weeks.add(item["week_id"])

    return travel_weeks


__all__ = [
    "PatternSignal",
    "adherence_patterns",
]
