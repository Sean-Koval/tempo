"""``search_memory`` + ``log_decision`` — the memory read/write surface.

Read path wraps ``tempo.embed.search_memory``. Write path inserts into the
decisions table and synchronously appends to ``memory.lance`` via
``tempo.embed.embed_single_decision`` so just-logged decisions are
searchable in the same session.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from tempo.embed import Embedder, embed_single_decision, search_memory

from .models import DecisionLogged, MemoryHit

_embedder_override: Embedder | None = None


def set_embedder_override(embedder: Embedder | None) -> None:
    """Test hook — inject a deterministic embedder. Pass None to reset."""
    global _embedder_override
    _embedder_override = embedder


def search_memory_hits(
    query: str,
    *,
    k: int = 5,
    since: str | None = None,
    scope: str | None = None,
    kind: str | None = None,
    vectors_dir: Path | None = None,
) -> list[MemoryHit]:
    raw = search_memory(
        query,
        k=k,
        since=since,
        scope=scope,
        kind=kind,
        vectors_dir=vectors_dir,
        embedder=_embedder_override,
    )
    return [
        MemoryHit(
            id=h.id,
            text=h.text,
            source=h.source,
            scope=h.scope,
            kind=h.kind,
            timestamp=h.timestamp,
            file_path=h.file_path,
            score=h.score,
        )
        for h in raw
    ]


def log_decision(
    conn: sqlite3.Connection,
    *,
    scope: str,
    kind: str,
    rationale: str,
    changed_files: list[str] | None = None,
    vectors_dir: Path | None = None,
    now_iso: str | None = None,
) -> DecisionLogged:
    """Write a decisions row and embed its rationale into memory.lance.

    Returns ``DecisionLogged`` with the new row id + whether embedding
    succeeded. Embedding failure is non-fatal: the SQL row lands regardless.
    """
    ts = now_iso or datetime.now().isoformat(timespec="seconds")
    cf_json = json.dumps(changed_files or [])
    cur = conn.execute(
        "INSERT INTO decisions (timestamp, scope, kind, rationale, changed_files) "
        "VALUES (?, ?, ?, ?, ?)",
        (ts, scope, kind, rationale, cf_json),
    )
    decision_id = int(cur.lastrowid or 0)

    embedded = False
    try:
        embed_single_decision(
            decision_id=decision_id,
            scope=scope,
            kind=kind,
            rationale=rationale,
            timestamp=ts,
            vectors_dir=vectors_dir,
            embedder=_embedder_override,
        )
        embedded = True
    except Exception:
        # Non-fatal — memory can be rebuilt via `coach vectors rebuild-memory`.
        embedded = False
    return DecisionLogged(id=decision_id, embedded=embedded, timestamp=ts)
