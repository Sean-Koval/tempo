"""Threshold provenance — when each zone was set, and how.

Story 05 §3 motivates this: ``athlete/profile.yaml`` historically stored
FTP/LTHR/run pace/CSS as bare scalars, so a 5-month-old half-marathon
threshold looks identical to one set yesterday. The agent silently emits
stale zones into draft sessions.

Thresholds in ``profile.yaml`` may be either:

* A bare scalar — interpreted as ``{value: <scalar>, set_at: None,
  source: manual_estimate}``. Treated as maximally stale for freshness
  debt detection so legacy profiles surface correctly on the macro
  dashboard.
* A struct ``{value, set_at: YYYY-MM-DD, source, source_ref?}``.

Source must be one of ``race_result | field_test | manual_estimate |
physiology_lab``; the parser is forgiving — unknown sources fall back to
``manual_estimate`` rather than raising, since the calibration debt
panel is a hint surface, not a load-bearing validator.

Freshness windows are sport-specific:

* ``ftp_w`` — 120 days (bike adapts slowly; FTP test is costly)
* ``lthr_bpm`` — 180 days (HR drift is gradual)
* ``run_threshold_pace`` — 90 days (run fitness moves fastest)
* ``swim_css_pace`` — 180 days (low-volume swim adapts slowly)
* ``max_hr`` — 365 days (essentially fixed; revisit annually)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

Source = Literal["race_result", "field_test", "manual_estimate", "physiology_lab"]

_VALID_SOURCES: frozenset[str] = frozenset(
    {"race_result", "field_test", "manual_estimate", "physiology_lab"}
)

_PLACEHOLDER_VALUES: frozenset[Any] = frozenset({None, "", "TODO", "TBD"})

STALE_WINDOWS: dict[str, int] = {
    "ftp_w": 120,
    "lthr_bpm": 180,
    "run_threshold_pace": 90,
    "swim_css_pace": 180,
    "max_hr": 365,
}

SUGGESTED_TEST: dict[str, str] = {
    "ftp_w": "20-min FTP test or ramp test",
    "lthr_bpm": "30/20 LTHR test or threshold race effort",
    "run_threshold_pace": "5K or 3K run time-trial",
    "swim_css_pace": "400+200 CSS test",
    "max_hr": "all-out hill repeats or end-of-VO2 race effort",
}


@dataclass(frozen=True)
class Threshold:
    """A single zone-defining threshold with provenance.

    ``value`` is None when the field is blank/placeholder. ``set_at`` is
    None when the user hasn't recorded a date (or when the value comes
    from a legacy scalar entry).
    """

    value: Any
    set_at: date | None
    source: Source
    source_ref: str | None = None

    @property
    def is_set(self) -> bool:
        return not _is_blank(self.value)

    def age_days(self, today: date) -> int | None:
        """Days since ``set_at``; None if the date is unknown."""
        if self.set_at is None:
            return None
        return (today - self.set_at).days


def _is_blank(value: Any) -> bool:
    if value in _PLACEHOLDER_VALUES:
        return True
    if isinstance(value, str) and value.strip().upper() in {"TODO", "TBD", ""}:
        return True
    return False


def _coerce_date(raw: Any) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() == "unknown":
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    return None


def _coerce_source(raw: Any) -> Source:
    if isinstance(raw, str) and raw in _VALID_SOURCES:
        return raw  # type: ignore[return-value]
    return "manual_estimate"


def parse_threshold(raw: Any) -> Threshold:
    """Normalize a profile.yaml threshold entry.

    Accepts either a bare scalar (legacy shape) or a struct with
    ``value/set_at/source/source_ref`` keys. Anything unparseable
    decays to a blank ``manual_estimate``-sourced entry rather than
    raising — calibration debt is the right place to flag bad data,
    not the parser.
    """
    if isinstance(raw, dict):
        return Threshold(
            value=raw.get("value"),
            set_at=_coerce_date(raw.get("set_at")),
            source=_coerce_source(raw.get("source")),
            source_ref=raw.get("source_ref") if isinstance(raw.get("source_ref"), str) else None,
        )
    return Threshold(
        value=raw,
        set_at=None,
        source="manual_estimate",
        source_ref=None,
    )


def threshold_value(raw: Any) -> Any:
    """Extract just the underlying numeric/string value, scalar or struct."""
    return parse_threshold(raw).value


def is_stale(key: str, threshold: Threshold, *, today: date) -> bool:
    """True when a populated threshold is older than its sport's window.

    Blank thresholds are not stale (they're a different debt category —
    "not set"). Thresholds with no ``set_at`` are treated as stale once
    the freshness window exists for that key, since "unknown when set"
    is the same risk as "set too long ago".
    """
    if not threshold.is_set:
        return False
    window = STALE_WINDOWS.get(key)
    if window is None:
        return False
    if threshold.set_at is None:
        return True
    return (today - threshold.set_at).days > window


__all__ = [
    "STALE_WINDOWS",
    "SUGGESTED_TEST",
    "Source",
    "Threshold",
    "is_stale",
    "parse_threshold",
    "threshold_value",
]
