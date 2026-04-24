"""Phase 3 milestone verification — exercise every MCP tool end-to-end.

This is the concrete proof that the plan milestone ("agent answers
load/adherence/knowledge questions through a typed MCP surface, no raw SQL
in prompts") holds. Each tool is called via ``mcp.call_tool`` (the same
path Claude Code takes) against a seeded fixture so the test is hermetic.

Run with:

    cd mcp-servers/coach-db && uv run pytest tests/test_milestone.py -v
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from tempo import embed
from tempo.db import init_schema


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


_KNOWLEDGE_DOC = (
    "Polarized training is the 80/20 intensity distribution popularized by "
    "Stephen Seiler. Athletes spend most time in Z1–Z2 with sparse hard efforts "
    "above threshold. The principle targets high aerobic volume without "
    "sympathetic overload. Friel agrees for endurance sports."
)

_NUTRITION_DOC = (
    "Race-day carb intake targets 60 to 90 grams per hour for a gut-trained "
    "Ironman athlete. Fueling starts in the first 20 minutes. Mix sources: "
    "glucose plus fructose for higher absorption."
)

_SESSION_LIBRARY = """# Session Library

## Bike

### `long_ride_z2`
- **Purpose:** aerobic capacity and durability
- **Duration:** 2.5–5 hours
- **TSS:** 120–300
- **Structure:** steady Z2 by HR drift with fueling

### `threshold_bike`
- **Purpose:** raise FTP
- **Duration:** 75–90 min
- **TSS:** 80–110
- **Structure:** 3–4×10min at 95-102%FTP

## Run

### `long_run_z2`
- **Purpose:** running economy under fatigue
- **Duration:** 75–150 min
- **TSS:** 70–150
- **Structure:** Z2 by HR, watch drift
"""


@pytest.fixture
def seeded_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Seed an isolated TEMPO_DATA_DIR with fixture SQL + vector indexes."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("TEMPO_DATA_DIR", str(data_root))

    # tempo.paths.data_dir reads the env each call; clear its cache if any.
    from tempo import paths as tpaths

    tpaths.repo_root.cache_clear()  # type: ignore[attr-defined]

    # Seed coach.db with activities + wellness + load + sessions + adherence.
    db_path = data_root / "coach.db"
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    today = date(2026, 4, 24)
    for i in range(28):
        d = today - timedelta(days=i)
        conn.execute(
            "INSERT INTO wellness_daily (date, sleep_h, hrv, rhr, readiness, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (d.isoformat(), 7.5, 72.0 + (i % 4), 52, 8, "clean" if i == 0 else None),
        )
        conn.execute(
            "INSERT INTO load_daily (date, ctl, atl, tsb, ctl_bike, ctl_run, ctl_swim, ramp_7d) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (d.isoformat(), 72.0 - i * 0.3, 60.0, 12.0, 50.0, 18.0, 4.0, 0.4),
        )
    # Long Z2 ride with excellent drift (the complex filter target).
    conn.execute(
        "INSERT INTO activities "
        "(id, start_date, sport, duration_s, distance_m, tss, decoupling) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("act1", "2026-04-20T07:00:00", "ride", 12600, 140000, 240.0, 3.2),
    )
    # A short ride that should NOT match the complex filter.
    conn.execute(
        "INSERT INTO activities "
        "(id, start_date, sport, duration_s, distance_m, tss, decoupling) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("act2", "2026-04-21T07:00:00", "ride", 4200, 35000, 75.0, 8.5),
    )
    # Planned week + adherence to exercise get_adherence + compare_plan_to_actual.
    conn.execute(
        "INSERT INTO sessions_planned "
        "(id, plan_id, week_id, date, sport, library_ref, target_tss, "
        "target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp-mon", "ironman-lp", "2026-W17", "2026-04-20", "ride",
         "long_ride_z2", 220.0, 12000, "aerobic_base"),
    )
    conn.execute(
        "INSERT INTO sessions_planned "
        "(id, plan_id, week_id, date, sport, library_ref, target_tss, "
        "target_duration_s, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sp-tue", "ironman-lp", "2026-W17", "2026-04-21", "run",
         "long_run_z2", 120.0, 7200, "endurance"),
    )
    conn.execute(
        "INSERT INTO adherence (planned_session_id, activity_id, completed, "
        "tss_delta, duration_delta_s, reason) VALUES (?, ?, ?, ?, ?, ?)",
        ("sp-mon", "act1", 1, 20.0, 600, "completed"),
    )
    # sp-tue intentionally has no adherence row → missed session.
    conn.close()

    # Seed knowledge.lance.
    kroot = tmp_path / "knowledge"
    kroot.mkdir()
    (kroot / "sources.yaml").write_text(
        "\n".join(
            [
                "sources:",
                "  - id: seiler",
                "    name: Stephen Seiler",
                "    type: expert_blog",
                "    credibility: peer_reviewed",
                "    topics: [polarized]",
                "  - id: jeukendrup",
                "    name: Jeukendrup",
                "    type: expert_blog",
                "    credibility: peer_reviewed",
                "    topics: [nutrition]",
            ]
        ),
        encoding="utf-8",
    )
    (kroot / "methodology").mkdir()
    (kroot / "methodology" / "polarized.md").write_text(
        "---\ntopic: polarized\nsources: [seiler]\n---\n\n" + _KNOWLEDGE_DOC,
        encoding="utf-8",
    )
    (kroot / "nutrition").mkdir()
    (kroot / "nutrition" / "race-day.md").write_text(
        "---\ntopic: nutrition\nsources: [jeukendrup]\n---\n\n" + _NUTRITION_DOC,
        encoding="utf-8",
    )
    vdir = data_root / "vectors"
    embed.rebuild(knowledge_root=kroot, vectors_dir=vdir, embedder=_fake_embedder())

    # Seed sessions.lance from a fixture library.
    lib = tmp_path / "session-library.md"
    lib.write_text(_SESSION_LIBRARY, encoding="utf-8")
    embed.rebuild_sessions(session_library=lib, vectors_dir=vdir, embedder=_fake_embedder())

    # Inject the fake embedder into the tool modules so search_* tools stay offline.
    from coach_db_mcp import knowledge as k_mod
    from coach_db_mcp import memory as m_mod
    from coach_db_mcp import sessions as s_mod

    k_mod.set_embedder_override(_fake_embedder())
    m_mod.set_embedder_override(_fake_embedder())
    s_mod.set_embedder_override(_fake_embedder())

    yield {"data_root": data_root, "vdir": vdir}

    k_mod.set_embedder_override(None)
    m_mod.set_embedder_override(None)
    s_mod.set_embedder_override(None)


async def _call(name: str, args: dict):
    from coach_db_mcp.server import mcp

    result = await mcp.call_tool(name, args)
    return result.structured_content


# ---------------------------------------------------------------------------
# Milestone checks — each tool once, end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_1_query_activities_complex_filter(seeded_world) -> None:
    payload = await _call(
        "query_activities",
        {
            "sport": "ride",
            "min_duration_s": 10800,
            "max_decoupling_pct": 5.0,
        },
    )
    items = payload["result"] if "result" in payload else payload
    ids = {a["id"] for a in items}
    assert "act1" in ids, "long Z2 ride should match the filter"
    assert "act2" not in ids, "short high-drift ride must be excluded"


@pytest.mark.asyncio
async def test_2_get_load_curve(seeded_world) -> None:
    payload = await _call(
        "get_load_curve",
        {"start_date": "2026-04-01", "end_date": "2026-04-24"},
    )
    points = payload["result"] if "result" in payload else payload
    assert len(points) >= 20
    assert all("ctl" in p for p in points)


@pytest.mark.asyncio
async def test_3_get_readiness(seeded_world) -> None:
    snap = await _call("get_readiness", {"as_of": "2026-04-24", "window_days": 14})
    assert snap["samples"] > 0
    assert snap["hrv_latest"] is not None
    assert snap["notes_latest"] == "clean"


@pytest.mark.asyncio
async def test_4_get_adherence(seeded_world) -> None:
    rep = await _call("get_adherence", {"week_id": "2026-W17"})
    assert rep["planned_count"] == 2
    assert rep["completed_count"] == 1
    assert rep["completion_pct"] == pytest.approx(50.0, abs=0.1)


@pytest.mark.asyncio
async def test_5_compare_plan_to_actual(seeded_world) -> None:
    payload = await _call("compare_plan_to_actual", {"week_id": "2026-W17"})
    deltas = payload["result"] if "result" in payload else payload
    assert len(deltas) == 2
    by_id = {d["planned_session_id"]: d for d in deltas}
    assert by_id["sp-mon"]["tss_delta"] == pytest.approx(20.0)
    assert by_id["sp-tue"]["activity_id"] is None  # missed session surfaces


@pytest.mark.asyncio
async def test_6_search_knowledge(seeded_world) -> None:
    payload = await _call(
        "search_knowledge",
        {"query": "polarized training 80/20 intensity distribution Seiler", "k": 3},
    )
    hits = payload["result"] if "result" in payload else payload
    assert hits
    assert hits[0]["path"].endswith("polarized.md")
    assert "seiler" in hits[0]["source_ids"]
    assert hits[0]["credibility"] == "peer_reviewed"


@pytest.mark.asyncio
async def test_7_log_decision_then_search_memory(seeded_world) -> None:
    logged = await _call(
        "log_decision",
        {
            "scope": "week:2026-W17",
            "kind": "adjust",
            "rationale": "Cut Tuesday long run — HRV dropped two sigma this morning.",
            "changed_files": ["plans/ironman-lp/weeks/2026-W17.md"],
        },
    )
    assert logged["id"] > 0
    assert logged["embedded"] is True

    payload = await _call(
        "search_memory",
        {"query": "cut Tuesday long run HRV drop", "k": 3},
    )
    hits = payload["result"] if "result" in payload else payload
    assert hits
    assert hits[0]["source"] == "decision"
    assert hits[0]["scope"] == "week:2026-W17"


@pytest.mark.asyncio
async def test_8_find_similar_session(seeded_world) -> None:
    payload = await _call(
        "find_similar_session",
        {
            "description": "2.5 hour steady aerobic ride Z2 with fueling",
            "sport": "bike",
            "k": 3,
        },
    )
    hits = payload["result"] if "result" in payload else payload
    assert hits
    ids = [h["id"] for h in hits]
    assert "long_ride_z2" in ids


@pytest.mark.asyncio
async def test_9_all_tools_registered(seeded_world) -> None:
    from coach_db_mcp.server import mcp

    names = {t.name for t in await mcp.list_tools()}
    expected = {
        "ping",
        "query_activities",
        "get_load_curve",
        "get_readiness",
        "get_adherence",
        "compare_plan_to_actual",
        "search_knowledge",
        "search_memory",
        "log_decision",
        "find_similar_session",
    }
    assert expected <= names
