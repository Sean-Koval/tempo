"""Knowledge-gap detection + trusted-source web research suggestions.

When the local corpus has no good answer for a topic the agent needs
(e.g. "BSI return-to-run protocol", "ITBS rehab progressions", "race-day
heat strategy for >32 °C"), the agent used to silently fall back on
general orthodoxy. This module turns that fallback into a structured
escalation:

1. :func:`detect_gap` runs a knowledge search and decides whether the
   hits clear a quality bar (n_hits, max_score, credibility rank).
2. If they don't, it returns a :class:`KnowledgeGap`.
3. :func:`suggest_research_queries` walks ``knowledge/sources.yaml`` and
   builds site-scoped queries against trusted sources whose ``domain``
   field is populated. Sources without a domain (books, podcasts,
   academic researchers without a personal site) get author/title-
   shaped fallback queries.
4. :func:`execute_research_gap` orchestrates the closed loop: it runs
   a caller-supplied ``web_search`` over the (already constrained)
   suggestion queries, surfaces the URL list to a caller-supplied
   ``approve`` callable for an explicit yes/no, and only then yields
   ingest tasks. The hard invariant is that ``web_search`` is only
   ever invoked with queries produced by :func:`suggest_research_queries`
   — never free-form. Cancel at the approval gate writes nothing.
5. :func:`discover_unregistered_sources` is the escape hatch for the
   "no registered source matches this topic at all" case (suggestions
   came back empty). It runs a single UNCONSTRAINED search with the
   raw gap query, tentatively classifies each result's domain, and
   hands back a discovery surface for the same approval gate. This
   path only fires when the constrained path produced zero candidates
   — preserving the credibility-leak protection on every other run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .embed import SearchHit, search
from .paths import repo_root

# Order from strongest (rank=1) to weakest (rank=5). Mirrors the order in
# embed._CREDIBILITY_RANK so we don't drift on rank semantics.
_CREDIBILITY_RANK: dict[str, int] = {
    "peer_reviewed": 1,
    "expert_practitioner": 2,
    "evidence_based_journalism": 3,
    "experiential": 4,
    "unvetted": 5,
}


@dataclass(frozen=True)
class RetrievalConfidence:
    """Aggregate signal for whether the local corpus has answered well."""

    n_hits: int
    max_score: float  # 0.0 - 1.0; embed.search returns this per hit
    mean_credibility_rank: float  # 1.0 (best) - 5.0 (worst)
    threshold_pass: bool

    @classmethod
    def from_hits(
        cls,
        hits: list[SearchHit],
        *,
        min_hits: int = 2,
        min_score: float = 0.6,
        max_credibility_rank: float = 3.0,
    ) -> RetrievalConfidence:
        n = len(hits)
        if n == 0:
            return cls(0, 0.0, 5.0, False)
        max_score = max(h.score for h in hits)
        mean_rank = sum(_CREDIBILITY_RANK.get(h.credibility, 5) for h in hits) / n
        passes = (
            n >= min_hits
            and max_score >= min_score
            and mean_rank <= max_credibility_rank
        )
        return cls(n_hits=n, max_score=max_score, mean_credibility_rank=mean_rank,
                   threshold_pass=passes)


@dataclass(frozen=True)
class KnowledgeGap:
    """Surfaced when local retrieval doesn't clear the confidence bar."""

    query: str
    topic: str | None
    confidence: RetrievalConfidence
    reason: str  # "no_hits" | "low_score" | "low_credibility" | "thin_coverage"


@dataclass(frozen=True)
class ResearchQuerySuggestion:
    """A query the user can paste into /ingest-research after the URL is found."""

    source_id: str
    source_name: str
    credibility: str
    query: str
    domain: str | None  # None when the source has no scoped domain (book / podcast)


# --- Sources --------------------------------------------------------------


def load_sources(path: Path | None = None) -> list[dict[str, Any]]:
    """Return the ``sources:`` list from ``knowledge/sources.yaml``."""
    path = path or repo_root() / "knowledge" / "sources.yaml"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return list(doc.get("sources") or [])


def _credibility_rank(value: str) -> int:
    return _CREDIBILITY_RANK.get(value, 5)


# --- Gap detection --------------------------------------------------------


def detect_gap(
    query: str,
    *,
    topic: str | None = None,
    k: int = 5,
    min_hits: int = 2,
    min_score: float = 0.6,
    max_credibility_rank: float = 3.0,
    credibility_min: str | None = None,
    embedder: Any = None,
    vectors_dir: Path | None = None,
) -> tuple[list[SearchHit], RetrievalConfidence] | KnowledgeGap:
    """Search local knowledge and return either hits + confidence or a gap.

    When the confidence threshold is missed, returns a :class:`KnowledgeGap`
    with ``reason`` set so callers can phrase the next step appropriately:

    - ``no_hits``         — zero results
    - ``thin_coverage``   — n_hits < min_hits
    - ``low_score``       — max_score < min_score
    - ``low_credibility`` — mean rank > max_credibility_rank
    """
    hits = search(
        query,
        k=k,
        topic=topic,
        credibility_min=credibility_min,
        vectors_dir=vectors_dir,
        embedder=embedder,
    )
    confidence = RetrievalConfidence.from_hits(
        hits,
        min_hits=min_hits,
        min_score=min_score,
        max_credibility_rank=max_credibility_rank,
    )
    if confidence.threshold_pass:
        return hits, confidence

    if confidence.n_hits == 0:
        reason = "no_hits"
    elif confidence.n_hits < min_hits:
        reason = "thin_coverage"
    elif confidence.max_score < min_score:
        reason = "low_score"
    else:
        reason = "low_credibility"

    return KnowledgeGap(query=query, topic=topic, confidence=confidence, reason=reason)


# --- Query suggestion -----------------------------------------------------


def _topic_match(source: dict[str, Any], topic: str | None) -> bool:
    """``everything`` matches any topic; otherwise overlap or no filter."""
    if topic is None:
        return True
    src_topics = source.get("topics") or []
    return "everything" in src_topics or topic in src_topics


def suggest_research_queries(
    gap: KnowledgeGap,
    *,
    sources_path: Path | None = None,
    k: int = 5,
    topic_filter: str | None = None,
) -> list[ResearchQuerySuggestion]:
    """Build site-scoped queries from sources.yaml, ranked by credibility.

    For sources with a ``domain`` field, builds ``site:<domain> <query>``.
    For sources without one (books, academic researchers, podcasts), builds
    ``"<author/title cue>" <query>`` so the user can paste it into a regular
    web search.

    Sources that don't match ``topic_filter`` (defaulting to ``gap.topic``)
    are dropped. Within the surviving set, sources are sorted by credibility
    rank ascending (peer-reviewed first), with ``domain``-bearing sources
    preferred over non-domain when ranks tie.
    """
    sources = load_sources(sources_path)
    topic = topic_filter if topic_filter is not None else gap.topic

    candidates = [s for s in sources if _topic_match(s, topic)]
    candidates.sort(
        key=lambda s: (
            _credibility_rank(s.get("credibility", "")),
            0 if s.get("domain") else 1,  # domain-bearing first
            s.get("id") or "",
        )
    )

    suggestions: list[ResearchQuerySuggestion] = []
    for src in candidates:
        domain = src.get("domain")
        if domain:
            query = f"site:{domain} {gap.query}"
        else:
            query = _author_query(src, gap.query)
        suggestions.append(
            ResearchQuerySuggestion(
                source_id=src.get("id") or "(no id)",
                source_name=src.get("name") or src.get("id") or "(unnamed)",
                credibility=src.get("credibility", "unvetted"),
                query=query,
                domain=domain,
            )
        )
        if len(suggestions) >= k:
            break

    return suggestions


def _author_query(source: dict[str, Any], query: str) -> str:
    """Build a non-site query keyed on an author or title cue.

    Pulls the leading author/title fragment from ``name``: e.g. for
    ``"Joe Friel — joefrielsblog.com"`` returns ``"Joe Friel" <query>``.
    Falls back to the source id if name parsing fails.
    """
    name = source.get("name") or ""
    fragment = name.split("—")[0].strip()
    if not fragment or fragment == name:
        # No em-dash split; try the first 4 words.
        fragment = " ".join(name.split()[:4]).strip(" ()")
    if not fragment:
        fragment = source.get("id") or ""
    return f'"{fragment}" {query}'.strip()


# --- Closed-loop execution ------------------------------------------------


@dataclass(frozen=True)
class WebSearchResult:
    """A single hit returned by the caller-supplied ``web_search`` callable.

    Mirrors the minimal shape the Claude Code WebSearch tool emits per result.
    Only ``url`` and ``title`` are required; ``snippet`` is best-effort.
    """

    url: str
    title: str
    snippet: str = ""


@dataclass(frozen=True)
class FetchCandidate:
    """A web-search result paired with the suggestion that produced it.

    Carries forward the credibility tag from the source registry so the
    approval prompt can show it alongside the URL — and so the downstream
    ingest step can stamp the right ``credibility`` into frontmatter
    without re-deriving it.
    """

    url: str
    title: str
    snippet: str
    suggestion: ResearchQuerySuggestion
    credibility: str  # mirrors suggestion.credibility, copied for ingest convenience


@dataclass(frozen=True)
class IngestTask:
    """An approved URL ready for the ingest-research pipeline.

    The ``frontmatter_extras`` dict is what callers should splice into the
    note's frontmatter so future retrieval can trace it back to the gap
    that prompted it. Keys: ``ingest_via``, ``gap_query``, ``suggestion``,
    ``source_id``.
    """

    url: str
    title: str
    credibility: str
    suggestion: ResearchQuerySuggestion
    gap_query: str
    frontmatter_extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchGapExecution:
    """Outcome of :func:`execute_research_gap`.

    ``approved`` is False when the caller cancelled at the approval gate
    (or there were no candidates to approve). ``tasks`` is empty when
    ``approved`` is False — that is the "cancel writes nothing" invariant.
    """

    gap: KnowledgeGap
    suggestions: list[ResearchQuerySuggestion]
    candidates: list[FetchCandidate]
    approved: bool
    tasks: list[IngestTask]


WebSearchFn = Callable[[str], Iterable[WebSearchResult]]
ApproveFn = Callable[[Sequence[FetchCandidate]], Sequence[int]]


def execute_research_gap(
    gap: KnowledgeGap,
    *,
    web_search: WebSearchFn,
    approve: ApproveFn,
    sources_path: Path | None = None,
    top_k_suggestions: int = 3,
    max_results_per_query: int = 5,
    topic_filter: str | None = None,
) -> ResearchGapExecution:
    """Run the constrained-search → approval → ingest-task pipeline.

    The hard invariant: ``web_search`` is only ever called with queries
    produced by :func:`suggest_research_queries` — never free-form input
    from the caller. This preserves the credibility-leak protection: every
    search is site-scoped to a registered source.

    Args:
        gap: A detected :class:`KnowledgeGap`.
        web_search: Callable taking a single suggestion query and yielding
            :class:`WebSearchResult` values. The caller (a slash command
            or test) is responsible for invoking the actual WebSearch tool.
        approve: Callable taking the assembled candidate list and returning
            the indices the user approved (0..len(candidates)-1). Returning
            an empty sequence means "cancel" — no tasks are emitted.
        sources_path: Override for ``knowledge/sources.yaml`` (tests).
        top_k_suggestions: How many suggestion queries to run (default 3
            per ticket: peer-reviewed-first triumvirate).
        max_results_per_query: Cap per-query results to keep the approval
            list scannable.
        topic_filter: Forwarded to :func:`suggest_research_queries`.

    Returns:
        :class:`ResearchGapExecution` with ``approved`` and ``tasks`` set.
        On cancel, ``tasks`` is empty and the caller writes nothing.
    """
    suggestions = suggest_research_queries(
        gap,
        sources_path=sources_path,
        k=top_k_suggestions,
        topic_filter=topic_filter,
    )

    candidates: list[FetchCandidate] = []
    seen_urls: set[str] = set()
    for sug in suggestions:
        # Constrained: only the suggestion's prebuilt query is sent. Never gap.query alone.
        for raw in web_search(sug.query):
            if not raw.url or raw.url in seen_urls:
                continue
            seen_urls.add(raw.url)
            candidates.append(
                FetchCandidate(
                    url=raw.url,
                    title=raw.title or raw.url,
                    snippet=raw.snippet,
                    suggestion=sug,
                    credibility=sug.credibility,
                )
            )
            if (
                sum(1 for c in candidates if c.suggestion.source_id == sug.source_id)
                >= max_results_per_query
            ):
                break

    if not candidates:
        return ResearchGapExecution(
            gap=gap,
            suggestions=suggestions,
            candidates=[],
            approved=False,
            tasks=[],
        )

    approved_indices = list(approve(candidates))
    if not approved_indices:
        return ResearchGapExecution(
            gap=gap,
            suggestions=suggestions,
            candidates=candidates,
            approved=False,
            tasks=[],
        )

    tasks: list[IngestTask] = []
    for i in approved_indices:
        if not (0 <= i < len(candidates)):
            continue
        c = candidates[i]
        tasks.append(
            IngestTask(
                url=c.url,
                title=c.title,
                credibility=c.credibility,
                suggestion=c.suggestion,
                gap_query=gap.query,
                frontmatter_extras={
                    "ingest_via": "research-gap",
                    "gap_query": gap.query,
                    "suggestion": c.suggestion.query,
                    "source_id": c.suggestion.source_id,
                },
            )
        )

    return ResearchGapExecution(
        gap=gap,
        suggestions=suggestions,
        candidates=candidates,
        approved=True,
        tasks=tasks,
    )


# --- Discovery (no-registered-sources path) ------------------------------
#
# WHY this lives behind the [] gate (don't tear it out without re-reading):
#
# The whole credibility-leak protection in this module rests on never
# sending a free-form query to a web search. Every constrained query is
# `site:<registered domain>` so the surfaced URLs are guaranteed to be on
# a domain we already trust at a known credibility tier. The discovery
# branch deliberately violates that — it has to, because the user's whole
# point with this branch is "I have NO source registered for this topic;
# please find me one." We pay for that escape hatch with two compensating
# controls:
#   1. It only runs when `suggest_research_queries` returned []. On every
#      other call the constrained path is the only path.
#   2. Tentative classification stays *tentative* — every URL surfaced via
#      discovery is `unvetted` unless the human upgrades it during the
#      approval prompt, and proposed registry entries are written to
#      `knowledge/sources-pending.yaml` (NEVER `sources.yaml`) so promotion
#      to the trusted registry remains a deliberate human act.

# Conservative high-credibility list: government, academic, peer-reviewed
# medical publishers we recognise by name. Suffix-matched against the
# hostname (so `bjsm.bmj.com` matches the `bmj.com` entry). Keep this list
# short — when in doubt classify lower; humans can upgrade. New peer-review
# publishers should be added explicitly, not by clever pattern-matching.
_HIGH_CRED_DOMAINS: tuple[str, ...] = (
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "nih.gov",
    "cdc.gov",
    "who.int",
    "jamanetwork.com",
    "nejm.org",
    "bmj.com",
    "thelancet.com",
    "sciencedirect.com",
    "springer.com",
    "nature.com",
    "cell.com",
    "wiley.com",
    "tandfonline.com",
    "oup.com",  # Oxford University Press
    "cambridge.org",
)

# TLDs that imply mass-media / commercial journalism. We treat hosts on
# these TLDs as `evidence_based_journalism` (vetted_needed) — never
# auto-credible. Stored without the leading dot so they can be tested
# with `host.endswith(tld)`.
_VETTED_NEEDED_TLDS: tuple[str, ...] = (
    ".com",
    ".org",
    ".net",
    ".io",
    ".co",
)

# Hostname tokens that strongly signal user-generated content / forums /
# personal blogs. These map straight to `unvetted`.
_UNVETTED_TOKENS: tuple[str, ...] = (
    "reddit.com",
    "quora.com",
    "medium.com",
    "substack.com",
    "wordpress.com",
    "blogspot.",
    "tumblr.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "stackexchange.com",
    "stackoverflow.com",
    "letsrun.com",  # forums
    "slowtwitch.com",  # forums
)


def _hostname_of(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.removeprefix("www.").lower()


def _suffix_match(host: str, candidates: Iterable[str]) -> bool:
    return any(host == c or host.endswith("." + c) for c in candidates)


@dataclass(frozen=True)
class DomainClassification:
    """Tentative credibility for a domain we don't have in sources.yaml.

    ``status`` is ``"known"`` when the URL maps onto a registered source,
    ``"unknown"`` otherwise. ``credibility`` mirrors the registered tag for
    known domains and a heuristic guess for unknown ones (see the comment
    at the top of the discovery section for why those guesses stay
    tentative). ``rationale`` is a one-phrase explanation surfaced to the
    user so they can make an informed approval call.
    """

    domain: str
    status: str  # "known" | "unknown"
    credibility: str  # heuristic guess, or registered tag for known
    rationale: str
    matched_source_id: str | None = None


def classify_domain(
    url: str,
    *,
    sources: list[dict[str, Any]] | None = None,
) -> DomainClassification:
    """Classify a URL's domain against the registered sources first, then
    fall back to suffix heuristics. Heuristic outcomes are deliberately
    conservative: government / academic / known peer-review publishers are
    high; mass-media TLDs default to ``vetted_needed``; forum-shaped hosts
    are ``unvetted``."""
    host = _hostname_of(url) or ""
    if not host:
        return DomainClassification(
            domain="",
            status="unknown",
            credibility="unvetted",
            rationale="URL had no parseable hostname",
        )

    sources = sources if sources is not None else load_sources()
    for src in sources:
        registered_domain = (src.get("domain") or "").lower()
        if not registered_domain:
            continue
        if host == registered_domain or host.endswith("." + registered_domain):
            return DomainClassification(
                domain=host,
                status="known",
                credibility=src.get("credibility", "unvetted"),
                rationale=f"matches registered source {src.get('id') or '?'}",
                matched_source_id=src.get("id"),
            )

    # Strong unvetted signals win first — a `.com` forum is still a forum.
    if any(tok in host for tok in _UNVETTED_TOKENS):
        return DomainClassification(
            domain=host,
            status="unknown",
            credibility="unvetted",
            rationale="forum / user-generated / personal-blog host",
        )

    if host.endswith(".gov") or host.endswith(".edu"):
        return DomainClassification(
            domain=host,
            status="unknown",
            credibility="peer_reviewed",
            rationale="government / academic TLD",
        )

    if _suffix_match(host, _HIGH_CRED_DOMAINS):
        return DomainClassification(
            domain=host,
            status="unknown",
            credibility="peer_reviewed",
            rationale="known peer-review or institutional publisher",
        )

    if any(host.endswith(tld) for tld in _VETTED_NEEDED_TLDS):
        return DomainClassification(
            domain=host,
            status="unknown",
            credibility="evidence_based_journalism",
            rationale="mass-media TLD — needs human vetting before it counts as evidence",
        )

    return DomainClassification(
        domain=host,
        status="unknown",
        credibility="unvetted",
        rationale="no recognised credibility signal",
    )


@dataclass(frozen=True)
class DiscoveryCandidate:
    """A web-search hit from the unconstrained discovery path.

    Carries the tentative :class:`DomainClassification` so the approval
    surface can show "[unvetted, mass-media TLD] example.com — <title>"
    and let the user decide whether to ingest at all and whether to draft
    a `sources-pending.yaml` entry.
    """

    url: str
    title: str
    snippet: str
    classification: DomainClassification


@dataclass(frozen=True)
class DiscoveryApproval:
    """Per-candidate decision returned by the discovery approval callback.

    ``ingest`` — bring this URL through /ingest-research.
    ``register`` — also append a draft entry for this domain to
        ``knowledge/sources-pending.yaml`` (the human still has to promote).
    ``credibility_override`` — optional human override of the heuristic tag.
        When ``None``, the classification's tentative tag is kept. Setting
        this to a stronger tier is the ONLY way an unvetted result becomes
        anything else — the heuristic never auto-upgrades.
    """

    index: int
    ingest: bool
    register: bool
    credibility_override: str | None = None


@dataclass(frozen=True)
class PendingSourceEntry:
    """Draft entry for ``knowledge/sources-pending.yaml``.

    Mirrors the shape of ``sources.yaml`` entries (id, name, credibility,
    topics, domain) plus a discovery audit trail (``proposed_via``,
    ``proposed_for_query``, ``proposed_at_isoweek``-style note). The pending
    file is APPEND-ONLY from this module's perspective — never written to
    ``sources.yaml`` directly.
    """

    id: str
    name: str
    credibility: str
    topics: list[str]
    domain: str
    proposed_via: str = "research-gap-discovery"
    proposed_for_query: str = ""
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "credibility": self.credibility,
            "topics": list(self.topics),
            "domain": self.domain,
            "proposed_via": self.proposed_via,
            "proposed_for_query": self.proposed_for_query,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class DiscoveryExecution:
    """Outcome of :func:`discover_unregistered_sources`.

    ``approved`` is False when the user cancelled (returned no
    approvals) or when there were no discovery candidates at all.
    ``tasks`` and ``pending_entries`` are both empty in that case —
    cancel writes nothing.
    """

    gap: KnowledgeGap
    candidates: list[DiscoveryCandidate]
    approved: bool
    tasks: list[IngestTask]
    pending_entries: list[PendingSourceEntry]


DiscoveryApproveFn = Callable[
    [Sequence[DiscoveryCandidate]], Sequence[DiscoveryApproval]
]


def _slug_for_domain(host: str) -> str:
    return host.replace(".", "-").lower()


def discover_unregistered_sources(
    gap: KnowledgeGap,
    *,
    web_search: WebSearchFn,
    approve: DiscoveryApproveFn,
    sources_path: Path | None = None,
    max_results: int = 10,
) -> DiscoveryExecution:
    """Run an UNCONSTRAINED search for the gap query and surface results
    with tentative domain classification.

    HARD invariant: callers must only invoke this when
    :func:`suggest_research_queries` returned [] — i.e. there is no
    registered source for this topic. The CLI ``--execute`` flow enforces
    that gating; tests should mirror it.

    Args:
        gap: The detected gap whose ``query`` will be sent free-form.
        web_search: Callable taking a single query and yielding
            :class:`WebSearchResult` values. Same contract as
            :func:`execute_research_gap`.
        approve: Callable taking the candidate list and returning
            per-candidate :class:`DiscoveryApproval` decisions. Returning
            an empty sequence (or an all-``ingest=False, register=False``
            sequence) means cancel — nothing is written.
        sources_path: Override for ``knowledge/sources.yaml`` (tests).
        max_results: Cap on the unconstrained-search result list.
    """
    sources = load_sources(sources_path)
    seen_urls: set[str] = set()
    candidates: list[DiscoveryCandidate] = []
    for raw in web_search(gap.query):
        if not raw.url or raw.url in seen_urls:
            continue
        seen_urls.add(raw.url)
        classification = classify_domain(raw.url, sources=sources)
        candidates.append(
            DiscoveryCandidate(
                url=raw.url,
                title=raw.title or raw.url,
                snippet=raw.snippet,
                classification=classification,
            )
        )
        if len(candidates) >= max_results:
            break

    if not candidates:
        return DiscoveryExecution(
            gap=gap,
            candidates=[],
            approved=False,
            tasks=[],
            pending_entries=[],
        )

    decisions = list(approve(candidates))
    actionable = [
        d for d in decisions if 0 <= d.index < len(candidates) and (d.ingest or d.register)
    ]
    if not actionable:
        return DiscoveryExecution(
            gap=gap,
            candidates=candidates,
            approved=False,
            tasks=[],
            pending_entries=[],
        )

    tasks: list[IngestTask] = []
    pending_entries: list[PendingSourceEntry] = []
    pending_by_domain: dict[str, PendingSourceEntry] = {}

    for d in actionable:
        c = candidates[d.index]
        # Human override is the only way to leave `unvetted` for something
        # higher than the heuristic guessed. We don't auto-promote on
        # behalf of the user, even if the URL came back from a `.gov`.
        credibility = d.credibility_override or c.classification.credibility
        if d.ingest:
            # Synthesise a minimal "suggestion" so the IngestTask shape
            # stays compatible with the constrained path's downstream
            # consumers — but mark source_id as "unlisted" so the
            # frontmatter audit makes the discovery origin obvious.
            sug = ResearchQuerySuggestion(
                source_id="unlisted",
                source_name=c.classification.domain or "(unknown domain)",
                credibility=credibility,
                query=gap.query,
                domain=c.classification.domain or None,
            )
            tasks.append(
                IngestTask(
                    url=c.url,
                    title=c.title,
                    credibility=credibility,
                    suggestion=sug,
                    gap_query=gap.query,
                    frontmatter_extras={
                        "ingest_via": "research-gap-discovery",
                        "gap_query": gap.query,
                        "suggestion": gap.query,
                        "source_id": "unlisted",
                        "domain_classification": c.classification.rationale,
                    },
                )
            )
        if d.register:
            host = c.classification.domain
            if not host or host in pending_by_domain:
                continue
            entry = PendingSourceEntry(
                id=_slug_for_domain(host),
                name=host,
                credibility=credibility,
                topics=[gap.topic] if gap.topic else ["unsorted"],
                domain=host,
                proposed_via="research-gap-discovery",
                proposed_for_query=gap.query,
                rationale=c.classification.rationale,
            )
            pending_by_domain[host] = entry
            pending_entries.append(entry)

    return DiscoveryExecution(
        gap=gap,
        candidates=candidates,
        approved=True,
        tasks=tasks,
        pending_entries=pending_entries,
    )


def write_pending_sources(
    entries: Sequence[PendingSourceEntry],
    *,
    path: Path | None = None,
) -> Path:
    """Append-or-merge ``entries`` into ``knowledge/sources-pending.yaml``.

    Existing entries (matched on ``domain``) are NOT clobbered. New entries
    are appended. The file is created with a header explaining its purpose
    if it doesn't exist. Returns the path written to.

    NEVER writes to ``sources.yaml`` — promotion stays a deliberate human
    act. That hard rule is the whole reason for keeping this in a
    separate file.
    """
    target = path or (repo_root() / "knowledge" / "sources-pending.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_doc: dict[str, Any] = {}
    if target.is_file():
        with target.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if isinstance(loaded, dict):
            existing_doc = loaded

    existing_list = list(existing_doc.get("pending") or [])
    existing_domains = {
        (item.get("domain") or "").lower() for item in existing_list if isinstance(item, dict)
    }

    for entry in entries:
        if entry.domain.lower() in existing_domains:
            continue
        existing_list.append(entry.to_dict())
        existing_domains.add(entry.domain.lower())

    out_doc = {
        "_note": (
            "Draft entries proposed by research-gap discovery. "
            "Promotion to sources.yaml is a deliberate human act — "
            "review credibility and topics before moving an entry."
        ),
        "pending": existing_list,
    }

    with target.open("w", encoding="utf-8") as f:
        yaml.safe_dump(out_doc, f, sort_keys=False)
    return target


__all__ = [
    "ApproveFn",
    "DiscoveryApproval",
    "DiscoveryApproveFn",
    "DiscoveryCandidate",
    "DiscoveryExecution",
    "DomainClassification",
    "FetchCandidate",
    "IngestTask",
    "KnowledgeGap",
    "PendingSourceEntry",
    "ResearchGapExecution",
    "ResearchQuerySuggestion",
    "RetrievalConfidence",
    "WebSearchFn",
    "WebSearchResult",
    "classify_domain",
    "detect_gap",
    "discover_unregistered_sources",
    "execute_research_gap",
    "load_sources",
    "suggest_research_queries",
    "write_pending_sources",
]
