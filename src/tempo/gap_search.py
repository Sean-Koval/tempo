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
4. The user picks 0-N suggestions and feeds them to ``/ingest-research``;
   no auto-fetch.

This module deliberately does NOT call the web. Wiring an actual
WebSearch tool is a follow-up — keeping the gap-detection + suggestion
shape stable lets that wiring slot in without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = [
    "KnowledgeGap",
    "ResearchQuerySuggestion",
    "RetrievalConfidence",
    "detect_gap",
    "load_sources",
    "suggest_research_queries",
]
