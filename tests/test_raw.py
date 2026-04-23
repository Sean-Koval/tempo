"""Tests for the append-only JSONL audit trail."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tempo.raw import append_raw


def test_writes_single_line(tmp_data_dir: Path):
    ts = datetime(2026, 4, 23, 12, 30, tzinfo=UTC)
    path = append_raw(
        "intervals",
        "/activities",
        response=[{"id": "a1", "distance": 1000}],
        params={"oldest": "2026-04-16"},
        now=ts,
    )

    assert path == tmp_data_dir / "raw" / "intervals" / "2026-04-23.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["source"] == "intervals"
    assert record["endpoint"] == "/activities"
    assert record["params"] == {"oldest": "2026-04-16"}
    assert record["response"] == [{"id": "a1", "distance": 1000}]
    assert record["ts"].startswith("2026-04-23T12:30:00")


def test_appends_multiple_lines_same_day(tmp_data_dir: Path):
    ts = datetime(2026, 4, 23, tzinfo=UTC)
    append_raw("intervals", "/activities", [{"id": "a1"}], now=ts)
    append_raw("intervals", "/wellness", [{"id": "2026-04-23"}], now=ts)

    path = tmp_data_dir / "raw" / "intervals" / "2026-04-23.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["endpoint"] == "/activities"
    assert json.loads(lines[1])["endpoint"] == "/wellness"


def test_separates_sources(tmp_data_dir: Path):
    ts = datetime(2026, 4, 23, tzinfo=UTC)
    append_raw("intervals", "/x", [], now=ts)
    append_raw("strava", "/x", [], now=ts)

    assert (tmp_data_dir / "raw" / "intervals" / "2026-04-23.jsonl").exists()
    assert (tmp_data_dir / "raw" / "strava" / "2026-04-23.jsonl").exists()


def test_rejects_embedded_newline(tmp_data_dir: Path):
    # Build a payload whose JSON serialization *would* have to contain an escaped
    # newline — but json.dumps escapes them, so this path is defensive. Simulate
    # by monkeypatch if needed; here we check the invariant holds for normal input.
    ts = datetime(2026, 4, 23, tzinfo=UTC)
    path = append_raw("intervals", "/x", {"note": "line1\nline2"}, now=ts)
    # Embedded newlines in string values get escaped by json.dumps, so this is fine.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["response"]["note"] == "line1\nline2"


def test_serialises_non_json_fallback(tmp_data_dir: Path):
    """Unknown types (e.g. Path) go through default=str rather than exploding."""
    ts = datetime(2026, 4, 23, tzinfo=UTC)
    path = append_raw("intervals", "/x", {"path": Path("/tmp/x")}, now=ts)
    line = json.loads(path.read_text(encoding="utf-8"))
    assert line["response"]["path"] == "/tmp/x"


def test_default_timestamp(tmp_data_dir: Path):
    before = datetime.now(UTC)
    path = append_raw("intervals", "/x", {})
    after = datetime.now(UTC)
    record = json.loads(path.read_text(encoding="utf-8"))
    ts = datetime.fromisoformat(record["ts"])
    assert before <= ts <= after


def test_newline_guard(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Defensive guard fires if a custom serializer ever produces a raw newline."""
    import tempo.raw as raw_mod

    monkeypatch.setattr(raw_mod.json, "dumps", lambda *a, **kw: "line1\nline2")
    with pytest.raises(ValueError, match="newline"):
        append_raw("intervals", "/x", {})
