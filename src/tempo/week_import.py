"""``coach week import`` — parse plans/<plan-id>/weeks/<week_id>.md into
``sessions_planned`` rows.

Bridges the gap between the ``/plan-training-week`` skill (writes
markdown) and ``coach push-week`` (reads SQLite). Each session in the
week markdown carries a fenced ```yaml tempo:session block — the prose
above stays human-readable, the YAML block is the machine-parseable
truth.

The parser is intentionally narrow: it ignores the prose, reads only
the YAML blocks, and UPSERTs idempotently. The push-week-owned columns
(``pushed_to_intervals``, ``intervals_event_id``) are never touched by
import.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import plans as _plans

# Fence sentinel — picked to be visually distinct from a plain `yaml`
# block so the prose author can drop the marker without confusing
# editors that highlight on the language tag alone.
_FENCE_OPEN_RE = re.compile(r"^\s*```yaml\s+tempo:session\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")

# Content columns we UPSERT. push-week owns pushed_to_intervals and
# intervals_event_id — import must not clobber them.
_CONTENT_COLS = (
    "plan_id",
    "week_id",
    "date",
    "sport",
    "library_ref",
    "target_tss",
    "target_duration_s",
    "purpose",
    "notes",
)


class ImportError(RuntimeError):
    """Raised on parse or resolution failures with caller-friendly text."""


@dataclass(slots=True)
class ParsedSession:
    """One YAML block, validated and ready for UPSERT."""

    id_slug: str
    date: str
    sport: str
    library_ref: str | None = None
    target_tss: float | None = None
    target_duration_s: int | None = None
    purpose: str | None = None
    notes: str | None = None
    source_line: int = 0

    def session_id(self, *, plan_id: str, week_id: str) -> str:
        return f"{plan_id}/{week_id}/{self.id_slug}"


@dataclass(slots=True)
class SessionChange:
    """Diff against the current DB row for one session."""

    id_slug: str
    session_id: str
    date: str
    sport: str
    library_ref: str | None
    target_tss: float | None
    target_duration_s: int | None
    purpose: str | None
    notes: str | None
    action: str  # "insert" | "update" | "noop"
    diff_fields: tuple[str, ...] = ()


@dataclass(slots=True)
class ImportResult:
    plan_id: str
    week_id: str
    week_file: Path
    changes: list[SessionChange] = field(default_factory=list)
    dry_run: bool = False

    @property
    def n_parsed(self) -> int:
        return len(self.changes)

    @property
    def n_inserted(self) -> int:
        return sum(1 for c in self.changes if c.action == "insert")

    @property
    def n_updated(self) -> int:
        return sum(1 for c in self.changes if c.action == "update")

    @property
    def n_noop(self) -> int:
        return sum(1 for c in self.changes if c.action == "noop")

    @property
    def changed(self) -> bool:
        return any(c.action != "noop" for c in self.changes)


# ---------------------------------------------------------------------------
# Plan resolution
# ---------------------------------------------------------------------------


def resolve_plan_id(plan_id: str | None) -> str:
    """Single-plan auto-detect, else explicit. Mirrors cli._resolve_plan_id."""
    if plan_id:
        return plan_id
    try:
        found = _plans.find_single_plan()
    except _plans.MultiplePlansError as e:
        raise ImportError(str(e)) from e
    if found is None:
        raise ImportError(
            "No plan under plans/. Pass --plan-id explicitly."
        )
    return found[0]


def locate_week_file(plan_id: str, week_id: str) -> Path:
    path = _plans.week_file(plan_id, week_id)
    if not path.is_file():
        raise ImportError(
            f"Week markdown not found: plans/{plan_id}/weeks/{week_id}.md\n"
            f"Run /plan-training-week or check the plan/week ids."
        )
    return path


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _extract_blocks(text: str) -> list[tuple[int, str]]:
    """Walk lines, yield (start_line, body) for each tempo:session block."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        if _FENCE_OPEN_RE.match(lines[i]):
            start = i + 1  # 1-indexed for human error messages
            body_lines: list[str] = []
            i += 1
            while i < n and not _FENCE_CLOSE_RE.match(lines[i]):
                body_lines.append(lines[i])
                i += 1
            if i >= n:
                raise ImportError(
                    f"Unterminated ```yaml tempo:session block opened at line {start}."
                )
            blocks.append((start, "\n".join(body_lines)))
        i += 1
    return blocks


_REQUIRED_FIELDS = ("id_slug", "date", "sport")
# Schema: type-coerced through yaml.safe_load; numbers may be int or float.
_NUMERIC_FIELDS = ("target_tss", "target_duration_s")


def _validate_block(raw: dict, *, source_line: int) -> ParsedSession:
    missing = [k for k in _REQUIRED_FIELDS if not raw.get(k)]
    if missing:
        raise ImportError(
            f"Session block at line {source_line}: missing required field(s): "
            f"{', '.join(missing)}. Required: {', '.join(_REQUIRED_FIELDS)}."
        )

    extra = set(raw) - {
        "id_slug",
        "date",
        "sport",
        "library_ref",
        "target_tss",
        "target_duration_s",
        "purpose",
        "notes",
    }
    if extra:
        raise ImportError(
            f"Session block at line {source_line}: unknown field(s): "
            f"{', '.join(sorted(extra))}."
        )

    target_tss = raw.get("target_tss")
    if target_tss is not None:
        try:
            target_tss = float(target_tss)
        except (TypeError, ValueError) as e:
            raise ImportError(
                f"Session block at line {source_line}: target_tss must be numeric."
            ) from e

    target_duration_s = raw.get("target_duration_s")
    if target_duration_s is not None:
        try:
            target_duration_s = int(target_duration_s)
        except (TypeError, ValueError) as e:
            raise ImportError(
                f"Session block at line {source_line}: target_duration_s must be integer."
            ) from e

    return ParsedSession(
        id_slug=str(raw["id_slug"]).strip(),
        date=str(raw["date"]).strip(),
        sport=str(raw["sport"]).strip(),
        library_ref=_opt_str(raw.get("library_ref")),
        target_tss=target_tss,
        target_duration_s=target_duration_s,
        purpose=_opt_str(raw.get("purpose")),
        notes=_opt_str(raw.get("notes")),
        source_line=source_line,
    )


def _opt_str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_week_markdown(text: str) -> list[ParsedSession]:
    """Extract every tempo:session YAML block from a week's markdown.

    Raises ``ImportError`` with line numbers on malformed YAML or
    schema violations. If zero blocks are found, raises with an
    explanation pointing at the option-C format.
    """
    blocks = _extract_blocks(text)
    if not blocks:
        raise ImportError(
            "No ```yaml tempo:session blocks found in the week markdown.\n"
            "Each session needs a fenced YAML block (see plan-training-week "
            "SKILL.md step 6). Fields: id_slug, date, sport, [library_ref, "
            "target_tss, target_duration_s, purpose, notes]."
        )

    parsed: list[ParsedSession] = []
    seen_slugs: dict[str, int] = {}
    for start_line, body in blocks:
        try:
            raw = yaml.safe_load(body)
        except yaml.YAMLError as e:
            raise ImportError(
                f"Session block at line {start_line}: YAML parse error: {e}"
            ) from e
        if not isinstance(raw, dict):
            raise ImportError(
                f"Session block at line {start_line}: expected a mapping, "
                f"got {type(raw).__name__}."
            )
        session = _validate_block(raw, source_line=start_line)
        if session.id_slug in seen_slugs:
            raise ImportError(
                f"Duplicate id_slug {session.id_slug!r} at line {start_line} "
                f"(first seen at line {seen_slugs[session.id_slug]})."
            )
        seen_slugs[session.id_slug] = start_line
        parsed.append(session)
    return parsed


# ---------------------------------------------------------------------------
# Diff + UPSERT
# ---------------------------------------------------------------------------


def _fetch_existing(
    conn: sqlite3.Connection, *, session_id: str
) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(_CONTENT_COLS)} FROM sessions_planned WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _diff_fields(parsed: ParsedSession, *, plan_id: str, week_id: str, existing: dict) -> tuple[str, ...]:
    incoming = {
        "plan_id": plan_id,
        "week_id": week_id,
        "date": parsed.date,
        "sport": parsed.sport,
        "library_ref": parsed.library_ref,
        "target_tss": parsed.target_tss,
        "target_duration_s": parsed.target_duration_s,
        "purpose": parsed.purpose,
        "notes": parsed.notes,
    }
    diffs: list[str] = []
    for col, val in incoming.items():
        prev = existing.get(col)
        if not _values_equal(col, prev, val):
            diffs.append(col)
    return tuple(diffs)


def _values_equal(col: str, a: object, b: object) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if col in _NUMERIC_FIELDS:
        try:
            return float(a) == float(b)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return a == b
    return a == b


def plan_changes(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    week_id: str,
    parsed: list[ParsedSession],
) -> list[SessionChange]:
    """Compute the insert/update/noop classification per parsed session."""
    changes: list[SessionChange] = []
    for s in parsed:
        sid = s.session_id(plan_id=plan_id, week_id=week_id)
        existing = _fetch_existing(conn, session_id=sid)
        if existing is None:
            action = "insert"
            diff_fields: tuple[str, ...] = tuple(_CONTENT_COLS)
        else:
            diff_fields = _diff_fields(s, plan_id=plan_id, week_id=week_id, existing=existing)
            action = "noop" if not diff_fields else "update"
        changes.append(
            SessionChange(
                id_slug=s.id_slug,
                session_id=sid,
                date=s.date,
                sport=s.sport,
                library_ref=s.library_ref,
                target_tss=s.target_tss,
                target_duration_s=s.target_duration_s,
                purpose=s.purpose,
                notes=s.notes,
                action=action,
                diff_fields=diff_fields,
            )
        )
    return changes


def apply_changes(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    week_id: str,
    changes: list[SessionChange],
) -> None:
    """Apply non-noop changes via UPSERT — push-week columns untouched."""
    pending = [c for c in changes if c.action != "noop"]
    if not pending:
        return
    with conn:
        for c in pending:
            conn.execute(
                """
                INSERT INTO sessions_planned(
                    id, plan_id, week_id, date, sport, library_ref,
                    target_tss, target_duration_s, purpose, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    plan_id = excluded.plan_id,
                    week_id = excluded.week_id,
                    date = excluded.date,
                    sport = excluded.sport,
                    library_ref = excluded.library_ref,
                    target_tss = excluded.target_tss,
                    target_duration_s = excluded.target_duration_s,
                    purpose = excluded.purpose,
                    notes = excluded.notes
                """,
                (
                    c.session_id,
                    plan_id,
                    week_id,
                    c.date,
                    c.sport,
                    c.library_ref,
                    c.target_tss,
                    c.target_duration_s,
                    c.purpose,
                    c.notes,
                ),
            )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def import_week(
    conn: sqlite3.Connection,
    *,
    week_id: str,
    plan_id: str | None = None,
    dry_run: bool = False,
) -> ImportResult:
    """End-to-end: resolve plan, parse markdown, diff, optionally apply."""
    pid = resolve_plan_id(plan_id)
    path = locate_week_file(pid, week_id)
    text = path.read_text(encoding="utf-8")
    parsed = parse_week_markdown(text)
    changes = plan_changes(conn, plan_id=pid, week_id=week_id, parsed=parsed)
    if not dry_run:
        apply_changes(conn, plan_id=pid, week_id=week_id, changes=changes)
    return ImportResult(
        plan_id=pid,
        week_id=week_id,
        week_file=path,
        changes=changes,
        dry_run=dry_run,
    )


def render_change_rows(result: ImportResult) -> list[tuple[str, ...]]:
    """Plain-data rows for the Rich summary table."""
    badge = {"insert": "+", "update": "~", "noop": "="}
    rows: list[tuple[str, ...]] = []
    for c in result.changes:
        dur = f"{c.target_duration_s // 60} min" if c.target_duration_s else "—"
        tss = str(int(c.target_tss)) if c.target_tss is not None else "—"
        rows.append(
            (
                badge.get(c.action, "?"),
                c.date,
                c.sport,
                c.library_ref or "—",
                tss,
                dur,
                ",".join(c.diff_fields) if c.action == "update" else c.action,
            )
        )
    return rows


__all__ = [
    "ImportError",
    "ImportResult",
    "ParsedSession",
    "SessionChange",
    "apply_changes",
    "import_week",
    "locate_week_file",
    "parse_week_markdown",
    "plan_changes",
    "render_change_rows",
    "resolve_plan_id",
]
