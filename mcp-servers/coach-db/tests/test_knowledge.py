"""Tests for coach_db_mcp.knowledge.search_knowledge.

Seeds a fresh knowledge.lance with the same hashed-BoW fake embedder the
root tempo suite uses, then queries through the MCP tool wrapper.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from tempo import embed

from coach_db_mcp import knowledge
from coach_db_mcp.models import Snippet


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


def _write_doc(path: Path, body: str, frontmatter: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_sources(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "sources:",
                "  - id: friel",
                "    name: Friel",
                "    type: book",
                "    credibility: expert_practitioner",
                "    topics: [periodization]",
                "  - id: anon-blog",
                "    name: Anonymous Blog",
                "    type: blog",
                "    credibility: experiential",
                "    topics: [nutrition]",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture
def seeded_vectors(tmp_path: Path) -> Path:
    kroot = tmp_path / "knowledge"
    kroot.mkdir()
    _write_sources(kroot / "sources.yaml")
    _write_doc(
        kroot / "methodology" / "periodization.md",
        "Periodization is the systematic planning of training "
        "phases: base, build, peak, taper. Friel teaches weekly "
        "volume progression with recovery weeks every fourth week.",
        {"topic": "periodization", "sources": ["friel"]},
    )
    _write_doc(
        kroot / "nutrition" / "carbs.md",
        "Race-day carb intake should target 60-90 grams per hour "
        "with a gut-trained athlete tolerating the higher end. "
        "Pre-race carb-loading starts three days out.",
        {"topic": "nutrition", "sources": ["anon-blog"]},
    )
    vdir = tmp_path / "vectors"
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=_fake_embedder())
    return vdir


def test_search_knowledge_returns_snippet_models(seeded_vectors: Path) -> None:
    knowledge.set_embedder_override(_fake_embedder())
    try:
        hits = knowledge.search_knowledge(
            "periodization phases and recovery weeks",
            k=3,
            vectors_dir=seeded_vectors,
        )
    finally:
        knowledge.set_embedder_override(None)

    assert hits, "expected at least one hit"
    assert all(isinstance(h, Snippet) for h in hits)
    assert hits[0].path.endswith("periodization.md")
    assert hits[0].credibility == "expert_practitioner"
    assert "friel" in hits[0].source_ids


def test_search_knowledge_credibility_filter(seeded_vectors: Path) -> None:
    knowledge.set_embedder_override(_fake_embedder())
    try:
        hits = knowledge.search_knowledge(
            "race-day carb intake grams per hour",
            k=5,
            credibility_min="expert_practitioner",
            vectors_dir=seeded_vectors,
        )
    finally:
        knowledge.set_embedder_override(None)

    # nutrition doc is experiential → dropped. Only the expert_practitioner
    # doc (or nothing) should come back.
    assert all(h.credibility != "experiential" for h in hits)


def test_search_knowledge_empty_index(tmp_path: Path) -> None:
    # Untouched vectors dir → no hits, no crash.
    hits = knowledge.search_knowledge("anything", vectors_dir=tmp_path / "empty")
    assert hits == []
