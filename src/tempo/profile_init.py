"""``coach init-profile`` — auto-seed ``athlete/profile.yaml`` from intervals.icu.

Cold-start friction is the biggest UX cliff: a freshly-cloned Tempo can't
draft a session until ``athlete/profile.yaml`` has FTP / LTHR / pace
thresholds. Intervals.icu already knows most of that data; this module
fetches it once, merges into the local file with provenance, and prompts
for the rest.

The intervals call uses :class:`ICUClient` in-process rather than the
``intervals`` MCP because (a) the CLI shouldn't depend on a running MCP
server and (b) we need raw athlete + sport-settings JSON — the upstream
Pydantic models drop most fields (e.g. ``icu_resting_hr``,
``icu_date_of_birth``, ``hr_zones``, ``power_zones``). ``push.py`` makes
the same call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any

import yaml
from intervals_icu_mcp.auth import ICUConfig, load_config
from intervals_icu_mcp.client import ICUClient

from .athlete import athlete_dir
from .zones import _is_blank as _is_blank_value  # type: ignore[attr-defined]

# Coggan default %FTP zones — present in the stub profile.yaml. We treat
# these as "no real data" so an intervals zone payload can overwrite them
# without --force. Anything else (custom user zones) is preserved.
_DEFAULT_BIKE_POWER_ZONES: dict[str, list[int]] = {
    "z1_active_recovery": [0, 55],
    "z2_endurance": [56, 75],
    "z3_tempo": [76, 90],
    "z4_lactate_threshold": [91, 105],
    "z5_vo2max": [106, 120],
    "z6_anaerobic": [121, 150],
    "z7_neuromuscular": [151, 200],
}
_DEFAULT_RUN_HR_ZONES: dict[str, list[int]] = {
    "z1": [0, 85],
    "z2": [85, 89],
    "z3": [90, 94],
    "z4": [95, 99],
    "z5a": [100, 102],
    "z5b": [103, 106],
    "z5c": [107, 200],
}

# What sport-settings type strings count as which sport. Intervals tags
# settings with `types: ["Ride", "VirtualRide", ...]`; we pick the first
# match for each canonical sport.
_BIKE_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide"}
_RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
_SWIM_TYPES = {"Swim"}

# Floors that distinguish intervals' unset/sentinel pace values (often 0
# or 1) from real configured ones. World-record 50m freestyle is ~21s/100m
# for a single 50, so sustained sub-30s/100m is implausible. 2 min/km is
# faster than world-record marathon pace.
_SWIM_PACE_SANITY_FLOOR_SEC_PER_100M = 30.0
_RUN_PACE_SANITY_FLOOR_MIN_PER_KM = 2.0


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FieldUpdate:
    """One field that init_profile populated, skipped, or left alone."""

    path: str  # dotted path inside profile.yaml, e.g. "thresholds.ftp_w"
    action: str  # "populated" | "skipped_existing" | "overwritten" | "prompted" | "no_data" | "skipped_unset_sentinel"
    value: Any = None
    source: str = ""  # e.g. "intervals_import", "manual", "default"
    note: str = ""  # human-readable detail (e.g. why a value was skipped)


@dataclass(slots=True)
class InitProfileResult:
    """Outcome of an ``init_profile`` call."""

    profile_path: Path
    changed: bool = False
    updates: list[FieldUpdate] = field(default_factory=list)
    creds_missing: bool = False
    creds_message: str = ""

    def populated(self) -> list[FieldUpdate]:
        return [u for u in self.updates if u.action in {"populated", "overwritten"}]

    def skipped(self) -> list[FieldUpdate]:
        return [u for u in self.updates if u.action == "skipped_existing"]


# ---------------------------------------------------------------------------
# Intervals fetch
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _IntervalsFacts:
    """Subset of intervals payloads relevant to athlete/profile.yaml."""

    athlete_id: str
    name: str | None = None
    dob: str | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    resting_hr: int | None = None
    bike_ftp: int | None = None
    bike_lthr: int | None = None
    bike_max_hr: int | None = None
    bike_power_zones: list[int] | None = None  # cumulative %FTP cutoffs
    run_threshold_pace: float | None = None  # min/km
    run_lthr: int | None = None
    run_max_hr: int | None = None
    run_hr_zones: list[int] | None = None  # cumulative %LTHR cutoffs
    swim_threshold_pace: float | None = None  # sec/100m


async def fetch_intervals_facts(config: ICUConfig) -> _IntervalsFacts:
    """Pull athlete + sport-settings raw JSON; flatten to fields we care about.

    Uses ``client._request`` to bypass the upstream Pydantic models, which
    strip most of the fields we want. The raw JSON is the source of truth.
    """
    async with ICUClient(config) as client:
        athlete_resp = await client._request("GET", f"/athlete/{config.intervals_icu_athlete_id}")
        athlete_raw: dict[str, Any] = athlete_resp.json()

        settings_resp = await client._request(
            "GET", f"/athlete/{config.intervals_icu_athlete_id}/sport-settings"
        )
        settings_raw: list[dict[str, Any]] = settings_resp.json() or []

    facts = _IntervalsFacts(athlete_id=config.intervals_icu_athlete_id)
    facts.name = athlete_raw.get("name")
    facts.dob = athlete_raw.get("icu_date_of_birth") or athlete_raw.get("dob")
    weight = athlete_raw.get("icu_weight") or athlete_raw.get("weight")
    if isinstance(weight, (int, float)) and weight > 0:
        facts.weight_kg = float(weight)
    height = athlete_raw.get("height")
    if isinstance(height, (int, float)) and height > 0:
        # intervals returns meters; profile.yaml is cm.
        facts.height_cm = float(height) * 100.0 if height < 5 else float(height)
    rhr = athlete_raw.get("icu_resting_hr")
    if isinstance(rhr, int) and rhr > 0:
        facts.resting_hr = rhr

    for s in settings_raw:
        types = set(s.get("types") or [])
        if types & _BIKE_TYPES:
            if facts.bike_ftp is None and isinstance(s.get("ftp"), int):
                facts.bike_ftp = s["ftp"]
            if facts.bike_lthr is None and isinstance(s.get("lthr"), int):
                facts.bike_lthr = s["lthr"]
            if facts.bike_max_hr is None and isinstance(s.get("max_hr"), int):
                facts.bike_max_hr = s["max_hr"]
            if facts.bike_power_zones is None:
                pz = s.get("power_zones")
                if isinstance(pz, list) and pz and all(isinstance(x, (int, float)) for x in pz):
                    facts.bike_power_zones = [int(x) for x in pz]
        if types & _RUN_TYPES:
            if facts.run_threshold_pace is None and isinstance(
                s.get("threshold_pace"), (int, float)
            ):
                facts.run_threshold_pace = float(s["threshold_pace"])
            if facts.run_lthr is None and isinstance(s.get("lthr"), int):
                facts.run_lthr = s["lthr"]
            if facts.run_max_hr is None and isinstance(s.get("max_hr"), int):
                facts.run_max_hr = s["max_hr"]
            if facts.run_hr_zones is None:
                hz = s.get("hr_zones")
                if isinstance(hz, list) and hz and all(isinstance(x, (int, float)) for x in hz):
                    facts.run_hr_zones = [int(x) for x in hz]
        if types & _SWIM_TYPES:
            if facts.swim_threshold_pace is None and isinstance(
                s.get("threshold_pace"), (int, float)
            ):
                facts.swim_threshold_pace = float(s["threshold_pace"])

    return facts


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _has_real_value(threshold: Any) -> bool:
    """True when a profile.yaml threshold entry already carries a value."""
    if isinstance(threshold, dict):
        return not _is_blank_value(threshold.get("value"))
    return not _is_blank_value(threshold)


def _format_run_pace(min_per_km: float) -> str:
    """``4.25`` → ``"4:15/km"``."""
    total_secs = int(round(min_per_km * 60.0))
    return f"{total_secs // 60}:{total_secs % 60:02d}/km"


def _format_swim_pace(sec_per_100m: float) -> str:
    """``98.0`` → ``"1:38/100m"``. Intervals returns sec/100m for swim."""
    s = int(round(sec_per_100m))
    return f"{s // 60}:{s % 60:02d}/100m"


def _set_threshold(
    profile: dict[str, Any],
    *,
    key: str,
    value: Any,
    source_ref: str,
    today_iso: str,
    force: bool,
    updates: list[FieldUpdate],
    source_label: str = "intervals_import",
) -> None:
    """Populate one ``thresholds.<key>`` entry in-place with provenance."""
    thresholds = profile.setdefault("thresholds", {})
    existing = thresholds.get(key)
    has_value = _has_real_value(existing)

    if has_value and not force:
        updates.append(
            FieldUpdate(path=f"thresholds.{key}", action="skipped_existing", value=existing)
        )
        return

    new_entry = {
        "value": value,
        "set_at": today_iso,
        "source": source_label,
        "source_ref": source_ref,
    }
    if existing == new_entry:
        return  # no-op — exact same payload already present
    thresholds[key] = new_entry
    action = "overwritten" if has_value else "populated"
    updates.append(
        FieldUpdate(path=f"thresholds.{key}", action=action, value=value, source=source_label)
    )


def _set_athlete_field(
    profile: dict[str, Any],
    *,
    key: str,
    value: Any,
    force: bool,
    updates: list[FieldUpdate],
) -> None:
    section = profile.setdefault("athlete", {})
    existing = section.get(key)
    has_value = not _is_blank_value(existing)
    if has_value and not force:
        updates.append(FieldUpdate(path=f"athlete.{key}", action="skipped_existing", value=existing))
        return
    if existing == value:
        return
    section[key] = value
    action = "overwritten" if has_value else "populated"
    updates.append(
        FieldUpdate(path=f"athlete.{key}", action=action, value=value, source="intervals_import")
    )


def _maybe_set_zones(
    profile: dict[str, Any],
    *,
    block_key: str,
    new_zones: dict[str, list[int]] | None,
    defaults: dict[str, list[int]],
    force: bool,
    updates: list[FieldUpdate],
) -> None:
    """Overwrite ``zones.<block_key>`` only when the user hasn't customized it.

    Intervals returns zone breakpoints as a flat list of cumulative cutoffs
    rather than the named ranges we store in profile.yaml — but we don't
    try to round-trip that mapping. If intervals delivers zones, we accept
    them; otherwise we leave whatever's there.

    A "customized" block is one whose values differ from the file's
    default Coggan/Friel scaffold. If the user already changed a single
    zone we won't touch the block without --force.
    """
    if new_zones is None:
        return
    zones = profile.setdefault("zones", {})
    current = zones.get(block_key)
    if current is not None and current != defaults and not force:
        updates.append(
            FieldUpdate(path=f"zones.{block_key}", action="skipped_existing", value=current)
        )
        return
    if current == new_zones:
        return
    zones[block_key] = new_zones
    action = "overwritten" if (current is not None and current != defaults) else "populated"
    updates.append(
        FieldUpdate(
            path=f"zones.{block_key}", action=action, value=new_zones, source="intervals_import"
        )
    )


def _zones_from_pct_cutoffs(
    cutoffs: list[int], anchor: int | None, names: list[str]
) -> dict[str, list[int]] | None:
    """Convert intervals' cumulative %-of-anchor cutoffs to named ranges.

    Intervals stores zones as e.g. ``[55, 75, 90, 105, 120, 150, 200]`` —
    seven cumulative percentages. We pair them into ``[lo, hi]`` ranges
    keyed by the canonical names in ``profile.yaml``. Returns None when
    the number of cutoffs doesn't match the expected zone-name count;
    that's a custom zone scheme we shouldn't try to map.

    ``anchor`` is unused for percentage zones — intervals already
    expresses zone boundaries as % of FTP / LTHR. We keep it in the
    signature for symmetry with future absolute-zone variants.
    """
    _ = anchor
    if len(cutoffs) != len(names):
        return None
    out: dict[str, list[int]] = {}
    prev_hi = -1
    for name, hi in zip(names, cutoffs, strict=True):
        lo = 0 if prev_hi < 0 else prev_hi + 1
        out[name] = [lo, int(hi)]
        prev_hi = int(hi)
    return out


def _apply_intervals_facts(
    profile: dict[str, Any],
    facts: _IntervalsFacts,
    *,
    today_iso: str,
    force: bool,
    updates: list[FieldUpdate],
) -> None:
    source_ref = f"intervals.icu/athlete/{facts.athlete_id}"

    if facts.name:
        _set_athlete_field(profile, key="name", value=facts.name, force=force, updates=updates)
    if facts.dob:
        _set_athlete_field(profile, key="dob", value=facts.dob, force=force, updates=updates)
    if facts.weight_kg is not None:
        _set_athlete_field(
            profile,
            key="weight_kg",
            value=round(facts.weight_kg, 1),
            force=force,
            updates=updates,
        )
    if facts.height_cm is not None:
        _set_athlete_field(
            profile,
            key="height_cm",
            value=int(round(facts.height_cm)),
            force=force,
            updates=updates,
        )

    # Resting HR is a scalar in profile.yaml, not a provenance struct.
    if facts.resting_hr is not None:
        existing = profile.get("thresholds", {}).get("resting_hr")
        if force or _is_blank_value(existing):
            profile.setdefault("thresholds", {})["resting_hr"] = facts.resting_hr
            if existing != facts.resting_hr:
                action = "overwritten" if not _is_blank_value(existing) else "populated"
                updates.append(
                    FieldUpdate(
                        path="thresholds.resting_hr",
                        action=action,
                        value=facts.resting_hr,
                        source="intervals_import",
                    )
                )
        else:
            updates.append(
                FieldUpdate(
                    path="thresholds.resting_hr", action="skipped_existing", value=existing
                )
            )

    if facts.bike_ftp:
        _set_threshold(
            profile,
            key="ftp_w",
            value=facts.bike_ftp,
            source_ref=source_ref,
            today_iso=today_iso,
            force=force,
            updates=updates,
        )
    # LTHR: prefer bike's fthr field, fall back to run's lthr.
    lthr = facts.bike_lthr or facts.run_lthr
    if lthr:
        _set_threshold(
            profile,
            key="lthr_bpm",
            value=lthr,
            source_ref=source_ref,
            today_iso=today_iso,
            force=force,
            updates=updates,
        )
    max_hr = facts.bike_max_hr or facts.run_max_hr
    if max_hr:
        _set_threshold(
            profile,
            key="max_hr",
            value=max_hr,
            source_ref=source_ref,
            today_iso=today_iso,
            force=force,
            updates=updates,
        )
    if facts.run_threshold_pace is not None:
        if facts.run_threshold_pace < _RUN_PACE_SANITY_FLOOR_MIN_PER_KM:
            updates.append(
                FieldUpdate(
                    path="thresholds.run_threshold_pace",
                    action="skipped_unset_sentinel",
                    value=facts.run_threshold_pace,
                    source="intervals_import",
                    note=(
                        f"intervals returned unset sentinel "
                        f"(got {facts.run_threshold_pace} min/km, "
                        f"< {_RUN_PACE_SANITY_FLOOR_MIN_PER_KM} min/km floor)"
                    ),
                )
            )
        else:
            _set_threshold(
                profile,
                key="run_threshold_pace",
                value=_format_run_pace(facts.run_threshold_pace),
                source_ref=source_ref,
                today_iso=today_iso,
                force=force,
                updates=updates,
            )
    if facts.swim_threshold_pace is not None:
        if facts.swim_threshold_pace < _SWIM_PACE_SANITY_FLOOR_SEC_PER_100M:
            updates.append(
                FieldUpdate(
                    path="thresholds.swim_css_pace",
                    action="skipped_unset_sentinel",
                    value=facts.swim_threshold_pace,
                    source="intervals_import",
                    note=(
                        f"intervals returned unset sentinel "
                        f"(got {facts.swim_threshold_pace} sec/100m, "
                        f"< {_SWIM_PACE_SANITY_FLOOR_SEC_PER_100M:.0f} sec/100m floor)"
                    ),
                )
            )
        else:
            _set_threshold(
                profile,
                key="swim_css_pace",
                value=_format_swim_pace(facts.swim_threshold_pace),
                source_ref=source_ref,
                today_iso=today_iso,
                force=force,
                updates=updates,
            )

    bike_zones = _zones_from_pct_cutoffs(
        facts.bike_power_zones or [], facts.bike_ftp, list(_DEFAULT_BIKE_POWER_ZONES.keys())
    )
    _maybe_set_zones(
        profile,
        block_key="bike_power",
        new_zones=bike_zones,
        defaults=_DEFAULT_BIKE_POWER_ZONES,
        force=force,
        updates=updates,
    )
    run_zones = _zones_from_pct_cutoffs(
        facts.run_hr_zones or [], lthr, list(_DEFAULT_RUN_HR_ZONES.keys())
    )
    _maybe_set_zones(
        profile,
        block_key="run_hr",
        new_zones=run_zones,
        defaults=_DEFAULT_RUN_HR_ZONES,
        force=force,
        updates=updates,
    )


# ---------------------------------------------------------------------------
# Gap prompts (intervals doesn't know these)
# ---------------------------------------------------------------------------


# (key, prompt-label, kind)
# kind: "threshold" -> populates thresholds.<key> with provenance struct;
# kind: "pr"        -> populates prs.<key> as plain string;
# kind: "list"      -> comma-separated -> list (for strengths/limiters).
_GAP_FIELDS: list[tuple[str, str, str]] = [
    ("swim_css_pace", "Swim CSS pace (e.g. 1:38/100m, blank to skip)", "threshold"),
    ("5k_run", "5k run PR (e.g. 18:45, blank to skip)", "pr"),
    ("10k_run", "10k run PR (blank to skip)", "pr"),
    ("half_marathon", "Half-marathon PR (blank to skip)", "pr"),
    ("marathon", "Marathon PR (blank to skip)", "pr"),
    ("strengths", "Strengths (comma-separated, blank to skip)", "list"),
    ("limiters", "Limiters (comma-separated, blank to skip)", "list"),
]


def _apply_gap_prompts(
    profile: dict[str, Any],
    *,
    today_iso: str,
    force: bool,
    updates: list[FieldUpdate],
    prompt_fn: Callable[[str], str],
) -> None:
    for key, label, kind in _GAP_FIELDS:
        if kind == "threshold":
            existing = profile.get("thresholds", {}).get(key)
            if _has_real_value(existing) and not force:
                continue
        elif kind == "pr":
            existing = profile.get("prs", {}).get(key)
            if not _is_blank_value(existing) and not force:
                continue
        else:  # list
            existing = profile.get(key)
            if isinstance(existing, list) and existing and not force:
                continue

        try:
            raw = prompt_fn(label)
        except (EOFError, KeyboardInterrupt):
            return
        raw = (raw or "").strip()
        if not raw:
            continue

        if kind == "threshold":
            profile.setdefault("thresholds", {})[key] = {
                "value": raw,
                "set_at": today_iso,
                "source": "manual_estimate",
                "source_ref": "init-profile prompt",
            }
            updates.append(
                FieldUpdate(
                    path=f"thresholds.{key}", action="prompted", value=raw, source="manual"
                )
            )
        elif kind == "pr":
            profile.setdefault("prs", {})[key] = raw
            updates.append(
                FieldUpdate(path=f"prs.{key}", action="prompted", value=raw, source="manual")
            )
        else:
            items = [s.strip() for s in raw.split(",") if s.strip()]
            profile[key] = items
            updates.append(
                FieldUpdate(path=key, action="prompted", value=items, source="manual")
            )


# ---------------------------------------------------------------------------
# IO + orchestration
# ---------------------------------------------------------------------------


_CREDS_HELP = (
    "Intervals.icu credentials missing. Add INTERVALS_ICU_ATHLETE_ID and "
    "INTERVALS_ICU_API_KEY to .env at the repo root, then re-run. See "
    "tempo-b7z for the credential-setup checklist."
)


def _creds_present(config: ICUConfig) -> bool:
    return bool(
        config.intervals_icu_api_key
        and config.intervals_icu_athlete_id
        and config.intervals_icu_api_key not in {"your_api_key_here"}
        and config.intervals_icu_athlete_id not in {"i123456"}
    )


def _read_profile(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_profile(path: Path, profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(profile, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


def init_profile(
    *,
    force: bool = False,
    prompt_gaps: bool = True,
    config: ICUConfig | None = None,
    root: Path | None = None,
    today: _date | None = None,
    prompt_fn: Callable[[str], str] | None = None,
    fetch_facts: Callable[[ICUConfig], _IntervalsFacts] | None = None,
) -> InitProfileResult:
    """Seed ``athlete/profile.yaml`` from intervals + interactive gap prompts.

    Pure-by-default — accepts injectable ``config``, ``prompt_fn``, and
    ``fetch_facts`` so tests can drive it without network or stdin.

    Returns an :class:`InitProfileResult` describing what changed. The
    file is only rewritten when at least one update would actually
    modify the YAML, so a re-run on an already-populated profile is a
    byte-identical no-op.
    """
    cfg = config or load_config()
    today_iso = (today or _date.today()).isoformat()
    profile_path = athlete_dir(root) / "profile.yaml"

    result = InitProfileResult(profile_path=profile_path)

    if not _creds_present(cfg):
        result.creds_missing = True
        result.creds_message = _CREDS_HELP
        return result

    original_text = profile_path.read_text(encoding="utf-8") if profile_path.is_file() else ""
    profile = _read_profile(profile_path)
    before = deepcopy(profile)

    fetch = fetch_facts or (lambda c: asyncio.run(fetch_intervals_facts(c)))
    facts = fetch(cfg)

    _apply_intervals_facts(
        profile, facts, today_iso=today_iso, force=force, updates=result.updates
    )

    if prompt_gaps and prompt_fn is not None:
        _apply_gap_prompts(
            profile,
            today_iso=today_iso,
            force=force,
            updates=result.updates,
            prompt_fn=prompt_fn,
        )

    if profile == before:
        # Nothing changed — preserve original bytes (and any comments).
        return result

    _write_profile(profile_path, profile)
    new_text = profile_path.read_text(encoding="utf-8")
    result.changed = new_text != original_text
    if not result.changed:
        # YAML round-trip happened to produce identical bytes; treat as no-op.
        # Re-write with original content so we're truly byte-identical.
        profile_path.write_text(original_text, encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def _format_value(v: Any) -> str:
    if isinstance(v, dict) and "value" in v:
        return str(v.get("value"))
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) or "—"
    return str(v)


def render_summary_rows(result: InitProfileResult) -> list[tuple[str, str, str, str]]:
    """Plain-data rows for the Rich summary table.

    Columns: (status, field, value, provenance).
    """
    rows: list[tuple[str, str, str, str]] = []
    for u in result.updates:
        if u.action == "skipped_existing":
            rows.append(("kept", u.path, _format_value(u.value), "existing"))
        elif u.action == "populated":
            rows.append(("set", u.path, _format_value(u.value), u.source or "—"))
        elif u.action == "overwritten":
            rows.append(("force", u.path, _format_value(u.value), u.source or "—"))
        elif u.action == "prompted":
            rows.append(("manual", u.path, _format_value(u.value), "prompt"))
        elif u.action == "skipped_unset_sentinel":
            detail = u.note or f"intervals sentinel ({_format_value(u.value)})"
            rows.append(("skipped", u.path, f"skipped — {detail}", u.source or "—"))
    return rows


__all__ = [
    "FieldUpdate",
    "InitProfileResult",
    "fetch_intervals_facts",
    "init_profile",
    "render_summary_rows",
]
