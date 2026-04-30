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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


__all__ = [
    "ApproveFn",
    "FetchCandidate",
    "IngestTask",
    "KnowledgeGap",
    "ResearchGapExecution",
    "ResearchQuerySuggestion",
    "RetrievalConfidence",
    "WebSearchFn",
    "WebSearchResult",
    "detect_gap",
    "execute_research_gap",
    "load_sources",
    "suggest_research_queries",
]
