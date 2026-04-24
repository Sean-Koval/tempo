"""Tests for coach_db_mcp.memory — search_memory_hits + log_decision."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from pathlib import Path

import pytest
from tempo import embed
from tempo.db import init_schema

from coach_db_mcp import memory


def _fake_embedder() -> embed.Embedder:
    def embed_fn(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * 384
            for word in t.lower().split():
                h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
                for i in range(8):
                    slot = (h >> (i * 8)) & 0xFFFF
                    vec[slot % 384] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    return embed_fn


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def test_log_decision_round_trip(tmp_path: Path, conn: sqlite3.Connection) -> None:
    vdir = tmp_path / "vectors"
    memory.set_embedder_override(_fake_embedder())
    try:
        result = memory.log_decision(
            conn,
            scope="week:2026-W18",
            kind="adjust",
            rationale="Cut Wednesday tempo bike — illness onset yesterday.",
            changed_files=["plans/ironman-lp/weeks/2026-W18.md"],
            vectors_dir=vdir,
        )
        assert result.id > 0
        assert result.embedded is True
        assert result.timestamp

        # SQL row lands.
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (result.id,)).fetchone()
        assert row["scope"] == "week:2026-W18"
        assert row["kind"] == "adjust"
        assert "illness onset" in row["rationale"]

        # Searchable in the same session.
        hits = memory.search_memory_hits(
            "cut tempo bike illness",
            k=3,
            vectors_dir=vdir,
        )
    finally:
        memory.set_embedder_override(None)

    assert hits
    assert hits[0].id == f"decision:{result.id}"
    assert hits[0].scope == "week:2026-W18"
    assert hits[0].kind == "adjust"


def test_search_memory_scope_prefix(tmp_path: Path, conn: sqlite3.Connection) -> None:
    vdir = tmp_path / "vectors"
    memory.set_embedder_override(_fake_embedder())
    try:
        memory.log_decision(
            conn,
            scope="week:2026-W18",
            kind="adjust",
            rationale="week-scoped decision about volume",
            vectors_dir=vdir,
        )
        memory.log_decision(
            conn,
            scope="plan:ironman-lp",
            kind="plan",
            rationale="plan-scoped decision about peak phase",
            vectors_dir=vdir,
        )

        plan_hits = memory.search_memory_hits(
            "decision about peak or volume",
            k=5,
            scope="plan:",
            vectors_dir=vdir,
        )
        week_hits = memory.search_memory_hits(
            "decision about peak or volume",
            k=5,
            scope="week:",
            vectors_dir=vdir,
        )
    finally:
        memory.set_embedder_override(None)

    assert all(h.scope.startswith("plan:") for h in plan_hits)
    assert all(h.scope.startswith("week:") for h in week_hits)


def test_log_decision_stores_changed_files_as_json(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    memory.set_embedder_override(_fake_embedder())
    try:
        result = memory.log_decision(
            conn,
            scope="session:abc",
            kind="observation",
            rationale="HR decoupling was 7% — flag Pw:HR drift.",
            changed_files=["athlete/injury-log.md", "plans/ironman-lp/changelog.md"],
            vectors_dir=tmp_path / "vectors",
        )
    finally:
        memory.set_embedder_override(None)

    row = conn.execute(
        "SELECT changed_files FROM decisions WHERE id = ?", (result.id,)
    ).fetchone()
    import json as _json
    files = _json.loads(row["changed_files"])
    assert "athlete/injury-log.md" in files
