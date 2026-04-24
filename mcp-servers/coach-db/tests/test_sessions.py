"""Tests for coach_db_mcp.sessions.find_similar_session."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from tempo import embed

from coach_db_mcp import sessions
from coach_db_mcp.models import SessionMatch

_SAMPLE = """# Session Library

## Bike

### `long_ride_z2`
- **Purpose:** aerobic capacity
- **Duration:** 2.5–5 hours
- **TSS:** 120–300
- **Structure:** steady Z2

### `threshold_bike`
- **Purpose:** raise FTP
- **Duration:** 75–90 min
- **TSS:** 80–110
- **Structure:** 3–4×10min @ 95–102%FTP

## Run

### `long_run_z2`
- **Purpose:** endurance
- **Duration:** 75–150 min
- **TSS:** 70–150
- **Structure:** Z2 by HR
"""


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


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    lib = tmp_path / "session-library.md"
    lib.write_text(_SAMPLE, encoding="utf-8")
    vdir = tmp_path / "vectors"
    embed.rebuild_sessions(
        session_library=lib, vectors_dir=vdir, embedder=_fake_embedder()
    )
    return vdir


def test_find_similar_session_returns_models(seeded: Path) -> None:
    sessions.set_embedder_override(_fake_embedder())
    try:
        hits = sessions.find_similar_session(
            "long aerobic ride with steady Z2",
            k=3,
            vectors_dir=seeded,
        )
    finally:
        sessions.set_embedder_override(None)
    assert hits
    assert all(isinstance(h, SessionMatch) for h in hits)
    # The long_ride_z2 session should rank in the top 3.
    assert any(h.id == "long_ride_z2" for h in hits[:3])


def test_find_similar_session_sport_filter(seeded: Path) -> None:
    sessions.set_embedder_override(_fake_embedder())
    try:
        hits = sessions.find_similar_session(
            "aerobic endurance run",
            k=5,
            sport="run",
            vectors_dir=seeded,
        )
    finally:
        sessions.set_embedder_override(None)
    assert hits
    assert all(h.sport == "run" for h in hits)


def test_find_similar_session_empty_index(tmp_path: Path) -> None:
    hits = sessions.find_similar_session("whatever", vectors_dir=tmp_path / "empty")
    assert hits == []


def test_session_match_preserves_null_ranges(tmp_path: Path) -> None:
    # Entries with unparseable TSS should come back as None, not -1.
    lib = tmp_path / "lib.md"
    lib.write_text(
        "## Run\n\n### `qualitative_only`\n"
        "- **Purpose:** neuromuscular activation\n"
        "- **Duration:** see coach\n"
        "- **TSS:** see coach\n"
        "- **Structure:** variable\n",
        encoding="utf-8",
    )
    vdir = tmp_path / "v"
    embed.rebuild_sessions(session_library=lib, vectors_dir=vdir, embedder=_fake_embedder())

    sessions.set_embedder_override(_fake_embedder())
    try:
        hits = sessions.find_similar_session(
            "neuromuscular activation variable",
            k=1,
            vectors_dir=vdir,
        )
    finally:
        sessions.set_embedder_override(None)
    assert hits
    assert hits[0].tss_lo is None
    assert hits[0].tss_hi is None
    assert hits[0].duration_min_lo is None
