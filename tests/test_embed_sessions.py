"""Tests for ``tempo.embed.rebuild_sessions`` + ``search_sessions``."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from tempo import embed

_SAMPLE_LIBRARY = """---
type: methodology
topic: session_library
---

# Session Library

## Swim

### `technique_swim`
- **Purpose:** stroke quality, not fitness
- **Duration:** 45–60 min
- **TSS:** ~30–45
- **Structure:** 400 WU → 4×50 drill focus → 8×50 stroke-count → 400 CD

### `aerobic_swim_set`
- **Purpose:** Z2 swim volume
- **Duration:** 45–75 min
- **TSS:** ~45–65
- **Structure:** 400 WU → 3–5×600 CSS+10 → 200 CD

---

## Bike

### `long_ride_z2`
- **Purpose:** aerobic capacity, durability, fat oxidation
- **Duration:** 2.5–5 hours
- **TSS:** 120–300
- **Structure:** steady Z2 by HR drift; fuel 60–90g carbs/hr

### `threshold_bike`
- **Purpose:** raise FTP
- **Duration:** 75–90 min
- **TSS:** 80–110
- **Structure:** 3–4×10min @ 95–102%FTP

---

## Run

### `long_run_z2`
- **Purpose:** running economy under fatigue
- **Duration:** 75–150 min
- **TSS:** 70–150
- **Structure:** Z2 by HR with drift watched
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
def sample_library(tmp_path: Path) -> Path:
    lib = tmp_path / "session-library.md"
    lib.write_text(_SAMPLE_LIBRARY, encoding="utf-8")
    return lib


def test_rebuild_sessions_parses_all_entries(sample_library: Path, tmp_path: Path) -> None:
    vdir = tmp_path / "vectors"
    stats = embed.rebuild_sessions(
        session_library=sample_library,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    # 2 swim + 2 bike + 1 run = 5 sessions
    assert stats.entries_scanned == 5
    assert stats.entries_embedded == 5


def test_rebuild_sessions_extracts_duration_hours_to_minutes(
    sample_library: Path, tmp_path: Path
) -> None:
    vdir = tmp_path / "vectors"
    embed.rebuild_sessions(
        session_library=sample_library,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    hits = embed.search_sessions(
        "long aerobic ride fueling",
        k=5,
        sport="bike",
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    long_ride = next((h for h in hits if h.id == "long_ride_z2"), None)
    assert long_ride is not None
    # "2.5–5 hours" → 150–300 minutes
    assert long_ride.duration_min_lo == 150
    assert long_ride.duration_min_hi == 300
    assert long_ride.tss_lo == 120
    assert long_ride.tss_hi == 300


def test_rebuild_sessions_is_idempotent(sample_library: Path, tmp_path: Path) -> None:
    vdir = tmp_path / "vectors"
    embed.rebuild_sessions(
        session_library=sample_library,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    stats = embed.rebuild_sessions(
        session_library=sample_library,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    assert stats.entries_embedded == 0
    assert stats.entries_skipped == 5


def test_rebuild_sessions_replaces_on_change(sample_library: Path, tmp_path: Path) -> None:
    vdir = tmp_path / "vectors"
    embed.rebuild_sessions(
        session_library=sample_library,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    sample_library.write_text(
        _SAMPLE_LIBRARY + "\n### `race_pace_bike`\n"
        "- **Purpose:** IM-pace practice\n"
        "- **Duration:** 3–5 hours\n"
        "- **TSS:** 180–300\n"
        "- **Structure:** sustained NP\n",
        encoding="utf-8",
    )
    stats = embed.rebuild_sessions(
        session_library=sample_library,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    assert stats.entries_embedded == 6
    assert stats.rows_deleted == 5


def test_search_sessions_sport_filter(sample_library: Path, tmp_path: Path) -> None:
    vdir = tmp_path / "vectors"
    embed.rebuild_sessions(
        session_library=sample_library,
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    hits = embed.search_sessions(
        "swim drill stroke",
        k=3,
        sport="swim",
        vectors_dir=vdir,
        embedder=_fake_embedder(),
    )
    assert hits
    assert all(h.sport == "swim" for h in hits)


def test_search_sessions_empty_index(tmp_path: Path) -> None:
    hits = embed.search_sessions("anything", vectors_dir=tmp_path / "empty",
                                 embedder=_fake_embedder())
    assert hits == []


def test_rebuild_sessions_directory_mode(tmp_path: Path) -> None:
    """Directory of per-sport markdown files is picked up the same way."""
    lib_dir = tmp_path / "session-library"
    lib_dir.mkdir()
    (lib_dir / "swim.md").write_text(
        "## Swim\n\n### `technique_swim`\n"
        "- **Purpose:** stroke quality\n"
        "- **Duration:** 45–60 min\n"
        "- **TSS:** 30–45\n"
        "- **Structure:** drills\n",
        encoding="utf-8",
    )
    (lib_dir / "bike.md").write_text(
        "## Bike\n\n### `long_ride_z2`\n"
        "- **Purpose:** durability\n"
        "- **Duration:** 2.5–5 hours\n"
        "- **TSS:** 120–300\n"
        "- **Structure:** steady Z2\n",
        encoding="utf-8",
    )
    vdir = tmp_path / "vectors"
    stats = embed.rebuild_sessions(
        session_library=lib_dir, vectors_dir=vdir, embedder=_fake_embedder()
    )
    assert stats.entries_scanned == 2
    assert stats.entries_embedded == 2

    # Idempotent on the second pass.
    stats2 = embed.rebuild_sessions(
        session_library=lib_dir, vectors_dir=vdir, embedder=_fake_embedder()
    )
    assert stats2.entries_embedded == 0
    assert stats2.entries_skipped == 2

    # Editing one file invalidates the whole index.
    (lib_dir / "bike.md").write_text(
        (lib_dir / "bike.md").read_text(encoding="utf-8")
        + "\n### `threshold_bike`\n"
        "- **Purpose:** raise FTP\n"
        "- **Duration:** 75–90 min\n"
        "- **TSS:** 80–110\n"
        "- **Structure:** 3-4x10\n",
        encoding="utf-8",
    )
    stats3 = embed.rebuild_sessions(
        session_library=lib_dir, vectors_dir=vdir, embedder=_fake_embedder()
    )
    assert stats3.entries_embedded == 3
    assert stats3.rows_deleted == 2

    # Sport filter works across files.
    hits = embed.search_sessions(
        "long aerobic ride", sport="bike", k=5,
        vectors_dir=vdir, embedder=_fake_embedder(),
    )
    assert hits
    assert all(h.sport == "bike" for h in hits)


def test_rebuild_sessions_against_real_library() -> None:
    """Sanity check against the real per-sport session library directory.

    Falls back to the legacy monolith path for old checkouts.
    """
    from tempo.paths import repo_root

    lib_dir = repo_root() / "knowledge" / "methodology" / "session-library"
    legacy = repo_root() / "knowledge" / "methodology" / "session-library.md"
    if lib_dir.is_dir():
        lib: Path = lib_dir
    elif legacy.is_file():
        lib = legacy
    else:
        pytest.skip("real library not present in this checkout")
    # Dry parse only — don't write to shared vectors dir.
    entries = list(embed._iter_session_entries(lib))
    assert len(entries) > 10
    # Every entry should have a sport — no "unknown".
    unknowns = [e.id for e in entries if e.sport == "unknown"]
    assert not unknowns, f"sessions missing sport heading: {unknowns}"
