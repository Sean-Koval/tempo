"""Tests for the closed-loop research-gap pipeline.

Covers:
- ``execute_research_gap`` in gap_search: approve path emits IngestTasks
  with the correct frontmatter extras; cancel path emits nothing.
- ``coach research-gap --execute`` CLI surface emits a JSON brief whose
  shape matches what the /research-gap-fetch slash command reads, and
  that the brief constrains queries to suggest_research_queries output.

The synthetic test scenario is the one from the ticket: "BSI return to
run" → BJSM/PubMed site-scoped suggestions.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from tempo import gap_search
from tempo.cli import app
from tempo.gap_search import (
    FetchCandidate,
    KnowledgeGap,
    RetrievalConfidence,
    WebSearchResult,
    execute_research_gap,
)

runner = CliRunner()


@pytest.fixture
def synthetic_sources_path(tmp_path: Path) -> Path:
    """A trimmed sources.yaml with the credible domains the BSI gap needs."""
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
                "id": "trainright",
                "name": "Carmichael Training Systems",
                "credibility": "expert_practitioner",
                "topics": ["ironman", "injury"],
                "domain": "trainright.com",
            },
        ]
    }
    p = tmp_path / "sources.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def _bsi_gap() -> KnowledgeGap:
    return KnowledgeGap(
        query="BSI return to run",
        topic="injury",
        confidence=RetrievalConfidence(0, 0.0, 5.0, False),
        reason="no_hits",
    )


# --- execute_research_gap: approve path ----------------------------------


def test_execute_emits_tasks_with_frontmatter_extras_on_approve(
    synthetic_sources_path: Path,
):
    """Acceptance: BSI gap → BJSM+PubMed URLs → approve all → IngestTasks
    carry ingest_via=research-gap, gap_query, suggestion, source_id."""
    seen_queries: list[str] = []

    def web_search(query: str):
        seen_queries.append(query)
        # Return a single canned hit per query, derived from the suggestion's
        # site filter so the test asserts site-scoping survives end-to-end.
        if "bjsm.bmj.com" in query:
            return [
                WebSearchResult(
                    url="https://bjsm.bmj.com/content/57/1/42",
                    title="Tibial bone stress injury return-to-run protocol",
                    snippet="Graded loading protocol after BSI...",
                ),
            ]
        if "pubmed.ncbi.nlm.nih.gov" in query:
            return [
                WebSearchResult(
                    url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
                    title="Tibial stress fracture rehab — systematic review",
                    snippet="Outcomes from 24 studies...",
                ),
            ]
        if "trainright.com" in query:
            return [
                WebSearchResult(
                    url="https://trainright.com/return-to-run-after-stress-fracture",
                    title="Coming back from a stress fracture",
                    snippet="Practitioner write-up...",
                ),
            ]
        return []

    def approve(candidates: Sequence[FetchCandidate]) -> Sequence[int]:
        # User approves all surfaced URLs.
        return list(range(len(candidates)))

    out = execute_research_gap(
        _bsi_gap(),
        web_search=web_search,
        approve=approve,
        sources_path=synthetic_sources_path,
        top_k_suggestions=3,
    )

    assert out.approved is True
    assert len(out.tasks) >= 2  # BJSM + PubMed at minimum

    # All web_search invocations used a constrained suggestion query — every
    # call carried a site: filter. Free-form queries would fail this.
    assert seen_queries, "web_search should have been called"
    assert all(q.startswith("site:") for q in seen_queries), seen_queries

    # The exact suggestion queries that were sent must come from
    # suggest_research_queries, not anything we made up here.
    suggestion_queries = {s.query for s in out.suggestions}
    assert set(seen_queries).issubset(suggestion_queries)

    # Frontmatter extras propagate the audit trail.
    bjsm_task = next(t for t in out.tasks if "bjsm.bmj.com" in t.url)
    assert bjsm_task.credibility == "peer_reviewed"
    assert bjsm_task.frontmatter_extras == {
        "ingest_via": "research-gap",
        "gap_query": "BSI return to run",
        "suggestion": "site:bjsm.bmj.com BSI return to run",
        "source_id": "bjsm",
    }


def test_execute_at_least_three_credible_suggestions_for_bsi(
    synthetic_sources_path: Path,
):
    """Ticket acceptance: gap detected for BSI returns 3+ suggestions."""
    out = execute_research_gap(
        _bsi_gap(),
        web_search=lambda q: [],
        approve=lambda c: [],
        sources_path=synthetic_sources_path,
        top_k_suggestions=3,
    )
    assert len(out.suggestions) == 3
    creds = [s.credibility for s in out.suggestions]
    assert creds.count("peer_reviewed") >= 2


# --- execute_research_gap: cancel path -----------------------------------


def test_execute_cancel_emits_no_tasks(synthetic_sources_path: Path):
    """Cancel at the approval gate writes nothing — invariant from the ticket."""

    def web_search(query: str):
        return [
            WebSearchResult(
                url="https://bjsm.bmj.com/content/57/1/42",
                title="BSI return to run",
            )
        ]

    cancel_was_called = {"flag": False}

    def cancel(candidates: Sequence[FetchCandidate]) -> Sequence[int]:
        cancel_was_called["flag"] = True
        return []  # empty selection = cancel

    out = execute_research_gap(
        _bsi_gap(),
        web_search=web_search,
        approve=cancel,
        sources_path=synthetic_sources_path,
        top_k_suggestions=3,
    )

    assert cancel_was_called["flag"] is True
    assert out.approved is False
    assert out.tasks == []
    # Candidates were assembled but never consumed.
    assert len(out.candidates) >= 1


def test_execute_no_candidates_returns_unapproved(synthetic_sources_path: Path):
    """If WebSearch returns nothing for any suggestion, we don't even prompt
    — there's nothing to approve, and tasks stay empty."""
    approve_calls = {"n": 0}

    def approve(candidates: Sequence[FetchCandidate]) -> Sequence[int]:
        approve_calls["n"] += 1
        return list(range(len(candidates)))

    out = execute_research_gap(
        _bsi_gap(),
        web_search=lambda q: [],
        approve=approve,
        sources_path=synthetic_sources_path,
    )
    assert approve_calls["n"] == 0
    assert out.approved is False
    assert out.tasks == []


def test_execute_partial_approval(synthetic_sources_path: Path):
    """User approves a subset of the candidate list — only those become tasks."""

    def web_search(query: str):
        if "bjsm.bmj.com" in query:
            return [WebSearchResult(url="https://bjsm.bmj.com/a", title="BJSM A")]
        if "pubmed.ncbi.nlm.nih.gov" in query:
            return [WebSearchResult(url="https://pubmed.ncbi.nlm.nih.gov/B", title="PubMed B")]
        return []

    out = execute_research_gap(
        _bsi_gap(),
        web_search=web_search,
        approve=lambda c: [0],  # approve first only
        sources_path=synthetic_sources_path,
        top_k_suggestions=3,
    )
    assert out.approved is True
    assert len(out.tasks) == 1
    assert out.tasks[0].url == out.candidates[0].url


def test_execute_dedupes_identical_urls_across_suggestions(
    synthetic_sources_path: Path,
):
    """If two site:-scoped queries surface the same URL, the candidate
    list dedupes — we don't ask the user to approve the same thing twice."""
    same_url = "https://bjsm.bmj.com/shared"

    def web_search(query: str):
        return [WebSearchResult(url=same_url, title="Shared")]

    out = execute_research_gap(
        _bsi_gap(),
        web_search=web_search,
        approve=lambda c: list(range(len(c))),
        sources_path=synthetic_sources_path,
        top_k_suggestions=3,
    )
    urls = [c.url for c in out.candidates]
    assert urls.count(same_url) == 1


# --- CLI: --execute brief shape ------------------------------------------


def test_cli_execute_emits_json_brief_for_gap(monkeypatch):
    """`coach research-gap "<q>" --execute` prints a JSON brief whose
    suggestions[].query strings are EXACTLY what the slash command must
    pass to WebSearch — never a paraphrase."""

    def fake_search(*args, **kwargs):
        return []  # force a no_hits gap

    monkeypatch.setattr(gap_search, "search", fake_search)

    result = runner.invoke(
        app,
        ["research-gap", "BSI return to run", "--topic", "injury", "--execute", "--top-k", "3"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)

    assert payload["gap_detected"] is True
    assert payload["reason"] == "no_hits"
    assert payload["constraints"]["queries_constrained_to_suggestions"] is True
    assert payload["constraints"]["approval_required"] is True
    assert payload["constraints"]["ingest_via"] == "research-gap"

    suggestions = payload["suggestions"]
    assert len(suggestions) >= 2
    # Every suggestion must be a constrained query — site:-scoped or
    # author-quoted. Both forms are produced by suggest_research_queries.
    for s in suggestions:
        q = s["query"]
        assert q.startswith("site:") or q.startswith('"'), q
        assert "BSI return to run" in q


def test_cli_execute_when_local_knowledge_sufficient(monkeypatch):
    """Brief shape when the corpus answered the question — no suggestions, no fetch."""
    from tempo.embed import SearchHit

    def fake_search(*args, **kwargs):
        return [
            SearchHit(
                id="x",
                text="local hit text",
                path="knowledge/research/2025/01/foo.md",
                topic="injury",
                credibility="peer_reviewed",
                source_ids=[],
                phase="",
                score=0.9,
            )
        ] * 2

    monkeypatch.setattr(gap_search, "search", fake_search)

    result = runner.invoke(app, ["research-gap", "x", "--execute"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["gap_detected"] is False
    assert payload["suggestions"] == []
    assert "Local knowledge sufficient" in payload["runbook"]
