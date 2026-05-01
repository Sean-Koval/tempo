"""``coach refresh-prs`` — derive PRs from synced intervals activities.

Sean's all-time PRs were typed by hand once; intervals.icu doesn't expose
an athlete-wide best-effort endpoint, so we derive them from the
``activities`` table populated by ``coach sync``. V1 is whole-activity
matching only — for each standard distance, find the best activity whose
distance falls inside ±5% of the target. Stream-based best-window
extraction (best 20-min power inside a 90-min ride) is a follow-up.

Conflict policy: if the existing PR in profile.yaml is faster than the
best activity-derived candidate, the existing value is kept and a warning
update is recorded. ``--force-from-activities`` overrides. The intent is
that hand-typed historical PRs (efforts that pre-date the synced corpus)
survive a refresh by default; the refresh only adds what activity history
can prove.

The module is importable so future onboarding flows (init, doctor) can
call ``refresh_prs`` directly without going through the CLI.
"""

from __future__ import annotations

import sqlite3
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .athlete import athlete_dir
from .db import connect, init_schema

# ±5% tolerance — tight enough to reject a 5.5k as a 5k PR, loose enough
# to admit a 4.95k that just missed the line. Keep parametric in case we
# loosen for ultra distances.
_DISTANCE_TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class _DistanceSpec:
    """A standard PR distance — what slot it belongs to in profile.yaml."""

    key: str  # e.g. "5k_run"
    sport: str  # "run" | "bike" | "swim"
    target_m: float
    slot: str  # "standard" -> prs.<key>; "other_prs" -> prs.other_prs.<key>


# Run distances — IAAF-ish standards plus Sean's frequently-raced extras.
# Marathon = 42.195 km (use 42195 m exactly so a measured-course race
# matches without rounding noise).
_RUN_DISTANCES: tuple[_DistanceSpec, ...] = (
    _DistanceSpec("400m_run", "run", 400, "other_prs"),
    _DistanceSpec("800m_run", "run", 800, "other_prs"),
    _DistanceSpec("1k_run", "run", 1000, "other_prs"),
    _DistanceSpec("1mi_run", "run", 1609.34, "other_prs"),
    _DistanceSpec("2mi_run", "run", 3218.69, "other_prs"),
    _DistanceSpec("5k_run", "run", 5000, "standard"),
    _DistanceSpec("10k_run", "run", 10000, "standard"),
    _DistanceSpec("15k_run", "run", 15000, "other_prs"),
    _DistanceSpec("10mi_run", "run", 16093.4, "other_prs"),
    _DistanceSpec("half_marathon", "run", 21097.5, "standard"),
    _DistanceSpec("marathon", "run", 42195.0, "standard"),
)

# Swim distances — pool standards plus open-water 5k. The IM swim
# (3.86 km) and 70.3 swim (1.9 km) are race-leg-specific; Sean's other
# IM/70.3 PR slots in profile.yaml roll up the whole-day time.
_SWIM_DISTANCES: tuple[_DistanceSpec, ...] = (
    _DistanceSpec("400m_swim", "swim", 400, "other_prs"),
    _DistanceSpec("800m_swim", "swim", 800, "other_prs"),
    _DistanceSpec("1500m_swim", "swim", 1500, "other_prs"),
    _DistanceSpec("5k_OW", "swim", 5000, "other_prs"),
)

# Bike distances are NOT mileage-based — what matters is power-at-duration.
# 20min_power = best NP from a ~20-min hard effort (used to derive FTP).
# ftp_bike   = best NP from a ~60-min FTP-test-shaped effort.
# Without streams we approximate from whole-activity NP filtered by
# duration window + intensity factor. Heuristic — good enough for a
# baseline, replaced by stream extraction in a follow-up ticket.
_BIKE_POWER_WINDOWS: tuple[tuple[str, int, int, float], ...] = (
    # (key, min_dur_s, max_dur_s, min_intensity_factor)
    ("20min_power", 18 * 60, 25 * 60, 0.95),
    ("ftp_bike", 50 * 60, 75 * 60, 0.92),
)


@dataclass(slots=True)
class PRUpdate:
    """One PR slot's outcome from a refresh pass."""

    key: str
    slot: str  # "standard" | "other_prs" | "bike_power"
    sport: str
    action: str  # "set" | "improved" | "kept" | "kept_manual_faster" | "no_data"
    value: Any = None
    previous: Any = None
    source: str = ""  # provenance string, e.g. "intervals_activity:i12345"
    set_at: str = ""  # YYYY-MM-DD
    notes: str = ""


@dataclass(slots=True)
class RefreshPRsResult:
    profile_path: Path
    activities_considered: int = 0
    changed: bool = False
    no_activities: bool = False
    updates: list[PRUpdate] = field(default_factory=list)

    def improved(self) -> list[PRUpdate]:
        return [u for u in self.updates if u.action in {"set", "improved"}]

    def manual_kept(self) -> list[PRUpdate]:
        return [u for u in self.updates if u.action == "kept_manual_faster"]


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------


def _format_duration(seconds: float | int) -> str:
    """``87`` → ``"1:27"``; ``3725`` → ``"1:02:05"``."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _parse_duration(text: str | int | float | None) -> float | None:
    """Inverse of ``_format_duration``. Accepts ``"M:SS"`` / ``"H:MM:SS"`` /
    bare numerics. Returns seconds. None on garbage so a typo'd manual PR
    doesn't trigger a spurious "improved" comparison."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    text = str(text).strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 1:
        return nums[0]
    return None


def _activity_date(start_date: str | None) -> str:
    """``"2026-04-15T07:30:00"`` → ``"2026-04-15"``. Empty string on bad input."""
    if not start_date:
        return ""
    try:
        return datetime.fromisoformat(start_date.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return start_date.split("T", 1)[0]


# ---------------------------------------------------------------------------
# Activity → best-effort matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ActivityRow:
    id: str
    start_date: str | None
    sport: str
    duration_s: int | None
    distance_m: float | None
    np: float | None
    intensity_factor: float | None


def _load_activities(conn: sqlite3.Connection) -> list[_ActivityRow]:
    rows = conn.execute(
        """
        SELECT id, start_date, sport, duration_s, distance_m, np, intensity_factor
        FROM activities
        """
    ).fetchall()
    return [
        _ActivityRow(
            id=r["id"],
            start_date=r["start_date"],
            sport=r["sport"],
            duration_s=r["duration_s"],
            distance_m=r["distance_m"],
            np=r["np"],
            intensity_factor=r["intensity_factor"],
        )
        for r in rows
    ]


def _best_distance_match(
    activities: list[_ActivityRow], spec: _DistanceSpec
) -> _ActivityRow | None:
    """Fastest whole activity inside ±5% of the target distance."""
    lo = spec.target_m * (1 - _DISTANCE_TOLERANCE)
    hi = spec.target_m * (1 + _DISTANCE_TOLERANCE)
    candidates = [
        a
        for a in activities
        if a.sport == spec.sport
        and a.distance_m is not None
        and a.duration_s is not None
        and a.duration_s > 0
        and lo <= a.distance_m <= hi
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda a: a.duration_s or 1 << 30)


def _best_bike_power(
    activities: list[_ActivityRow],
    *,
    min_dur_s: int,
    max_dur_s: int,
    min_if: float,
) -> _ActivityRow | None:
    """Highest-NP bike activity inside the duration + IF window.

    Heuristic stand-in for true best-window extraction. An NP of 280W on a
    60-min ride at IF 0.96 is ~FTP-test territory; we use that as the
    ftp_bike candidate. False positives are possible (e.g. a hilly 60-min
    Z3 ride) so the IF floor is the gate.
    """
    candidates = [
        a
        for a in activities
        if a.sport == "bike"
        and a.duration_s is not None
        and a.np is not None
        and a.np > 0
        and min_dur_s <= a.duration_s <= max_dur_s
        and (a.intensity_factor is None or a.intensity_factor >= min_if)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.np or 0.0)


# ---------------------------------------------------------------------------
# Profile merge
# ---------------------------------------------------------------------------


def _read_profile(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_profile(path: Path, profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(profile, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _existing_pr_value(profile: dict[str, Any], spec: _DistanceSpec) -> Any:
    prs = profile.get("prs") or {}
    if spec.slot == "standard":
        return prs.get(spec.key)
    return (prs.get("other_prs") or {}).get(spec.key)


def _existing_pr_meta(profile: dict[str, Any], key: str) -> dict[str, Any] | None:
    meta_block = profile.get("prs_meta") or {}
    entry = meta_block.get(key)
    if isinstance(entry, dict):
        return entry
    return None


def _set_pr_value(profile: dict[str, Any], spec: _DistanceSpec, value: str) -> None:
    prs = profile.setdefault("prs", {})
    if spec.slot == "standard":
        prs[spec.key] = value
    else:
        prs.setdefault("other_prs", {})[spec.key] = value


def _set_pr_meta(profile: dict[str, Any], key: str, *, value: str, source: str, set_at: str) -> None:
    meta = profile.setdefault("prs_meta", {})
    meta[key] = {"value": value, "source": source, "set_at": set_at}


def _apply_distance_specs(
    profile: dict[str, Any],
    activities: list[_ActivityRow],
    specs: tuple[_DistanceSpec, ...],
    *,
    force: bool,
    updates: list[PRUpdate],
) -> None:
    for spec in specs:
        match = _best_distance_match(activities, spec)
        existing_value = _existing_pr_value(profile, spec)
        existing_secs = _parse_duration(existing_value)
        existing_meta = _existing_pr_meta(profile, spec.key)
        existing_source = (existing_meta or {}).get("source", "manual")

        if match is None:
            updates.append(
                PRUpdate(
                    key=spec.key,
                    slot=spec.slot,
                    sport=spec.sport,
                    action="no_data",
                    value=existing_value,
                    previous=existing_value,
                    source=existing_source,
                )
            )
            continue

        candidate_secs = float(match.duration_s or 0)
        candidate_value = _format_duration(candidate_secs)
        candidate_source = f"intervals_activity:{match.id}"
        candidate_set_at = _activity_date(match.start_date)

        if existing_secs is not None and existing_secs <= candidate_secs and not force:
            # Manual / older PR is faster — keep it. Only warn when the
            # existing PR is actually faster (strict <), but we treat ==
            # as "no improvement" too.
            updates.append(
                PRUpdate(
                    key=spec.key,
                    slot=spec.slot,
                    sport=spec.sport,
                    action="kept_manual_faster" if existing_secs < candidate_secs else "kept",
                    value=existing_value,
                    previous=existing_value,
                    source=existing_source,
                    notes=(
                        f"activity {match.id} ran {candidate_value} — "
                        f"manual {_format_duration(existing_secs)} stands"
                    )
                    if existing_secs < candidate_secs
                    else "",
                )
            )
            continue

        action = "improved" if existing_secs is not None else "set"
        _set_pr_value(profile, spec, candidate_value)
        _set_pr_meta(
            profile,
            spec.key,
            value=candidate_value,
            source=candidate_source,
            set_at=candidate_set_at,
        )
        updates.append(
            PRUpdate(
                key=spec.key,
                slot=spec.slot,
                sport=spec.sport,
                action=action,
                value=candidate_value,
                previous=existing_value,
                source=candidate_source,
                set_at=candidate_set_at,
            )
        )


def _apply_bike_power(
    profile: dict[str, Any],
    activities: list[_ActivityRow],
    *,
    force: bool,
    updates: list[PRUpdate],
) -> None:
    for key, min_dur, max_dur, min_if in _BIKE_POWER_WINDOWS:
        match = _best_bike_power(
            activities, min_dur_s=min_dur, max_dur_s=max_dur, min_if=min_if
        )
        existing_value = (profile.get("prs") or {}).get(key)
        existing_meta = _existing_pr_meta(profile, key)
        existing_source = (existing_meta or {}).get("source", "manual")

        if match is None:
            updates.append(
                PRUpdate(
                    key=key,
                    slot="standard",
                    sport="bike",
                    action="no_data",
                    value=existing_value,
                    previous=existing_value,
                    source=existing_source,
                )
            )
            continue

        candidate_value = int(round(match.np or 0))
        candidate_source = f"intervals_activity:{match.id}"
        candidate_set_at = _activity_date(match.start_date)

        existing_int: int | None
        if isinstance(existing_value, (int, float)):
            existing_int = int(existing_value)
        else:
            existing_int = None

        if existing_int is not None and existing_int >= candidate_value and not force:
            updates.append(
                PRUpdate(
                    key=key,
                    slot="standard",
                    sport="bike",
                    action="kept_manual_faster" if existing_int > candidate_value else "kept",
                    value=existing_value,
                    previous=existing_value,
                    source=existing_source,
                    notes=(
                        f"activity {match.id} held {candidate_value}W — "
                        f"manual {existing_int}W stands"
                    )
                    if existing_int > candidate_value
                    else "",
                )
            )
            continue

        action = "improved" if existing_int is not None else "set"
        prs = profile.setdefault("prs", {})
        prs[key] = candidate_value
        _set_pr_meta(
            profile,
            key,
            value=str(candidate_value),
            source=candidate_source,
            set_at=candidate_set_at,
        )
        updates.append(
            PRUpdate(
                key=key,
                slot="standard",
                sport="bike",
                action=action,
                value=candidate_value,
                previous=existing_value,
                source=candidate_source,
                set_at=candidate_set_at,
            )
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def refresh_prs(
    *,
    force: bool = False,
    dry_run: bool = False,
    root: Path | None = None,
    db_path: Path | None = None,
) -> RefreshPRsResult:
    """Derive PRs from coach.db activities → athlete/profile.yaml.

    Returns a :class:`RefreshPRsResult`. The file is only rewritten when
    at least one update would change the YAML, so a re-run on the same
    activity corpus is byte-identical.

    With ``dry_run=True`` no file write happens, but all updates are still
    populated in the result so the caller can render a preview.
    """
    profile_path = athlete_dir(root) / "profile.yaml"
    result = RefreshPRsResult(profile_path=profile_path)

    conn = connect(db_path)
    try:
        init_schema(conn)
        activities = _load_activities(conn)
    finally:
        conn.close()

    result.activities_considered = len(activities)
    if not activities:
        result.no_activities = True
        return result

    original_text = profile_path.read_text(encoding="utf-8") if profile_path.is_file() else ""
    profile = _read_profile(profile_path)
    before = deepcopy(profile)

    _apply_distance_specs(
        profile, activities, _RUN_DISTANCES, force=force, updates=result.updates
    )
    _apply_distance_specs(
        profile, activities, _SWIM_DISTANCES, force=force, updates=result.updates
    )
    _apply_bike_power(profile, activities, force=force, updates=result.updates)

    if profile == before:
        return result

    if dry_run:
        result.changed = True  # would-have-changed
        return result

    _write_profile(profile_path, profile)
    new_text = profile_path.read_text(encoding="utf-8")
    if new_text == original_text:
        # YAML round-trip happened to produce identical bytes; restore so
        # disk really is byte-identical.
        profile_path.write_text(original_text, encoding="utf-8")
        return result

    result.changed = True
    return result


# ---------------------------------------------------------------------------
# Summary rendering — for the CLI
# ---------------------------------------------------------------------------


def render_summary_rows(result: RefreshPRsResult) -> list[tuple[str, str, str, str, str]]:
    """Plain-data rows for the Rich table.

    Columns: (status, distance, value, source, note).
    """
    rows: list[tuple[str, str, str, str, str]] = []
    for u in result.updates:
        value = "" if u.value is None else str(u.value)
        if u.action == "set":
            rows.append(("set", u.key, value, u.source or "—", ""))
        elif u.action == "improved":
            prev = "" if u.previous is None else str(u.previous)
            rows.append(("improved", u.key, value, u.source or "—", f"was {prev}"))
        elif u.action == "kept_manual_faster":
            rows.append(("kept (manual faster)", u.key, value, u.source or "manual", u.notes))
        elif u.action == "kept":
            rows.append(("kept", u.key, value, u.source or "manual", ""))
        elif u.action == "no_data":
            rows.append(("no data", u.key, value, u.source or "—", ""))
    return rows


__all__ = [
    "PRUpdate",
    "RefreshPRsResult",
    "refresh_prs",
    "render_summary_rows",
]
