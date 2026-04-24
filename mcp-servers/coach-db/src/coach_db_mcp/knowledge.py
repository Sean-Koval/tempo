"""``search_knowledge`` — wraps ``tempo.embed.search`` for the MCP surface.

We keep this thin: the indexing/embedding logic lives in ``tempo.embed`` so
``coach vectors rebuild`` and this MCP tool stay single-sourced.

Tests inject a fake embedder via ``set_embedder_override`` to avoid pulling
the ~100MB fastembed model on CI. Production calls go through
``tempo.embed._default_embedder`` (lazy-loaded on first real use).
"""

from __future__ import annotations

from pathlib import Path

from tempo.embed import Embedder, search

from .models import Snippet

_embedder_override: Embedder | None = None


def set_embedder_override(embedder: Embedder | None) -> None:
    """Test hook — inject a deterministic embedder. Pass None to reset."""
    global _embedder_override
    _embedder_override = embedder


def search_knowledge(
    query: str,
    *,
    k: int = 5,
    topic: str | None = None,
    credibility_min: str | None = None,
    vectors_dir: Path | None = None,
) -> list[Snippet]:
    hits = search(
        query,
        k=k,
        topic=topic,
        credibility_min=credibility_min,
        vectors_dir=vectors_dir,
        embedder=_embedder_override,
    )
    return [
        Snippet(
            id=h.id,
            text=h.text,
            path=h.path,
            topic=h.topic,
            credibility=h.credibility,
            source_ids=h.source_ids,
            phase=h.phase,
            score=h.score,
        )
        for h in hits
    ]
