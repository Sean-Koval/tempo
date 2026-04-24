"""``find_similar_session`` — wraps tempo.embed.search_sessions.

Used by plan-training-week (Phase 4) before inventing a new workout.
"""

from __future__ import annotations

from pathlib import Path

from tempo.embed import Embedder, search_sessions

from .models import SessionMatch

_embedder_override: Embedder | None = None


def set_embedder_override(embedder: Embedder | None) -> None:
    global _embedder_override
    _embedder_override = embedder


def find_similar_session(
    description: str,
    *,
    k: int = 3,
    sport: str | None = None,
    vectors_dir: Path | None = None,
) -> list[SessionMatch]:
    raw = search_sessions(
        description,
        k=k,
        sport=sport,
        vectors_dir=vectors_dir,
        embedder=_embedder_override,
    )
    return [
        SessionMatch(
            id=m.id,
            text=m.text,
            sport=m.sport,
            purpose=m.purpose,
            duration_min_lo=m.duration_min_lo,
            duration_min_hi=m.duration_min_hi,
            tss_lo=m.tss_lo,
            tss_hi=m.tss_hi,
            score=m.score,
        )
        for m in raw
    ]
