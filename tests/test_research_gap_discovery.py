"""Tests for the discovery branch (tempo-uqc).

Behaviour under test: when ``suggest_research_queries`` returns []
(i.e. no source in ``sources.yaml`` matches the topic), an unconstrained
WebSearch is allowed to fire ONCE with the raw gap query, results are
classified by domain heuristic, and the user can both ingest and propose
domains for ``knowledge/sources-pending.yaml``.

Hard rules these tests pin:
- ``sources.yaml`` is never written to.
- Cancel writes nothing.
- The unconstrained branch only triggers when the constrained path is
  empty — the normal path stays site-scoped.
- ``log_decision`` rationale prefix is ``no_registered_sources``
  (verified via the CLI brief that drives the slash command).
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
    DiscoveryApproval,
    DiscoveryCandidate,
    KnowledgeGap,
    PendingSourceEntry,
    RetrievalConfidence,
    WebSearchResult,
    classify_domain,
    discover_unregistered_sources,
    write_pending_sources,
)

runner = CliRunner()


# --- classify_domain heuristics ------------------------------------------


def test_classify_domain_known_registered_source(tmp_path: Path):
    sources = [
        {"id": "bjsm", "credibility": "peer_reviewed", "domain": "bjsm.bmj.com"},
    ]
    out = classify_domain("https://bjsm.bmj.com/content/57/1/42", sources=sources)
    assert out.status == "known"
    assert out.credibility == "peer_reviewed"
    assert out.matched_source_id == "bjsm"


def test_classify_domain_subdomain_of_known(tmp_path: Path):
    """Subdomains of registered domains should still match."""
    sources = [
        {"id": "bmj", "credibility": "peer_reviewed", "domain": "bmj.com"},
    ]
    out = classify_domain("https://something.bmj.com/x", sources=sources)
    assert out.status == "known"
    assert out.matched_source_id == "bmj"


def test_classify_domain_gov_tld_high_credibility():
    out = classify_domain("https://www.cdc.gov/heat/index.html", sources=[])
    assert out.status == "unknown"
    assert out.credibility == "peer_reviewed"
    assert "government" in out.rationale or "academic" in out.rationale


def test_classify_domain_edu_tld_high_credibility():
    out = classify_domain("https://med.stanford.edu/article", sources=[])
    assert out.credibility == "peer_reviewed"


def test_classify_domain_known_publisher_high_credibility():
    out = classify_domain("https://www.nejm.org/doi/full/x", sources=[])
    assert out.credibility == "peer_reviewed"


def test_classify_domain_pubmed_high_even_without_registry():
    out = classify_domain("https://pubmed.ncbi.nlm.nih.gov/12345", sources=[])
    assert out.credibility == "peer_reviewed"


def test_classify_domain_mass_media_com_needs_vetting():
    """Generic .com gets flagged as needing vetting — not auto-credible."""
    out = classify_domain("https://www.runnersworld.com/some-article", sources=[])
    assert out.status == "unknown"
    assert out.credibility == "evidence_based_journalism"
    assert "vetting" in out.rationale or "vetted" in out.rationale


def test_classify_domain_forum_unvetted():
    out = classify_domain("https://www.reddit.com/r/triathlon/comments/abc", sources=[])
    assert out.credibility == "unvetted"


def test_classify_domain_blog_platform_unvetted():
    out = classify_domain("https://someone.substack.com/p/post", sources=[])
    assert out.credibility == "unvetted"


def test_classify_domain_unparseable_returns_unvetted():
    out = classify_domain("not-a-url", sources=[])
    assert out.credibility == "unvetted"


def test_classify_domain_forum_signal_beats_com_tld():
    """A `.com` host that's actually a forum still classifies as unvetted —
    forum/UGC signals win over the mass-media TLD heuristic."""
    out = classify_domain(
        "https://forum.slowtwitch.com/forum/Slowtwitch_Forums_C1/", sources=[]
    )
    assert out.credibility == "unvetted"


# --- discover_unregistered_sources ---------------------------------------


def _gap(query: str = "novel topic with no registered source") -> KnowledgeGap:
    return KnowledgeGap(
        query=query,
        topic="experimental",
        confidence=RetrievalConfidence(0, 0.0, 5.0, False),
        reason="no_hits",
    )


def test_discover_runs_one_unconstrained_search_with_raw_query(tmp_path: Path):
    """The discovery branch sends the raw gap query, NOT a site:-scoped one."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))

    seen: list[str] = []

    def web_search(q: str):
        seen.append(q)
        return [
            WebSearchResult(
                url="https://pubmed.ncbi.nlm.nih.gov/9999",
                title="Novel topic review",
            )
        ]

    discover_unregistered_sources(
        _gap("novel hyponatremia protocol"),
        web_search=web_search,
        approve=lambda c: [],
        sources_path=sources_path,
    )

    assert seen == ["novel hyponatremia protocol"]
    assert "site:" not in seen[0]


def test_discover_classifies_each_returned_url(tmp_path: Path):
    """Three URLs surfaced → three tentative classifications."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))

    captured: dict = {}

    def web_search(q: str):
        return [
            WebSearchResult(url="https://pubmed.ncbi.nlm.nih.gov/1", title="a"),
            WebSearchResult(url="https://www.cnn.com/2", title="b"),
            WebSearchResult(url="https://www.reddit.com/r/x/3", title="c"),
        ]

    def approve(candidates: Sequence[DiscoveryCandidate]) -> Sequence[DiscoveryApproval]:
        captured["cands"] = list(candidates)
        return []

    discover_unregistered_sources(
        _gap(),
        web_search=web_search,
        approve=approve,
        sources_path=sources_path,
    )

    cands = captured["cands"]
    assert len(cands) == 3
    creds = [c.classification.credibility for c in cands]
    assert creds == ["peer_reviewed", "evidence_based_journalism", "unvetted"]


def test_discover_cancel_writes_nothing(tmp_path: Path):
    """Acceptance: returning empty (or all-False) approvals = cancel."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))
    pending_path = tmp_path / "knowledge" / "sources-pending.yaml"

    def web_search(q: str):
        return [WebSearchResult(url="https://example.gov/a", title="A")]

    out = discover_unregistered_sources(
        _gap(),
        web_search=web_search,
        approve=lambda c: [],  # cancel
        sources_path=sources_path,
    )
    assert out.approved is False
    assert out.tasks == []
    assert out.pending_entries == []
    assert not pending_path.exists()


def test_discover_cancel_via_all_false_decisions(tmp_path: Path):
    """All ingest=False, register=False = same as no decisions = cancel."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))

    def web_search(q: str):
        return [WebSearchResult(url="https://example.gov/a", title="A")]

    out = discover_unregistered_sources(
        _gap(),
        web_search=web_search,
        approve=lambda c: [DiscoveryApproval(index=0, ingest=False, register=False)],
        sources_path=sources_path,
    )
    assert out.approved is False
    assert out.tasks == []
    assert out.pending_entries == []


def test_discover_no_results_no_prompt(tmp_path: Path):
    """If WebSearch returns nothing there's nothing to approve."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))
    approve_calls = {"n": 0}

    def approve(candidates):
        approve_calls["n"] += 1
        return []

    out = discover_unregistered_sources(
        _gap(),
        web_search=lambda q: [],
        approve=approve,
        sources_path=sources_path,
    )
    assert approve_calls["n"] == 0
    assert out.approved is False


def test_discover_approve_emits_ingest_tasks_with_audit_frontmatter(tmp_path: Path):
    """Approving ingest produces IngestTasks tagged ingest_via=research-gap-discovery."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))

    def web_search(q: str):
        return [
            WebSearchResult(url="https://pubmed.ncbi.nlm.nih.gov/abc", title="Paper"),
            WebSearchResult(url="https://medium.com/post", title="Post"),
        ]

    def approve(candidates):
        return [
            DiscoveryApproval(index=0, ingest=True, register=True),
            DiscoveryApproval(index=1, ingest=True, register=False),
        ]

    out = discover_unregistered_sources(
        _gap("foo"),
        web_search=web_search,
        approve=approve,
        sources_path=sources_path,
    )
    assert out.approved is True
    assert len(out.tasks) == 2
    extras = out.tasks[0].frontmatter_extras
    assert extras["ingest_via"] == "research-gap-discovery"
    assert extras["source_id"] == "unlisted"
    assert extras["gap_query"] == "foo"
    assert "domain_classification" in extras
    # Heuristic credibility flows through (pubmed = peer_reviewed even though
    # the user did not override; medium.com = unvetted).
    assert out.tasks[0].credibility == "peer_reviewed"
    assert out.tasks[1].credibility == "unvetted"


def test_discover_credibility_override_only_path_to_upgrade(tmp_path: Path):
    """An unvetted heuristic guess only becomes anything else when a human
    explicitly overrides it. The heuristic never auto-promotes."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))

    def web_search(q: str):
        return [WebSearchResult(url="https://obscure-blog.example/x", title="X")]

    out = discover_unregistered_sources(
        _gap(),
        web_search=web_search,
        approve=lambda c: [
            DiscoveryApproval(
                index=0,
                ingest=True,
                register=False,
                credibility_override="expert_practitioner",
            )
        ],
        sources_path=sources_path,
    )
    assert out.tasks[0].credibility == "expert_practitioner"


def test_discover_register_writes_pending_not_sources(tmp_path: Path):
    """Acceptance: approval with register=True populates sources-pending.yaml."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))
    pending_path = tmp_path / "knowledge" / "sources-pending.yaml"

    def web_search(q: str):
        return [
            WebSearchResult(url="https://pubmed.ncbi.nlm.nih.gov/1", title="A"),
            WebSearchResult(url="https://www.runnersworld.com/2", title="B"),
            WebSearchResult(url="https://newsite.example/3", title="C"),
        ]

    def approve(candidates):
        return [
            DiscoveryApproval(index=0, ingest=True, register=True),
            DiscoveryApproval(index=1, ingest=True, register=True),
            DiscoveryApproval(index=2, ingest=False, register=False),
        ]

    out = discover_unregistered_sources(
        _gap("ITBS strap rehab"),
        web_search=web_search,
        approve=approve,
        sources_path=sources_path,
    )
    assert len(out.pending_entries) == 2
    write_pending_sources(out.pending_entries, path=pending_path)

    assert pending_path.is_file()
    doc = yaml.safe_load(pending_path.read_text())
    domains = {item["domain"] for item in doc["pending"]}
    assert "pubmed.ncbi.nlm.nih.gov" in domains
    assert "runnersworld.com" in domains
    # The third URL was neither ingested nor registered.
    assert "newsite.example" not in domains

    # Hard rule: sources.yaml was not touched.
    src_doc = yaml.safe_load(sources_path.read_text())
    assert src_doc == {"sources": []}


def test_discover_dedupes_register_per_domain(tmp_path: Path):
    """If two URLs from the same domain are registered, write one entry."""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump({"sources": []}))

    def web_search(q: str):
        return [
            WebSearchResult(url="https://example.gov/a", title="A"),
            WebSearchResult(url="https://example.gov/b", title="B"),
        ]

    out = discover_unregistered_sources(
        _gap(),
        web_search=web_search,
        approve=lambda c: [
            DiscoveryApproval(index=0, ingest=True, register=True),
            DiscoveryApproval(index=1, ingest=True, register=True),
        ],
        sources_path=sources_path,
    )
    domains = [e.domain for e in out.pending_entries]
    assert domains == ["example.gov"]


def test_discover_ignores_known_domain_for_classification(tmp_path: Path):
    """A returned URL whose domain is registered should classify as 'known'
    and carry the registered tag — even on the discovery path. (This is
    just defensive: discovery only fires when no suggestions exist for
    the topic, but a registered all-topics source could still surface.)"""
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "id": "trainright",
                        "credibility": "expert_practitioner",
                        "topics": ["cycling"],  # doesn't match gap.topic
                        "domain": "trainright.com",
                    }
                ]
            }
        )
    )

    captured: dict = {}

    def web_search(q: str):
        return [WebSearchResult(url="https://trainright.com/x", title="x")]

    def approve(candidates):
        captured["cls"] = candidates[0].classification
        return []

    discover_unregistered_sources(
        _gap(),
        web_search=web_search,
        approve=approve,
        sources_path=sources_path,
    )
    cls = captured["cls"]
    assert cls.status == "known"
    assert cls.credibility == "expert_practitioner"


# --- write_pending_sources merge semantics -------------------------------


def test_write_pending_sources_creates_file_with_header(tmp_path: Path):
    pending_path = tmp_path / "sources-pending.yaml"
    write_pending_sources(
        [
            PendingSourceEntry(
                id="example-com",
                name="example.com",
                credibility="unvetted",
                topics=["heat"],
                domain="example.com",
                proposed_for_query="heat acclimation",
                rationale="mass-media TLD",
            )
        ],
        path=pending_path,
    )
    doc = yaml.safe_load(pending_path.read_text())
    # Header note explains why pending exists at all.
    assert "deliberate human act" in (doc.get("_note") or "")
    assert "sources.yaml" in (doc.get("_note") or "")
    assert doc["pending"][0]["domain"] == "example.com"


def test_write_pending_sources_appends_without_clobbering(tmp_path: Path):
    pending_path = tmp_path / "sources-pending.yaml"
    pending_path.write_text(
        yaml.safe_dump(
            {
                "_note": "x",
                "pending": [
                    {
                        "id": "old-com",
                        "name": "old.com",
                        "credibility": "evidence_based_journalism",
                        "topics": ["nutrition"],
                        "domain": "old.com",
                    }
                ],
            }
        )
    )

    write_pending_sources(
        [
            PendingSourceEntry(
                id="new-com",
                name="new.com",
                credibility="unvetted",
                topics=["heat"],
                domain="new.com",
            )
        ],
        path=pending_path,
    )

    doc = yaml.safe_load(pending_path.read_text())
    domains = {item["domain"] for item in doc["pending"]}
    assert domains == {"old.com", "new.com"}


def test_write_pending_sources_dedupes_by_domain(tmp_path: Path):
    """Re-running discovery for the same domain is a no-op on the file —
    we don't accumulate duplicate proposals."""
    pending_path = tmp_path / "sources-pending.yaml"
    pending_path.write_text(
        yaml.safe_dump(
            {
                "pending": [
                    {
                        "id": "repeat-com",
                        "name": "repeat.com",
                        "credibility": "unvetted",
                        "topics": ["x"],
                        "domain": "repeat.com",
                    }
                ]
            }
        )
    )
    write_pending_sources(
        [
            PendingSourceEntry(
                id="repeat-com",
                name="repeat.com",
                credibility="unvetted",
                topics=["y"],
                domain="repeat.com",
            )
        ],
        path=pending_path,
    )
    doc = yaml.safe_load(pending_path.read_text())
    assert len(doc["pending"]) == 1


# --- end-to-end: synthetic acceptance scenario ---------------------------


def test_acceptance_3_urls_approved_writes_notes_and_pending(tmp_path: Path):
    """Ticket acceptance: gap query for a topic with no registered source
    → unconstrained search runs → 3 URLs returned with tentative
    classification → approval prompt shows tag + register toggle → on
    approval, /ingest-research path is taken AND sources-pending.yaml
    gains entries."""
    sources_path = tmp_path / "sources.yaml"
    # An empty registry guarantees the discovery branch is the only path.
    sources_path.write_text(yaml.safe_dump({"sources": []}))
    pending_path = tmp_path / "knowledge" / "sources-pending.yaml"

    def web_search(q: str):
        return [
            WebSearchResult(
                url="https://pubmed.ncbi.nlm.nih.gov/100",
                title="Polar vortex acclimation review",
            ),
            WebSearchResult(
                url="https://www.runnersworld.com/cold",
                title="How to train when it's cold",
            ),
            WebSearchResult(
                url="https://forum.slowtwitch.com/topic/abc",
                title="Slowtwitch thread on cold-weather IM",
            ),
        ]

    surfaced: list[DiscoveryCandidate] = []

    def approve(candidates):
        surfaced.extend(candidates)
        # User approves all three for ingest, registers the first two
        # domains, leaves the forum unregistered.
        return [
            DiscoveryApproval(index=0, ingest=True, register=True),
            DiscoveryApproval(index=1, ingest=True, register=True),
            DiscoveryApproval(index=2, ingest=True, register=False),
        ]

    out = discover_unregistered_sources(
        _gap("cold-weather IM acclimation"),
        web_search=web_search,
        approve=approve,
        sources_path=sources_path,
    )
    assert out.approved is True
    assert len(out.tasks) == 3
    assert {t.credibility for t in out.tasks} == {
        "peer_reviewed",
        "evidence_based_journalism",
        "unvetted",
    }
    # Each candidate exposed a credibility tag + the rationale string the
    # approval prompt is supposed to show.
    assert all(c.classification.rationale for c in surfaced)

    write_pending_sources(out.pending_entries, path=pending_path)
    doc = yaml.safe_load(pending_path.read_text())
    domains = {item["domain"] for item in doc["pending"]}
    assert domains == {"pubmed.ncbi.nlm.nih.gov", "runnersworld.com"}
    assert "slowtwitch.com" not in domains  # forum was not registered

    # sources.yaml stayed empty.
    assert yaml.safe_load(sources_path.read_text()) == {"sources": []}


# --- CLI brief surfaces the discovery branch correctly -------------------


def test_cli_execute_no_suggestions_emits_discovery_brief(monkeypatch):
    """When suggest_research_queries returns [], the CLI brief flips into
    discovery mode (instead of exiting with an error). The slash command
    keys off `discovery_required` and the `no_registered_sources` decision
    prefix to drive the new branch."""

    def fake_search(*args, **kwargs):
        return []  # force a no_hits gap

    monkeypatch.setattr(gap_search, "search", fake_search)
    # Force suggest_research_queries to see no sources, so it returns [].
    monkeypatch.setattr(gap_search, "load_sources", lambda path=None: [])

    result = runner.invoke(
        app,
        [
            "research-gap",
            "novel ultra-marathon nutrition protocol",
            "--topic",
            "experimental",
            "--execute",
            "--top-k",
            "3",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["gap_detected"] is True
    assert payload["suggestions"] == []
    assert payload["discovery_required"] is True
    assert payload["constraints"]["queries_constrained_to_suggestions"] is False
    assert (
        payload["constraints"]["unconstrained_query"]
        == "novel ultra-marathon nutrition protocol"
    )
    assert (
        payload["constraints"]["log_decision_gap_reason_prefix"]
        == "no_registered_sources"
    )
    assert "no_registered_sources" in payload["runbook"]


def test_cli_execute_with_suggestions_does_not_set_discovery(monkeypatch):
    """The discovery branch must not leak onto the normal path. When
    sources.yaml has a matching entry, the brief stays constrained and
    `discovery_required` is absent."""

    def fake_search(*args, **kwargs):
        return []

    monkeypatch.setattr(gap_search, "search", fake_search)
    # Use the real default sources.yaml — it has bjsm with topics including
    # "everything" so suggestions WILL come back.

    result = runner.invoke(
        app,
        ["research-gap", "BSI return to run", "--topic", "injury", "--execute", "--top-k", "3"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["gap_detected"] is True
    assert len(payload["suggestions"]) >= 1
    assert "discovery_required" not in payload
    assert payload["constraints"]["queries_constrained_to_suggestions"] is True
    # Every suggestion stays site:-scoped (or author-quoted) — the
    # unconstrained branch is locked off.
    for s in payload["suggestions"]:
        q = s["query"]
        assert q.startswith("site:") or q.startswith('"'), q


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://pubmed.ncbi.nlm.nih.gov/1", "peer_reviewed"),
        ("https://nih.gov/x", "peer_reviewed"),
        ("https://www.bmj.com/content/x", "peer_reviewed"),
        ("https://www.thelancet.com/article", "peer_reviewed"),
        ("https://med.harvard.edu/study", "peer_reviewed"),
        ("https://www.runnersworld.com/article", "evidence_based_journalism"),
        ("https://www.reddit.com/r/x", "unvetted"),
        ("https://x.substack.com/post", "unvetted"),
    ],
)
def test_classify_domain_table(url: str, expected: str):
    """One sweep over the heuristic table — keeps drift visible."""
    assert classify_domain(url, sources=[]).credibility == expected
