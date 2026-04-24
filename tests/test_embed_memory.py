"""Tests for ``tempo.embed.rebuild_memory`` + ``search_memory``.

Uses the same deterministic hashed-BoW fake embedder as test_embed.py so the
suite stays offline. Seeds a temp repo layout (journal/, plans/, coach.db)
and exercises idempotency, entry replacement, and search filters.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from pathlib import Path

import pytest

from tempo import embed
from tempo.db import init_schema


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
def fake_world(tmp_path: Path) -> dict:
    journal_root = tmp_path / "journal"
    plans_root = tmp_path / "plans"
    vectors_dir = tmp_path / "vectors"
    journal_root.mkdir()
    plans_root.mkdir()

    (journal_root / "2026-04-20.md").write_text(
        "Morning: felt fresh, HRV 72. Ran easy 8km.\n\nEvening: dinner then rest.",
        encoding="utf-8",
    )
    (plans_root / "ironman-lp").mkdir()
    (plans_root / "ironman-lp" / "changelog.md").write_text(
        "## 2026-04-15 base→build transition\n"
        "Bumped weekly TSS target after two clean adherence weeks.\n\n"
        "## 2026-04-22 volume cut\n"
        "Cut long ride by 45min due to rising RHR trend.",
        encoding="utf-8",
    )

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute(
        "INSERT INTO decisions (timestamp, scope, kind, rationale) VALUES (?, ?, ?, ?)",
        ("2026-04-15T08:00:00", "week:2026-W16", "adjust",
         "Backed off Tuesday threshold run because HRV dropped two standard deviations."),
    )
    return {
        "journal_root": journal_root,
        "plans_root": plans_root,
        "vectors_dir": vectors_dir,
        "conn": conn,
    }


def _rebuild(world: dict, **kwargs) -> embed.MemoryEmbedStats:
    return embed.rebuild_memory(
        journal_root=world["journal_root"],
        plans_root=world["plans_root"],
        decisions_conn=world["conn"],
        vectors_dir=world["vectors_dir"],
        embedder=_fake_embedder(),
        **kwargs,
    )


def test_rebuild_memory_indexes_all_sources(fake_world: dict) -> None:
    stats = _rebuild(fake_world)
    # 1 decision + 1 journal file + 1 changelog file (with 2 sections) = 3 keys
    assert stats.sources_scanned == 3
    assert stats.sources_embedded == 3
    # Rows: 1 decision + 1 journal chunk + 2 changelog sections = 4
    assert stats.rows_written == 4


def test_rebuild_memory_is_idempotent(fake_world: dict) -> None:
    _rebuild(fake_world)
    stats = _rebuild(fake_world)
    assert stats.sources_skipped == 3
    assert stats.rows_written == 0


def test_modified_journal_replaces_only_its_rows(fake_world: dict) -> None:
    _rebuild(fake_world)
    # Change the journal, leave decisions + changelog alone.
    (fake_world["journal_root"] / "2026-04-20.md").write_text(
        "Afternoon update: brick run went well, paced on HR not pace.",
        encoding="utf-8",
    )
    stats = _rebuild(fake_world)
    assert stats.sources_embedded == 1
    assert stats.sources_skipped == 2
    assert stats.rows_deleted >= 1


def test_search_memory_finds_decision(fake_world: dict) -> None:
    _rebuild(fake_world)
    hits = embed.search_memory(
        "backed off threshold run due to HRV drop",
        k=3,
        vectors_dir=fake_world["vectors_dir"],
        embedder=_fake_embedder(),
    )
    assert hits
    assert any(h.source == "decision" for h in hits)
    top = hits[0]
    assert top.source == "decision"
    assert top.scope == "week:2026-W16"


def test_search_memory_scope_prefix_filter(fake_world: dict) -> None:
    _rebuild(fake_world)
    hits = embed.search_memory(
        "volume cut because of RHR",
        k=5,
        scope="plan:",
        vectors_dir=fake_world["vectors_dir"],
        embedder=_fake_embedder(),
    )
    assert hits
    assert all(h.scope.startswith("plan:") for h in hits)


def test_search_memory_kind_filter(fake_world: dict) -> None:
    _rebuild(fake_world)
    hits = embed.search_memory(
        "anything",
        k=5,
        kind="adjust",
        vectors_dir=fake_world["vectors_dir"],
        embedder=_fake_embedder(),
    )
    # Only decisions have kind; non-decision rows filtered out.
    assert all(h.kind == "adjust" for h in hits)


def test_embed_single_decision_appends(fake_world: dict) -> None:
    _rebuild(fake_world)
    embed.embed_single_decision(
        decision_id=999,
        scope="week:2026-W18",
        kind="plan",
        rationale="New race added — restructured peak phase.",
        timestamp="2026-05-01T12:00:00",
        vectors_dir=fake_world["vectors_dir"],
        embedder=_fake_embedder(),
    )
    hits = embed.search_memory(
        "new race added restructured peak",
        k=1,
        vectors_dir=fake_world["vectors_dir"],
        embedder=_fake_embedder(),
    )
    assert hits
    assert hits[0].id == "decision:999"


def test_search_memory_empty_index(tmp_path: Path) -> None:
    hits = embed.search_memory(
        "anything",
        vectors_dir=tmp_path / "none",
        embedder=_fake_embedder(),
    )
    assert hits == []
