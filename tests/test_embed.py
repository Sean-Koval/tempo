"""Tests for ``tempo.embed`` — the knowledge-tree vector indexer.

Uses a deterministic fake embedder so the suite is fast, offline, and
doesn't drag in fastembed's model download. The fake hashes text into
a 384-dim unit vector — good enough that semantically different chunks
land in different regions of vector space.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from tempo import embed


def _fake_embedder() -> embed.Embedder:
    """Deterministic 384-dim unit vectors via hashed bag-of-words.

    Each lowercased token lights up a few fixed vector slots. Texts
    sharing vocabulary end up with higher cosine similarity, which is
    enough for the test suite to assert ordering without touching a
    real embedding model.
    """

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


def _write_doc(path: Path, body: str, frontmatter: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if frontmatter is not None:
        lines.append("---")
        for k, v in frontmatter.items():
            if isinstance(v, list):
                lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_sources(path: Path, registry: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["sources:"]
    for sid, cred in registry.items():
        lines.extend(
            [
                f"  - id: {sid}",
                f"    name: {sid}",
                "    type: blog",
                f"    credibility: {cred}",
                "    topics: [test]",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def kroot(tmp_path: Path) -> Path:
    k = tmp_path / "knowledge"
    k.mkdir()
    _write_sources(
        k / "sources.yaml",
        {
            "src-peer": "peer_reviewed",
            "src-expert": "expert_practitioner",
            "src-journo": "evidence_based_journalism",
        },
    )
    _write_doc(
        k / "methodology" / "alpha.md",
        "Polarized training lives at Z1 and Z5 with little in between. " * 40,
        {"topic": "polarized", "sources": ["src-peer", "src-expert"]},
    )
    _write_doc(
        k / "methodology" / "beta.md",
        "Threshold intervals at LT2 build race-specific durability. " * 40,
        {"topic": "threshold", "sources": ["src-expert"]},
    )
    _write_doc(
        k / "nutrition" / "gamma.md",
        "Race-day carbs target 90g/hour using glucose plus fructose. " * 40,
        {"topic": "race_day", "sources": ["src-journo"]},
    )
    return k


def test_rebuild_writes_expected_chunks(kroot: Path, tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    stats = embed.rebuild(
        knowledge_root=kroot,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    assert stats.files_scanned == 3
    assert stats.files_embedded == 3
    assert stats.files_skipped == 0
    assert stats.chunks_written >= 3


def test_rebuild_is_idempotent(kroot: Path, tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    emb = _fake_embedder()
    first = embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)
    second = embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)
    assert second.files_scanned == first.files_scanned
    assert second.files_embedded == 0
    assert second.files_skipped == first.files_scanned
    assert second.chunks_written == 0


def test_modified_file_replaces_only_its_rows(kroot: Path, tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    emb = _fake_embedder()
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)

    alpha = kroot / "methodology" / "alpha.md"
    _write_doc(
        alpha,
        "Entirely new body. " * 60,
        {"topic": "polarized", "sources": ["src-peer", "src-expert"]},
    )

    stats = embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)
    assert stats.files_embedded == 1
    assert stats.files_skipped == 2
    # The old alpha chunks should have been deleted before new ones were written.
    assert stats.chunks_deleted >= 1
    assert stats.paths_indexed == ["knowledge/methodology/alpha.md"]


def test_unknown_source_is_unvetted(tmp_path: Path, tmp_data_dir: Path) -> None:
    kroot = tmp_path / "knowledge"
    kroot.mkdir()
    _write_sources(kroot / "sources.yaml", {"known": "peer_reviewed"})
    _write_doc(
        kroot / "methodology" / "doc.md",
        "Body. " * 50,
        {"topic": "x", "sources": ["known", "not-in-registry"]},
    )
    vdir = tmp_data_dir / "vectors"
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=_fake_embedder())

    hits = embed.search(
        "Body",
        k=5,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    assert hits
    assert all(h.credibility == "unvetted" for h in hits)


def test_doc_without_sources_is_unvetted(tmp_path: Path, tmp_data_dir: Path) -> None:
    kroot = tmp_path / "knowledge"
    kroot.mkdir()
    _write_sources(kroot / "sources.yaml", {"known": "peer_reviewed"})
    _write_doc(kroot / "solo.md", "Body. " * 50, {"topic": "x"})
    vdir = tmp_data_dir / "vectors"
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=_fake_embedder())

    hits = embed.search("Body", k=3, vectors_dir=vdir, embedder=_fake_embedder())
    assert hits
    assert hits[0].credibility == "unvetted"


def test_worst_credibility_wins(tmp_path: Path, tmp_data_dir: Path) -> None:
    kroot = tmp_path / "knowledge"
    kroot.mkdir()
    _write_sources(
        kroot / "sources.yaml",
        {"peer": "peer_reviewed", "journo": "evidence_based_journalism"},
    )
    _write_doc(
        kroot / "mix.md",
        "Mixed-credibility content. " * 40,
        {"topic": "x", "sources": ["peer", "journo"]},
    )
    vdir = tmp_data_dir / "vectors"
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=_fake_embedder())

    hits = embed.search("Mixed", k=2, vectors_dir=vdir, embedder=_fake_embedder())
    assert hits
    # Worst of {peer_reviewed, evidence_based_journalism} → journalism.
    assert hits[0].credibility == "evidence_based_journalism"


def test_search_returns_relevant_doc_first(kroot: Path, tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    emb = _fake_embedder()
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)

    # Query vocabulary overlaps uniquely with alpha.md's body.
    hits = embed.search(
        "polarized Z1 Z5 training little in between",
        k=3,
        vectors_dir=vdir,
        embedder=emb,
    )
    assert hits
    assert hits[0].path == "knowledge/methodology/alpha.md"


def test_topic_filter_excludes_off_topic(kroot: Path, tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    emb = _fake_embedder()
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)

    hits = embed.search("anything", k=5, topic="race_day", vectors_dir=vdir, embedder=emb)
    assert hits
    assert all(h.topic == "race_day" for h in hits)
    assert all(h.path == "knowledge/nutrition/gamma.md" for h in hits)


def test_credibility_min_filter_drops_weaker(tmp_path: Path, tmp_data_dir: Path) -> None:
    kroot = tmp_path / "knowledge"
    kroot.mkdir()
    _write_sources(
        kroot / "sources.yaml",
        {"peer": "peer_reviewed", "journo": "evidence_based_journalism"},
    )
    _write_doc(
        kroot / "strong.md",
        "Peer-reviewed content. " * 40,
        {"topic": "a", "sources": ["peer"]},
    )
    _write_doc(
        kroot / "weak.md",
        "Journalism content. " * 40,
        {"topic": "b", "sources": ["journo"]},
    )
    vdir = tmp_data_dir / "vectors"
    emb = _fake_embedder()
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)

    hits = embed.search(
        "anything",
        k=5,
        credibility_min="expert_practitioner",
        vectors_dir=vdir,
        embedder=emb,
    )
    assert hits
    assert all(h.credibility == "peer_reviewed" for h in hits)


def test_search_on_empty_index_returns_empty(tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    vdir.mkdir()
    assert embed.search("query", vectors_dir=vdir, embedder=_fake_embedder()) == []


def test_rebuild_with_paths_only_embeds_given_files(kroot: Path, tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    emb = _fake_embedder()
    target = kroot / "methodology" / "alpha.md"
    stats = embed.rebuild(
        knowledge_root=kroot, vectors_dir=vdir, paths=[target], embedder=emb
    )
    assert stats.files_scanned == 1
    assert stats.files_embedded == 1
    assert stats.paths_indexed == ["knowledge/methodology/alpha.md"]


def test_force_reembeds_despite_matching_hash(kroot: Path, tmp_data_dir: Path) -> None:
    vdir = tmp_data_dir / "vectors"
    emb = _fake_embedder()
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb)
    stats = embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=emb, force=True)
    assert stats.files_skipped == 0
    assert stats.files_embedded == 3
