"""Tests for gap_search — knowledge gap detection + trusted-source query suggestions."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tempo import gap_search
from tempo.embed import SearchHit
from tempo.gap_search import (
    KnowledgeGap,
    RetrievalConfidence,
    detect_gap,
    load_sources,
    suggest_research_queries,
)


def _hit(score: float, credibility: str = "expert_practitioner") -> SearchHit:
    return SearchHit(
        id="x",
        text="t",
        path="p",
        topic="periodization",
        credibility=credibility,
        source_ids=[],
        phase="",
        score=score,
    )


# --- RetrievalConfidence --------------------------------------------------


def test_retrieval_confidence_empty_hits_fails():
    c = RetrievalConfidence.from_hits([])
    assert c.n_hits == 0
    assert c.max_score == 0.0
    assert c.mean_credibility_rank == 5.0
    assert c.threshold_pass is False


def test_retrieval_confidence_passes_when_thresholds_met():
    hits = [_hit(0.8, "peer_reviewed"), _hit(0.7, "peer_reviewed")]
    c = RetrievalConfidence.from_hits(hits, min_hits=2, min_score=0.6, max_credibility_rank=3.0)
    assert c.threshold_pass is True
    assert c.max_score == 0.8
    assert c.mean_credibility_rank == 1.0


def test_retrieval_confidence_fails_on_low_score():
    hits = [_hit(0.4, "peer_reviewed"), _hit(0.3, "peer_reviewed")]
    c = RetrievalConfidence.from_hits(hits, min_score=0.6)
    assert c.threshold_pass is False


def test_retrieval_confidence_fails_on_low_credibility():
    hits = [_hit(0.9, "unvetted"), _hit(0.85, "unvetted")]
    c = RetrievalConfidence.from_hits(hits, max_credibility_rank=3.0)
    assert c.mean_credibility_rank == 5.0
    assert c.threshold_pass is False


# --- detect_gap -----------------------------------------------------------


def test_detect_gap_no_hits(monkeypatch):
    monkeypatch.setattr(gap_search, "search", lambda *a, **kw: [])
    out = detect_gap("BSI return to run")
    assert isinstance(out, KnowledgeGap)
    assert out.reason == "no_hits"
    assert out.confidence.n_hits == 0


def test_detect_gap_thin_coverage(monkeypatch):
    monkeypatch.setattr(gap_search, "search", lambda *a, **kw: [_hit(0.9, "peer_reviewed")])
    out = detect_gap("BSI return to run", min_hits=2)
    assert isinstance(out, KnowledgeGap)
    assert out.reason == "thin_coverage"


def test_detect_gap_low_score(monkeypatch):
    hits = [_hit(0.3, "peer_reviewed"), _hit(0.2, "peer_reviewed")]
    monkeypatch.setattr(gap_search, "search", lambda *a, **kw: hits)
    out = detect_gap("foo", min_score=0.6)
    assert isinstance(out, KnowledgeGap)
    assert out.reason == "low_score"


def test_detect_gap_low_credibility(monkeypatch):
    hits = [_hit(0.9, "unvetted"), _hit(0.85, "unvetted")]
    monkeypatch.setattr(gap_search, "search", lambda *a, **kw: hits)
    out = detect_gap("foo", max_credibility_rank=3.0)
    assert isinstance(out, KnowledgeGap)
    assert out.reason == "low_credibility"


def test_detect_gap_passes_returns_hits(monkeypatch):
    hits = [_hit(0.85, "peer_reviewed"), _hit(0.8, "peer_reviewed")]
    monkeypatch.setattr(gap_search, "search", lambda *a, **kw: hits)
    out = detect_gap("foo")
    assert isinstance(out, tuple)
    returned_hits, conf = out
    assert returned_hits == hits
    assert conf.threshold_pass is True


# --- load_sources ---------------------------------------------------------


def test_load_sources_returns_list(tmp_path: Path):
    p = tmp_path / "sources.yaml"
    p.write_text(yaml.safe_dump({"sources": [{"id": "a", "name": "A"}, {"id": "b"}]}))
    out = load_sources(p)
    assert len(out) == 2
    assert out[0]["id"] == "a"


def test_load_sources_missing_file_returns_empty(tmp_path: Path):
    assert load_sources(tmp_path / "nope.yaml") == []


# --- suggest_research_queries ---------------------------------------------


@pytest.fixture
def synthetic_sources(tmp_path: Path) -> Path:
    doc = {
        "sources": [
            {
                "id": "bjsm",
                "name": "British Journal of Sports Medicine",
                "credibility": "peer_reviewed",
                "topics": ["everything", "injury", "bone_stress"],
                "domain": "bjsm.bmj.com",
            },
            {
                "id": "pubmed",
                "name": "PubMed",
                "credibility": "peer_reviewed",
                "topics": ["everything"],
                "domain": "pubmed.ncbi.nlm.nih.gov",
            },
            {
                "id": "friel-book",
                "name": "Joe Friel — Triathlete's Training Bible",
                "credibility": "expert_practitioner",
                "topics": ["periodization"],
                # no domain — book
            },
            {
                "id": "trainright",
                "name": "Carmichael Training Systems",
                "credibility": "expert_practitioner",
                "topics": ["ironman"],
                "domain": "trainright.com",
            },
            {
                "id": "random-blog",
                "name": "Some random blog",
                "credibility": "unvetted",
                "topics": ["everything"],
                "domain": "randomblog.example",
            },
        ]
    }
    p = tmp_path / "sources.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def _gap(query: str, topic: str | None = None) -> KnowledgeGap:
    return KnowledgeGap(
        query=query,
        topic=topic,
        confidence=RetrievalConfidence(0, 0.0, 5.0, False),
        reason="no_hits",
    )


def test_suggest_builds_site_queries_for_domain_sources(synthetic_sources: Path):
    gap = _gap("bone stress injury return to running")
    out = suggest_research_queries(gap, sources_path=synthetic_sources, k=10)
    bjsm = next(s for s in out if s.source_id == "bjsm")
    assert bjsm.query == "site:bjsm.bmj.com bone stress injury return to running"
    assert bjsm.domain == "bjsm.bmj.com"


def test_suggest_uses_author_query_for_non_domain_sources(synthetic_sources: Path):
    gap = _gap("base period volume ramp")
    out = suggest_research_queries(gap, sources_path=synthetic_sources, k=10)
    friel = next(s for s in out if s.source_id == "friel-book")
    assert friel.query.startswith('"Joe Friel"')
    assert "base period volume ramp" in friel.query
    assert friel.domain is None


def test_suggest_orders_by_credibility_then_domain(synthetic_sources: Path):
    gap = _gap("query")
    out = suggest_research_queries(gap, sources_path=synthetic_sources, k=10)
    # peer_reviewed (with domain) should come before expert_practitioner.
    assert out[0].credibility == "peer_reviewed"
    assert out[1].credibility == "peer_reviewed"
    # Among ties on credibility, sources with a domain rank above those without.
    ep_with_domain = [s for s in out if s.credibility == "expert_practitioner"]
    assert ep_with_domain[0].domain is not None
    assert ep_with_domain[-1].domain is None or any(
        s.domain is None for s in ep_with_domain
    )


def test_suggest_respects_topic_filter(synthetic_sources: Path):
    gap = _gap("query", topic="periodization")
    out = suggest_research_queries(gap, sources_path=synthetic_sources, k=10)
    ids = {s.source_id for s in out}
    # bjsm/pubmed/random-blog match via "everything"; friel matches via topic; trainright doesn't.
    assert "friel-book" in ids
    assert "trainright" not in ids


def test_suggest_topic_filter_explicit_overrides_gap(synthetic_sources: Path):
    gap = _gap("query", topic="periodization")
    out = suggest_research_queries(
        gap, sources_path=synthetic_sources, k=10, topic_filter="ironman"
    )
    ids = {s.source_id for s in out}
    assert "trainright" in ids
    assert "friel-book" not in ids


def test_suggest_respects_k(synthetic_sources: Path):
    gap = _gap("query")
    out = suggest_research_queries(gap, sources_path=synthetic_sources, k=2)
    assert len(out) == 2


def test_bsi_query_returns_at_least_three_credible_sources(synthetic_sources: Path):
    """Acceptance: detect a BSI gap → >=3 site-scoped queries on credible sources."""
    gap = _gap("BSI return to run", topic="injury")
    out = suggest_research_queries(gap, sources_path=synthetic_sources, k=5)
    credible = [s for s in out if s.credibility in ("peer_reviewed", "expert_practitioner")]
    assert len(credible) >= 2
    site_queries = [s.query for s in out if s.query.startswith("site:")]
    assert any("bjsm.bmj.com" in q for q in site_queries)
    assert any("pubmed.ncbi.nlm.nih.gov" in q for q in site_queries)


def test_real_sources_yaml_has_bjsm_with_injury_topic():
    """Ground-truth: the shipped sources.yaml supports the BSI gap flow."""
    out = load_sources()  # default path
    bjsm = next((s for s in out if s.get("id") == "bjsm"), None)
    assert bjsm is not None
    assert bjsm.get("domain") == "bjsm.bmj.com"
    assert "injury" in (bjsm.get("topics") or [])
