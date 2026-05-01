"""Tests for ``tempo.profile_init`` — intervals → athlete/profile.yaml seed.

Mocks both the ICUClient (via injected ``fetch_facts``) and the
interactive prompt, so nothing here touches the network or stdin.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

import yaml
from intervals_icu_mcp.auth import ICUConfig

from tempo.profile_init import (
    _format_run_pace,
    _format_swim_pace,
    _IntervalsFacts,
    init_profile,
    render_summary_rows,
)

_STUB_PROFILE = """\
athlete:
  name: Sean Koval
  dob:
  weight_kg:
  height_cm:

thresholds:
  ftp_w:
    value:
    set_at:
    source: manual_estimate
    source_ref:
  lthr_bpm:
    value:
    set_at:
    source: manual_estimate
    source_ref:
  run_threshold_pace:
    value:
    set_at:
    source: manual_estimate
    source_ref:
  swim_css_pace:
    value:
    set_at:
    source: manual_estimate
    source_ref:
  max_hr:
    value:
    set_at:
    source: manual_estimate
    source_ref:
  resting_hr:

zones:
  bike_power:
    z1_active_recovery: [0, 55]
    z2_endurance: [56, 75]
    z3_tempo: [76, 90]
    z4_lactate_threshold: [91, 105]
    z5_vo2max: [106, 120]
    z6_anaerobic: [121, 150]
    z7_neuromuscular: [151, 200]
  run_hr:
    z1: [0, 85]
    z2: [85, 89]
    z3: [90, 94]
    z4: [95, 99]
    z5a: [100, 102]
    z5b: [103, 106]
    z5c: [107, 200]

prs:
  5k_run:

strengths: []
limiters: []
"""


def _seed_profile(tmp_path: Path, content: str = _STUB_PROFILE) -> Path:
    (tmp_path / "athlete").mkdir(parents=True, exist_ok=True)
    p = tmp_path / "athlete" / "profile.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _config() -> ICUConfig:
    return ICUConfig(intervals_icu_athlete_id="i42", intervals_icu_api_key="secret")


def _facts(**overrides) -> _IntervalsFacts:
    base = _IntervalsFacts(
        athlete_id="i42",
        name="Sean Koval",
        dob="1990-06-15",
        weight_kg=72.5,
        height_cm=178,
        resting_hr=48,
        bike_ftp=285,
        bike_lthr=164,
        bike_max_hr=190,
        bike_power_zones=[55, 75, 90, 105, 120, 150, 200],
        run_threshold_pace=4.25,
        run_lthr=170,
        run_max_hr=192,
        run_hr_zones=[85, 89, 94, 99, 102, 106, 200],
        swim_threshold_pace=98.0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ---------------------------------------------------------------------------
# Pace formatting
# ---------------------------------------------------------------------------


def test_format_run_pace_min_per_km():
    assert _format_run_pace(4.25) == "4:15/km"


def test_format_swim_pace_sec_per_100m():
    assert _format_swim_pace(98.0) == "1:38/100m"


# ---------------------------------------------------------------------------
# Creds gate
# ---------------------------------------------------------------------------


def test_missing_creds_returns_creds_missing(tmp_path: Path):
    _seed_profile(tmp_path)
    result = init_profile(
        config=ICUConfig(intervals_icu_api_key="", intervals_icu_athlete_id=""),
        root=tmp_path,
        prompt_gaps=False,
        fetch_facts=lambda c: _facts(),
    )
    assert result.creds_missing is True
    assert "tempo-b7z" in result.creds_message


def test_placeholder_creds_treated_as_missing(tmp_path: Path):
    _seed_profile(tmp_path)
    result = init_profile(
        config=ICUConfig(
            intervals_icu_api_key="your_api_key_here",
            intervals_icu_athlete_id="i123456",
        ),
        root=tmp_path,
        prompt_gaps=False,
        fetch_facts=lambda c: _facts(),
    )
    assert result.creds_missing is True


# ---------------------------------------------------------------------------
# Stub population
# ---------------------------------------------------------------------------


def test_populates_stub_with_intervals_data(tmp_path: Path):
    p = _seed_profile(tmp_path)
    result = init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(),
    )

    assert result.changed
    doc = yaml.safe_load(p.read_text())

    assert doc["athlete"]["dob"] == "1990-06-15"
    assert doc["athlete"]["weight_kg"] == 72.5
    assert doc["athlete"]["height_cm"] == 178

    ftp = doc["thresholds"]["ftp_w"]
    assert ftp["value"] == 285
    assert ftp["set_at"] == "2026-04-30"
    assert ftp["source"] == "intervals_import"
    assert ftp["source_ref"] == "intervals.icu/athlete/i42"

    assert doc["thresholds"]["resting_hr"] == 48
    assert doc["thresholds"]["lthr_bpm"]["value"] == 164
    assert doc["thresholds"]["max_hr"]["value"] == 190
    assert doc["thresholds"]["run_threshold_pace"]["value"] == "4:15/km"
    assert doc["thresholds"]["swim_css_pace"]["value"] == "1:38/100m"


def test_populates_zones_when_default_scaffold(tmp_path: Path):
    p = _seed_profile(tmp_path)
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(),
    )
    doc = yaml.safe_load(p.read_text())
    bp = doc["zones"]["bike_power"]
    # Intervals cutoffs [55,75,90,105,120,150,200] → ranges starting at 0 / prev_hi+1.
    assert bp["z1_active_recovery"] == [0, 55]
    assert bp["z2_endurance"] == [56, 75]
    assert bp["z7_neuromuscular"] == [151, 200]


# ---------------------------------------------------------------------------
# Existing-value preservation
# ---------------------------------------------------------------------------


_FILLED_PROFILE = """\
athlete:
  name: Sean Koval
  dob: '1985-01-01'
  weight_kg: 70.0
  height_cm: 175

thresholds:
  ftp_w:
    value: 300
    set_at: '2026-01-01'
    source: field_test
    source_ref: '2026-W01-test'
  lthr_bpm:
    value: 170
    set_at: '2026-01-01'
    source: field_test
    source_ref: ref
  run_threshold_pace:
    value: 4:00/km
    set_at: '2026-01-01'
    source: race_result
    source_ref: ref
  swim_css_pace:
    value: 1:30/100m
    set_at: '2026-01-01'
    source: field_test
    source_ref: ref
  max_hr:
    value: 195
    set_at: '2026-01-01'
    source: field_test
    source_ref: ref
  resting_hr: 45

zones:
  bike_power:
    z1_active_recovery: [0, 55]
    z2_endurance: [56, 75]
    z3_tempo: [76, 90]
    z4_lactate_threshold: [91, 105]
    z5_vo2max: [106, 120]
    z6_anaerobic: [121, 150]
    z7_neuromuscular: [151, 200]
  run_hr:
    z1: [0, 85]
    z2: [85, 89]
    z3: [90, 94]
    z4: [95, 99]
    z5a: [100, 102]
    z5b: [103, 106]
    z5c: [107, 200]
"""


def test_existing_values_preserved_without_force(tmp_path: Path):
    p = _seed_profile(tmp_path, _FILLED_PROFILE)
    result = init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(),
    )
    doc = yaml.safe_load(p.read_text())
    assert doc["thresholds"]["ftp_w"]["value"] == 300
    assert doc["thresholds"]["resting_hr"] == 45
    assert doc["athlete"]["weight_kg"] == 70.0
    skipped_paths = {u.path for u in result.skipped()}
    assert "thresholds.ftp_w" in skipped_paths
    assert "athlete.weight_kg" in skipped_paths


def test_force_overwrites_existing_values(tmp_path: Path):
    p = _seed_profile(tmp_path, _FILLED_PROFILE)
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        force=True,
        fetch_facts=lambda c: _facts(),
    )
    doc = yaml.safe_load(p.read_text())
    assert doc["thresholds"]["ftp_w"]["value"] == 285
    assert doc["thresholds"]["ftp_w"]["set_at"] == "2026-04-30"
    assert doc["thresholds"]["ftp_w"]["source"] == "intervals_import"
    assert doc["athlete"]["weight_kg"] == 72.5


# ---------------------------------------------------------------------------
# Idempotency — re-running with no upstream change is a no-op
# ---------------------------------------------------------------------------


def test_rerun_is_byte_identical(tmp_path: Path):
    p = _seed_profile(tmp_path)
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(),
    )
    text_after_first = p.read_text(encoding="utf-8")

    result2 = init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(),
    )
    text_after_second = p.read_text(encoding="utf-8")

    assert text_after_first == text_after_second
    assert result2.changed is False
    assert all(u.action == "skipped_existing" for u in result2.updates)


# ---------------------------------------------------------------------------
# Prompt path
# ---------------------------------------------------------------------------


def test_gap_prompt_populates_swim_css_when_intervals_missing_it(tmp_path: Path):
    _seed_profile(tmp_path)
    answers = iter(["1:42/100m", "19:30", "", "", "", "endurance, pacing", ""])
    p = tmp_path / "athlete" / "profile.yaml"
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=True,
        today=_date(2026, 4, 30),
        prompt_fn=lambda label: next(answers),
        fetch_facts=lambda c: _facts(swim_threshold_pace=None),
    )
    doc = yaml.safe_load(p.read_text())
    assert doc["thresholds"]["swim_css_pace"]["value"] == "1:42/100m"
    assert doc["thresholds"]["swim_css_pace"]["source"] == "manual_estimate"
    assert doc["prs"]["5k_run"] == "19:30"
    assert doc["strengths"] == ["endurance", "pacing"]
    assert doc["limiters"] == []


def test_gap_prompt_skipped_on_blank_input(tmp_path: Path):
    p = _seed_profile(tmp_path)
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=True,
        today=_date(2026, 4, 30),
        prompt_fn=lambda label: "",
        fetch_facts=lambda c: _facts(swim_threshold_pace=None),
    )
    doc = yaml.safe_load(p.read_text())
    # swim_css_pace remains unpopulated since intervals didn't have it and
    # the user blanked the prompt.
    assert doc["thresholds"]["swim_css_pace"]["value"] in (None, "", "TODO")


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def test_summary_rows_include_status_and_value(tmp_path: Path):
    _seed_profile(tmp_path)
    result = init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(),
    )
    rows = render_summary_rows(result)
    statuses = {r[0] for r in rows}
    fields = {r[1] for r in rows}
    assert "set" in statuses
    assert "thresholds.ftp_w" in fields
    ftp_row = next(r for r in rows if r[1] == "thresholds.ftp_w")
    assert ftp_row[2] == "285"
    assert ftp_row[3] == "intervals_import"


# ---------------------------------------------------------------------------
# Partial intervals data
# ---------------------------------------------------------------------------


def test_zones_left_alone_when_intervals_returns_none(tmp_path: Path):
    p = _seed_profile(tmp_path)
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(bike_power_zones=None, run_hr_zones=None),
    )
    doc = yaml.safe_load(p.read_text())
    # Original Coggan defaults survive untouched.
    assert doc["zones"]["bike_power"]["z2_endurance"] == [56, 75]


def test_run_only_lthr_used_when_bike_lacks_it(tmp_path: Path):
    p = _seed_profile(tmp_path)
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(bike_lthr=None),
    )
    doc = yaml.safe_load(p.read_text())
    assert doc["thresholds"]["lthr_bpm"]["value"] == 170  # from run


# ---------------------------------------------------------------------------
# Custom zones preserved
# ---------------------------------------------------------------------------


def test_custom_zones_preserved_without_force(tmp_path: Path):
    custom = _STUB_PROFILE.replace(
        "z2_endurance: [56, 75]", "z2_endurance: [60, 80]"
    )
    p = _seed_profile(tmp_path, custom)
    init_profile(
        config=_config(),
        root=tmp_path,
        prompt_gaps=False,
        today=_date(2026, 4, 30),
        fetch_facts=lambda c: _facts(),
    )
    doc = yaml.safe_load(p.read_text())
    # User customization survives — intervals zones never landed.
    assert doc["zones"]["bike_power"]["z2_endurance"] == [60, 80]
