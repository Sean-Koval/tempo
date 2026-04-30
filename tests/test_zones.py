"""Tests for ``tempo.zones`` — threshold provenance + freshness."""

from __future__ import annotations

from datetime import date

from tempo import zones


def test_parse_threshold_scalar_legacy() -> None:
    t = zones.parse_threshold(280)
    assert t.value == 280
    assert t.set_at is None
    assert t.source == "manual_estimate"
    assert t.source_ref is None
    assert t.is_set is True


def test_parse_threshold_blank_scalar() -> None:
    for raw in (None, "", "TODO", "TBD"):
        t = zones.parse_threshold(raw)
        assert t.is_set is False


def test_parse_threshold_struct_full() -> None:
    t = zones.parse_threshold(
        {
            "value": "4:10/km",
            "set_at": "2026-03-15",
            "source": "race_result",
            "source_ref": "2026-03-15-stockholm-half",
        }
    )
    assert t.value == "4:10/km"
    assert t.set_at == date(2026, 3, 15)
    assert t.source == "race_result"
    assert t.source_ref == "2026-03-15-stockholm-half"


def test_parse_threshold_struct_partial_defaults() -> None:
    t = zones.parse_threshold({"value": 165})
    assert t.value == 165
    assert t.set_at is None
    assert t.source == "manual_estimate"


def test_parse_threshold_unknown_source_falls_back() -> None:
    t = zones.parse_threshold({"value": 280, "source": "guesstimate"})
    assert t.source == "manual_estimate"


def test_parse_threshold_set_at_unknown_string() -> None:
    t = zones.parse_threshold({"value": 280, "set_at": "unknown"})
    assert t.set_at is None


def test_parse_threshold_set_at_garbage_returns_none() -> None:
    t = zones.parse_threshold({"value": 280, "set_at": "yesterday"})
    assert t.set_at is None


def test_age_days_with_known_set_at() -> None:
    t = zones.parse_threshold({"value": 280, "set_at": "2026-04-01"})
    assert t.age_days(date(2026, 5, 1)) == 30


def test_age_days_with_unknown_set_at() -> None:
    t = zones.parse_threshold(280)
    assert t.age_days(date(2026, 5, 1)) is None


def test_is_stale_blank_is_not_stale() -> None:
    t = zones.parse_threshold(None)
    assert zones.is_stale("ftp_w", t, today=date(2026, 5, 1)) is False


def test_is_stale_within_window() -> None:
    t = zones.parse_threshold({"value": 280, "set_at": "2026-04-01"})
    assert zones.is_stale("ftp_w", t, today=date(2026, 5, 1)) is False


def test_is_stale_past_window() -> None:
    t = zones.parse_threshold({"value": 280, "set_at": "2025-12-01"})
    assert zones.is_stale("ftp_w", t, today=date(2026, 5, 1)) is True


def test_is_stale_unknown_set_at_treated_as_stale() -> None:
    t = zones.parse_threshold(280)
    assert zones.is_stale("ftp_w", t, today=date(2026, 5, 1)) is True


def test_is_stale_unknown_key_returns_false() -> None:
    t = zones.parse_threshold({"value": 1.0, "set_at": "2020-01-01"})
    assert zones.is_stale("not_a_real_key", t, today=date(2026, 5, 1)) is False


def test_run_pace_window_is_tighter_than_ftp() -> None:
    assert zones.STALE_WINDOWS["run_threshold_pace"] < zones.STALE_WINDOWS["ftp_w"]


def test_threshold_value_helper_unwraps_struct() -> None:
    assert zones.threshold_value({"value": 280}) == 280
    assert zones.threshold_value(280) == 280
    assert zones.threshold_value(None) is None
