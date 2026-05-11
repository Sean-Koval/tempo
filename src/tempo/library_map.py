"""``coach library`` — map Tempo session-library refs to intervals.icu library workouts.

Track A of tempo-d5e: read-only discovery + persistence. Track B (compile
from source via ``create_workout``) is deliberately out of scope here; this
module never invokes any intervals.icu write endpoint.

The mapping persists in ``coach.db.library_workout_map`` and is consumed by
``coach push-week`` to attach ``plan_workout_id`` to calendar events
(eliminating the "inline description text" fallback).

The session-library is the source of truth for which refs exist; the
mapping table is a side cache and is fully rebuildable.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from intervals_icu_mcp.auth import ICUConfig, load_config
from intervals_icu_mcp.client import ICUClient
from intervals_icu_mcp.models import Workout

from .paths import repo_root

_SESSION_LIB_HEADING_RE = re.compile(r"^###\s+`([a-z0-9_]+)`", re.MULTILINE)
_SPORT_FROM_FILENAME = ("bike", "run", "swim", "strength", "brick")
_SPORT_TO_ICU_TYPE: dict[str, tuple[str, ...]] = {
    "bike": ("Ride", "VirtualRide", "Workout"),
    "run": ("Run",),
    "swim": ("Swim",),
    "strength": ("WeightTraining",),
    "brick": ("Ride", "Run"),
}


class LibraryMappingError(RuntimeError):
    """Raised on invalid mapping input."""


@dataclass(slots=True)
class LibraryEntry:
    """One session-library ref discovered on disk."""

    library_ref: str
    sport: str
    source_path: Path


@dataclass(slots=True)
class StatusRow:
    """Per-ref view: ref + sport + mapping snapshot + classification."""

    library_ref: str
    sport: str
    status: str  # "mapped" | "unmapped" | "stale"
    intervals_workout_id: int | None = None
    intervals_name: str | None = None
    intervals_folder_id: int | None = None


@dataclass(slots=True)
class ImportPlan:
    """Per-ref proposal for ``coach library import``."""

    library_ref: str
    sport: str
    candidate: Workout | None
    candidates: list[Workout] = field(default_factory=list)
    action: str = "skip"  # "map" | "skip" | "already"


# ---------------------------------------------------------------------------
# Session-library reader
# ---------------------------------------------------------------------------


def list_session_library(*, root: Path | None = None) -> list[LibraryEntry]:
    """Walk ``knowledge/methodology/session-library/*.md`` and yield refs.

    Sport is inferred from the per-sport file name. The legacy monolithic
    ``session-library.md`` is supported but treats every ref as
    ``sport="unknown"`` — the new per-sport layout has been in place since
    Phase 4.
    """
    base = (root or repo_root()) / "knowledge" / "methodology"
    lib_dir = base / "session-library"
    files: list[Path]
    if lib_dir.is_dir():
        files = sorted(p for p in lib_dir.glob("*.md") if p.is_file())
    else:
        legacy = base / "session-library.md"
        files = [legacy] if legacy.is_file() else []

    out: list[LibraryEntry] = []
    for path in files:
        sport = _sport_from_filename(path.stem)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for ref in _SESSION_LIB_HEADING_RE.findall(text):
            out.append(LibraryEntry(library_ref=ref, sport=sport, source_path=path))
    return out


def _sport_from_filename(stem: str) -> str:
    stem_lc = stem.lower()
    if stem_lc in _SPORT_FROM_FILENAME:
        return stem_lc
    return "unknown"


def known_refs(*, root: Path | None = None) -> set[str]:
    return {e.library_ref for e in list_session_library(root=root)}


def find_entry(library_ref: str, *, root: Path | None = None) -> LibraryEntry | None:
    for e in list_session_library(root=root):
        if e.library_ref == library_ref:
            return e
    return None


# ---------------------------------------------------------------------------
# Mapping CRUD
# ---------------------------------------------------------------------------


def upsert_mapping(
    conn: sqlite3.Connection,
    *,
    library_ref: str,
    intervals_workout_id: int,
    intervals_name: str | None,
    intervals_folder_id: int | None,
    sport: str | None,
    notes: str | None = None,
) -> None:
    if not library_ref:
        raise LibraryMappingError("library_ref is required")
    if intervals_workout_id is None:
        raise LibraryMappingError("intervals_workout_id is required")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute(
            """
            INSERT INTO library_workout_map(
                library_ref, intervals_workout_id, intervals_name,
                intervals_folder_id, sport, last_synced_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(library_ref) DO UPDATE SET
                intervals_workout_id = excluded.intervals_workout_id,
                intervals_name       = excluded.intervals_name,
                intervals_folder_id  = excluded.intervals_folder_id,
                sport                = excluded.sport,
                last_synced_at       = excluded.last_synced_at,
                notes                = COALESCE(excluded.notes, library_workout_map.notes)
            """,
            (
                library_ref,
                int(intervals_workout_id),
                intervals_name,
                intervals_folder_id,
                sport,
                now,
                notes,
            ),
        )


def get_mapping(conn: sqlite3.Connection, *, library_ref: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT library_ref, intervals_workout_id, intervals_name, intervals_folder_id, "
        "sport, last_synced_at, notes FROM library_workout_map WHERE library_ref = ?",
        (library_ref,),
    ).fetchone()
    return dict(row) if row is not None else None


def all_mappings(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT library_ref, intervals_workout_id, intervals_name, intervals_folder_id, "
        "sport, last_synced_at, notes FROM library_workout_map"
    ).fetchall()
    return {r["library_ref"]: dict(r) for r in rows}


def delete_mapping(conn: sqlite3.Connection, *, library_ref: str) -> bool:
    with conn:
        cur = conn.execute(
            "DELETE FROM library_workout_map WHERE library_ref = ?",
            (library_ref,),
        )
    return cur.rowcount > 0


def lookup_workout_ids(
    conn: sqlite3.Connection, *, refs: list[str]
) -> dict[str, int]:
    """Resolve a list of refs to mapped intervals workout ids. Unmapped refs are omitted."""
    if not refs:
        return {}
    placeholders = ",".join(["?"] * len(refs))
    rows = conn.execute(
        f"SELECT library_ref, intervals_workout_id FROM library_workout_map "
        f"WHERE library_ref IN ({placeholders})",
        refs,
    ).fetchall()
    return {r["library_ref"]: int(r["intervals_workout_id"]) for r in rows}


# ---------------------------------------------------------------------------
# Status scoring
# ---------------------------------------------------------------------------


def score_status(
    entry: LibraryEntry,
    *,
    mapping: dict[str, Any] | None,
    intervals_ids: set[int] | None,
) -> StatusRow:
    """Classify one ref as mapped / unmapped / stale.

    ``intervals_ids`` carries the workout ids visible right now from
    intervals.icu. Passing ``None`` means "we didn't fetch" — staleness can't
    be asserted without fresh data, so the call defaults to ``mapped`` when a
    row is present.
    """
    if mapping is None:
        return StatusRow(library_ref=entry.library_ref, sport=entry.sport, status="unmapped")
    wid = int(mapping["intervals_workout_id"])
    if intervals_ids is not None and wid not in intervals_ids:
        return StatusRow(
            library_ref=entry.library_ref,
            sport=entry.sport,
            status="stale",
            intervals_workout_id=wid,
            intervals_name=mapping.get("intervals_name"),
            intervals_folder_id=mapping.get("intervals_folder_id"),
        )
    return StatusRow(
        library_ref=entry.library_ref,
        sport=entry.sport,
        status="mapped",
        intervals_workout_id=wid,
        intervals_name=mapping.get("intervals_name"),
        intervals_folder_id=mapping.get("intervals_folder_id"),
    )


# ---------------------------------------------------------------------------
# Fuzzy ranking
# ---------------------------------------------------------------------------


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def fuzzy_rank(library_ref: str, *, workouts: list[Workout]) -> list[Workout]:
    """Rank workouts by similarity to a library ref name. Higher = better."""
    target = _normalize(library_ref.replace("_", " "))
    if not target:
        return list(workouts)

    def _score(w: Workout) -> float:
        cand = _normalize(w.name)
        if not cand:
            return 0.0
        ratio = SequenceMatcher(None, target, cand).ratio()
        bonus = 0.0
        if target in cand or cand in target:
            bonus += 0.25
        # Token overlap — equal-weight nudge so a candidate with all the same
        # tokens beats one with just substring containment of a single word.
        target_tokens = set(target.split())
        cand_tokens = set(cand.split())
        if target_tokens and cand_tokens:
            overlap = len(target_tokens & cand_tokens) / len(target_tokens)
            bonus += 0.2 * overlap
        return ratio + bonus

    return sorted(workouts, key=_score, reverse=True)


def filter_by_sport(workouts: list[Workout], *, sport: str | None) -> list[Workout]:
    """Restrict to intervals.icu workout ``type`` values consistent with the sport.

    Returns the full list if the sport is unknown or has no mapping, on the
    theory that the human selecting is the final arbiter.
    """
    if not sport:
        return workouts
    types = _SPORT_TO_ICU_TYPE.get(sport.lower())
    if not types:
        return workouts
    by_type = [w for w in workouts if (w.type or "") in types]
    return by_type or workouts


# ---------------------------------------------------------------------------
# Intervals fetch
# ---------------------------------------------------------------------------


async def fetch_intervals_workouts(config: ICUConfig | None = None) -> list[Workout]:
    """Read-only fetch of the athlete's intervals workout library."""
    cfg = config or load_config()
    async with ICUClient(cfg) as client:
        return await client.list_workouts()


async def fetch_intervals_workouts_in_folder(
    folder_id: int, config: ICUConfig | None = None
) -> list[Workout]:
    cfg = config or load_config()
    async with ICUClient(cfg) as client:
        return await client.get_workouts_in_folder(folder_id)


# ---------------------------------------------------------------------------
# Build status rows + import plan
# ---------------------------------------------------------------------------


def build_status_rows(
    conn: sqlite3.Connection,
    *,
    entries: list[LibraryEntry] | None = None,
    intervals_ids: set[int] | None = None,
    root: Path | None = None,
) -> list[StatusRow]:
    """Compose the on-disk session library with mapping rows + (optional) fresh ids."""
    entries = entries if entries is not None else list_session_library(root=root)
    mappings = all_mappings(conn)
    return [
        score_status(e, mapping=mappings.get(e.library_ref), intervals_ids=intervals_ids)
        for e in entries
    ]


def plan_import(
    conn: sqlite3.Connection,
    *,
    intervals_workouts: list[Workout],
    folder_id: int | None = None,
    top_k: int = 5,
    root: Path | None = None,
) -> list[ImportPlan]:
    """One ImportPlan per unmapped ref, with top-K ranked intervals candidates."""
    entries = list_session_library(root=root)
    mapped = set(all_mappings(conn))
    candidates_pool: list[Workout]
    if folder_id is not None:
        candidates_pool = [w for w in intervals_workouts if w.folder_id == folder_id]
    else:
        candidates_pool = list(intervals_workouts)

    plans: list[ImportPlan] = []
    for entry in entries:
        if entry.library_ref in mapped:
            continue
        scoped = filter_by_sport(candidates_pool, sport=entry.sport)
        ranked = fuzzy_rank(entry.library_ref, workouts=scoped)[:top_k]
        plans.append(
            ImportPlan(
                library_ref=entry.library_ref,
                sport=entry.sport,
                candidate=ranked[0] if ranked else None,
                candidates=ranked,
            )
        )
    return plans


# ---------------------------------------------------------------------------
# Interactive picker (overridable for tests)
# ---------------------------------------------------------------------------


def _default_picker(
    *,
    library_ref: str,
    sport: str,
    candidates: list[Workout],
) -> int:
    """Default to skipping when no TTY-driven prompt is wired (e.g. ``--yes`` path).

    The CLI replaces this at call time with a typer.prompt-based picker.
    Returning -1 (or any value not in 1..N) signals "skip".
    """
    return -1


def apply_import_plan(
    conn: sqlite3.Connection,
    *,
    plans: list[ImportPlan],
    picker=None,
    auto_top: bool = False,
    dry_run: bool = False,
) -> list[ImportPlan]:
    """Walk an import plan, invoke ``picker`` for each, persist confirmed maps.

    ``picker`` is called as ``picker(library_ref=..., sport=..., candidates=[...])``
    and must return a 1-indexed candidate position or any other int to skip.
    ``auto_top=True`` skips the picker entirely and picks candidate 1 every
    time — used in ``--yes`` mode for scripted runs.
    """
    pick_fn = picker or _default_picker
    decided: list[ImportPlan] = []
    for plan in plans:
        if not plan.candidates:
            plan.action = "skip"
            decided.append(plan)
            continue
        if auto_top:
            choice = 1
        else:
            choice = pick_fn(
                library_ref=plan.library_ref,
                sport=plan.sport,
                candidates=plan.candidates,
            )
        if 1 <= choice <= len(plan.candidates):
            picked = plan.candidates[choice - 1]
            plan.candidate = picked
            plan.action = "map"
            if not dry_run:
                upsert_mapping(
                    conn,
                    library_ref=plan.library_ref,
                    intervals_workout_id=picked.id,
                    intervals_name=picked.name,
                    intervals_folder_id=picked.folder_id,
                    sport=plan.sport,
                )
        else:
            plan.action = "skip"
        decided.append(plan)
    return decided


# ---------------------------------------------------------------------------
# Sync wrappers — keep async surface contained
# ---------------------------------------------------------------------------


def fetch_workouts_sync(*, folder_id: int | None = None) -> list[Workout]:
    if folder_id is not None:
        return asyncio.run(fetch_intervals_workouts_in_folder(folder_id))
    return asyncio.run(fetch_intervals_workouts())


__all__ = [
    "ImportPlan",
    "LibraryEntry",
    "LibraryMappingError",
    "StatusRow",
    "all_mappings",
    "apply_import_plan",
    "build_status_rows",
    "delete_mapping",
    "fetch_intervals_workouts",
    "fetch_intervals_workouts_in_folder",
    "fetch_workouts_sync",
    "filter_by_sport",
    "find_entry",
    "fuzzy_rank",
    "get_mapping",
    "known_refs",
    "list_session_library",
    "lookup_workout_ids",
    "plan_import",
    "score_status",
    "upsert_mapping",
]
