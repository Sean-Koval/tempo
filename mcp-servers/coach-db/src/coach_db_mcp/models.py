"""Pydantic models — the typed tool surface exposed to the agent.

Keep these small and JSON-friendly. All dates are ISO strings; sqlite3 stores
timestamps as strings already and we pass them through rather than converting
twice.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ActivityOut(BaseModel):
    id: str
    start_date: str
    sport: str
    duration_s: int | None = None
    distance_m: float | None = None
    tss: float | None = None
    np: float | None = None
    intensity_factor: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    decoupling: float | None = None
    elevation_gain_m: float | None = None


class LoadPoint(BaseModel):
    date: str
    ctl: float | None = None
    atl: float | None = None
    tsb: float | None = None
    ramp_7d: float | None = None
    ctl_bike: float | None = None
    ctl_run: float | None = None
    ctl_swim: float | None = None


class ReadinessSnapshot(BaseModel):
    as_of: str
    sleep_h_latest: float | None = None
    sleep_h_7d_mean: float | None = None
    hrv_latest: float | None = None
    hrv_7d_mean: float | None = None
    hrv_trend_delta: float | None = Field(
        default=None,
        description="7d mean minus previous-7d mean — positive = improving.",
    )
    rhr_latest: int | None = None
    rhr_7d_mean: float | None = None
    readiness_latest: int | None = None
    notes_latest: str | None = None
    samples: int = Field(default=0, description="wellness_daily rows covered by the window.")


class AdherenceItem(BaseModel):
    planned_session_id: str
    date: str | None = None
    sport: str | None = None
    library_ref: str | None = None
    activity_id: str | None = None
    completed: bool | None = None
    tss_delta: float | None = None
    duration_delta_s: int | None = None
    reason: str | None = None


class AdherenceReport(BaseModel):
    week_id: str
    planned_count: int
    completed_count: int
    skipped_count: int
    moved_count: int
    completion_pct: float
    total_planned_tss: float
    total_actual_tss: float
    items: list[AdherenceItem]


class SessionMatch(BaseModel):
    id: str
    text: str
    sport: str = ""
    purpose: str = ""
    duration_min_lo: int | None = None
    duration_min_hi: int | None = None
    tss_lo: int | None = None
    tss_hi: int | None = None
    score: float = 0.0


class MemoryHit(BaseModel):
    id: str
    text: str
    source: str
    scope: str = ""
    kind: str = ""
    timestamp: str = ""
    file_path: str = ""
    score: float = 0.0


class DecisionLogged(BaseModel):
    id: int
    embedded: bool
    timestamp: str


class Snippet(BaseModel):
    id: str
    text: str
    path: str
    topic: str = ""
    credibility: str = "unvetted"
    source_ids: list[str] = Field(default_factory=list)
    phase: str = ""
    score: float = 0.0


class Delta(BaseModel):
    planned_session_id: str
    date: str | None = None
    sport: str | None = None
    library_ref: str | None = None
    purpose: str | None = None
    planned_tss: float | None = None
    actual_tss: float | None = None
    tss_delta: float | None = None
    planned_duration_s: int | None = None
    actual_duration_s: int | None = None
    duration_delta_s: int | None = None
    reason: str | None = None
    activity_id: str | None = None
