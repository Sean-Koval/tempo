"""Tests for tempo.check_in — morning wellness capture."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempo import check_in as ci


def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))


def test_to_intervals_payload_maps_known_fields() -> None:
    data = ci.CheckInInput(
        for_date="2026-04-24",
        sleep_h=7.5,
        sleep_score=86,
        hrv=62.4,
        rhr=48,
        readiness=8,
        body_weight_kg=75.2,
        soreness="3",
        notes="felt great",
    )
    payload = ci.to_intervals_payload(data)
    assert payload["id"] == "2026-04-24"
    assert payload["sleepSecs"] == int(7.5 * 3600)
    assert payload["sleepScore"] == 86
    assert payload["hrv"] == pytest.approx(62.4)
    assert payload["restingHR"] == 48
    assert payload["readiness"] == 8
    assert payload["weight"] == pytest.approx(75.2)
    assert payload["soreness"] == 3
    assert payload["comments"] == "felt great"


def test_to_intervals_payload_skips_none_fields() -> None:
    data = ci.CheckInInput(for_date="2026-04-24", readiness=6)
    payload = ci.to_intervals_payload(data)
    # Only id and readiness set.
    assert set(payload.keys()) == {"id", "readiness"}


def test_to_intervals_payload_soreness_text_dropped() -> None:
    # Free-text soreness stays in coach.db only — intervals expects int.
    data = ci.CheckInInput(for_date="2026-04-24", soreness="right calf tight")
    payload = ci.to_intervals_payload(data)
    assert "soreness" not in payload


def test_check_in_writes_to_db_and_skips_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_db(monkeypatch, tmp_path)
    data = ci.CheckInInput(
        for_date="2026-04-24", sleep_h=7.2, hrv=65.0, rhr=50, readiness=8
    )
    result = ci.check_in(data, push=False)
    assert result.db_written
    assert not result.intervals_pushed
    assert result.intervals_error is None

    from tempo.db import connect

    conn = connect()
    try:
        row = conn.execute(
            "SELECT sleep_h, hrv, rhr, readiness FROM wellness_daily WHERE date = ?",
            ("2026-04-24",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["sleep_h"] == pytest.approx(7.2)
    assert row["hrv"] == pytest.approx(65.0)
    assert row["rhr"] == 50
    assert row["readiness"] == 8


def test_check_in_is_upsert_on_same_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_db(monkeypatch, tmp_path)
    ci.check_in(ci.CheckInInput(for_date="2026-04-24", sleep_h=6.5, readiness=5), push=False)
    ci.check_in(ci.CheckInInput(for_date="2026-04-24", sleep_h=7.8, readiness=8), push=False)

    from tempo.db import connect

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT sleep_h, readiness FROM wellness_daily WHERE date = ?",
            ("2026-04-24",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["sleep_h"] == pytest.approx(7.8)
    assert rows[0]["readiness"] == 8


def test_check_in_captures_intervals_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing intervals push must not roll back the DB write."""
    _isolate_db(monkeypatch, tmp_path)

    async def _boom(d: ci.CheckInInput) -> None:
        raise RuntimeError("no creds")

    monkeypatch.setattr(ci, "_push_to_intervals", _boom)

    data = ci.CheckInInput(for_date="2026-04-24", sleep_h=7.0, readiness=7)
    result = ci.check_in(data, push=True)
    assert result.db_written
    assert not result.intervals_pushed
    assert result.intervals_error == "no creds"

    from tempo.db import connect

    conn = connect()
    try:
        row = conn.execute(
            "SELECT sleep_h FROM wellness_daily WHERE date = ?", ("2026-04-24",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["sleep_h"] == pytest.approx(7.0)


def test_check_in_push_success_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_db(monkeypatch, tmp_path)
    calls: list[ci.CheckInInput] = []

    async def _recorder(d: ci.CheckInInput) -> None:
        calls.append(d)

    monkeypatch.setattr(ci, "_push_to_intervals", _recorder)

    data = ci.CheckInInput(for_date="2026-04-24", sleep_h=7.0, readiness=7)
    result = ci.check_in(data, push=True)
    assert result.intervals_pushed
    assert result.intervals_error is None
    assert len(calls) == 1
    assert calls[0].for_date == "2026-04-24"
