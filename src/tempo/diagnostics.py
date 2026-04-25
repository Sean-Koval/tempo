"""Preflight checks for the Tempo planning loop.

Surfaced via ``coach doctor``. Each check is a pure read against an external
dependency (intervals.icu API, coach.db, embedding model, plans tree) and
returns a structured :class:`CheckResult` so the CLI can render a Rich table
and exit with a non-zero code if any check fails.

This exists because intervals.icu errors as "Access denied" with no
diagnostic; we want to distinguish missing env / malformed athlete ID /
401 (bad key) / 403 (revoked or scope) / network errors at a glance.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from .paths import coach_db_path, repo_root

Status = Literal["ok", "warn", "fail"]

_ATHLETE_ID_RE = re.compile(r"^i\d+$")
_INTERVALS_BASE_URL = "https://intervals.icu/api/v1"


@dataclass
class CheckResult:
    """Outcome of one diagnostic check.

    ``status`` drives the CLI exit code (any ``fail`` → exit 1) and the row
    color. ``suggested_fix`` is shown only on warn/fail.
    """

    name: str
    status: Status
    message: str
    suggested_fix: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a flat dict without mutating os.environ.

    Lines are ``KEY=VALUE``. Quoted values are stripped of one layer of
    surrounding quotes. Comments (# ...) and blanks ignored.
    """
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _load_intervals_creds() -> tuple[str, str, str]:
    """Return ``(athlete_id, api_key, source)``.

    Prefer .env file at repo root; fall back to process env. ``source``
    is ``"env-file"``, ``"process-env"``, or ``"unset"`` for messages.
    """
    env_path = repo_root() / ".env"
    file_vals = _read_env_file(env_path)

    athlete_id = file_vals.get("INTERVALS_ICU_ATHLETE_ID") or os.environ.get(
        "INTERVALS_ICU_ATHLETE_ID", ""
    )
    api_key = file_vals.get("INTERVALS_ICU_API_KEY") or os.environ.get(
        "INTERVALS_ICU_API_KEY", ""
    )

    if file_vals.get("INTERVALS_ICU_ATHLETE_ID") or file_vals.get("INTERVALS_ICU_API_KEY"):
        source = "env-file"
    elif athlete_id or api_key:
        source = "process-env"
    else:
        source = "unset"

    return athlete_id, api_key, source


def check_intervals() -> CheckResult:
    """Probe ``GET /api/v1/athlete/{id}`` and classify the failure mode."""
    athlete_id, api_key, source = _load_intervals_creds()

    if not athlete_id and not api_key:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message="No credentials found in .env or process environment.",
            suggested_fix=(
                "Add INTERVALS_ICU_ATHLETE_ID and INTERVALS_ICU_API_KEY to "
                f"{repo_root() / '.env'}. Generate the API key at "
                "https://intervals.icu/settings (Developer section)."
            ),
        )

    if not athlete_id:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message="INTERVALS_ICU_ATHLETE_ID is missing.",
            suggested_fix=(
                "Set INTERVALS_ICU_ATHLETE_ID to your profile slug "
                "(format: i123456 — the leading 'i' is required)."
            ),
        )

    if not api_key or api_key in {"your_api_key_here", "i123456"}:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message="INTERVALS_ICU_API_KEY is missing or still set to a placeholder.",
            suggested_fix=(
                "Generate a real API key at https://intervals.icu/settings "
                "(Developer section) and paste it into .env."
            ),
        )

    if not _ATHLETE_ID_RE.match(athlete_id):
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message=(
                f"INTERVALS_ICU_ATHLETE_ID={athlete_id!r} is malformed — "
                "expected i<digits> (e.g. i123456)."
            ),
            suggested_fix=(
                "Open https://intervals.icu and copy the slug from the URL "
                "(it starts with 'i' followed by digits)."
            ),
            detail={"source": source},
        )

    url = f"{_INTERVALS_BASE_URL}/athlete/{athlete_id}"
    try:
        response = httpx.get(
            url,
            auth=httpx.BasicAuth(username="API_KEY", password=api_key),
            timeout=10.0,
        )
    except httpx.TimeoutException:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message="Request to intervals.icu timed out after 10s.",
            suggested_fix="Check network connectivity and retry.",
            detail={"url": url, "source": source},
        )
    except httpx.HTTPError as exc:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message=f"Network error contacting intervals.icu: {exc}.",
            suggested_fix="Check network connectivity and retry.",
            detail={"url": url, "source": source},
        )

    code = response.status_code
    if code == 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        name = payload.get("name") or payload.get("first_name") or "(unnamed)"
        return CheckResult(
            name="intervals.icu",
            status="ok",
            message=f"Authenticated as {name} ({athlete_id}).",
            detail={"source": source, "athlete_id": athlete_id},
        )

    if code == 401:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message="HTTP 401 — API key rejected.",
            suggested_fix=(
                "The API key is invalid or expired. Re-issue at "
                "https://intervals.icu/settings (Developer) and update .env."
            ),
            detail={"http_status": 401, "source": source},
        )

    if code == 403:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message=(
                "HTTP 403 — credentials accepted but the API key cannot read "
                f"athlete {athlete_id}."
            ),
            suggested_fix=(
                "Most likely the API key was issued under a different "
                "intervals.icu account. Verify the athlete ID in the "
                "intervals.icu URL bar matches the account where the key was "
                "generated, then re-issue the key under the correct account."
            ),
            detail={"http_status": 403, "source": source},
        )

    if code == 404:
        return CheckResult(
            name="intervals.icu",
            status="fail",
            message=f"HTTP 404 — athlete {athlete_id} not found.",
            suggested_fix=(
                "Double-check the athlete ID by opening intervals.icu in a "
                "browser; the slug appears in the URL after /athlete/."
            ),
            detail={"http_status": 404, "source": source},
        )

    return CheckResult(
        name="intervals.icu",
        status="fail",
        message=f"Unexpected HTTP {code} from intervals.icu.",
        suggested_fix="Check intervals.icu status and retry.",
        detail={"http_status": code, "source": source, "body": response.text[:200]},
    )


def check_coach_db() -> CheckResult:
    """Verify coach.db exists and the activities/wellness tables are present."""
    db_path = coach_db_path()
    if not db_path.is_file():
        return CheckResult(
            name="coach.db",
            status="warn",
            message=f"{db_path} does not exist yet.",
            suggested_fix="Run `coach sync` to populate the database.",
        )

    try:
        from .db import connect, current_schema_version, init_schema

        conn = connect(db_path)
        try:
            init_schema(conn)
            version = current_schema_version(conn)
            activities = conn.execute("SELECT COUNT(*) AS n FROM activities").fetchone()["n"]
            wellness = conn.execute("SELECT COUNT(*) AS n FROM wellness_daily").fetchone()["n"]
        finally:
            conn.close()
    except Exception as exc:
        return CheckResult(
            name="coach.db",
            status="fail",
            message=f"Could not open {db_path}: {exc}.",
            suggested_fix="Delete data/coach.db and re-run `coach sync` to rebuild.",
        )

    if activities == 0 and wellness == 0:
        return CheckResult(
            name="coach.db",
            status="warn",
            message=f"Schema v{version} present but tables are empty.",
            suggested_fix="Run `coach sync` to pull activities and wellness from intervals.",
            detail={"activities": 0, "wellness": 0},
        )

    return CheckResult(
        name="coach.db",
        status="ok",
        message=(
            f"Schema v{version} • {activities} activities • {wellness} wellness rows."
        ),
        detail={"activities": activities, "wellness": wellness, "version": version},
    )


def check_embedding_model() -> CheckResult:
    """Verify the fastembed model and lancedb are importable."""
    try:
        from . import embed as _embed

        model_name = getattr(_embed, "_EMBED_MODEL", None)
    except ImportError as exc:
        return CheckResult(
            name="embedding model",
            status="fail",
            message=f"Could not import tempo.embed: {exc}.",
            suggested_fix="Run `uv sync` to install dependencies.",
        )
    except Exception as exc:
        return CheckResult(
            name="embedding model",
            status="warn",
            message=f"Embedding subsystem reachable but emitted: {exc}.",
            suggested_fix="Run `coach vectors rebuild` to verify embedding works end to end.",
        )

    return CheckResult(
        name="embedding model",
        status="ok",
        message=f"tempo.embed importable (model: {model_name or 'unknown'}).",
        detail={"model": model_name},
    )


def check_active_plan() -> CheckResult:
    """Confirm exactly one plan is auto-detectable under plans/."""
    from . import plans as _plans

    try:
        result = _plans.find_single_plan()
    except _plans.MultiplePlansError as exc:
        return CheckResult(
            name="active plan",
            status="warn",
            message=f"Multiple plans under plans/: {exc.plan_ids}.",
            suggested_fix="Pass --plan-id to commands that need it.",
        )

    if result is None:
        return CheckResult(
            name="active plan",
            status="warn",
            message="No plan.yaml found under plans/.",
            suggested_fix=(
                "Declare a goal in athlete/race-calendar.yaml and run "
                "/bootstrap-plan to scaffold one."
            ),
        )

    plan_id, plan_doc = result
    phases = plan_doc.get("phases") or []
    target = plan_doc.get("target_date") or plan_doc.get("goal", {}).get("date")
    return CheckResult(
        name="active plan",
        status="ok",
        message=f"{plan_id} — {len(phases)} phases, target {target or '(none)'}.",
        detail={"plan_id": plan_id, "phase_count": len(phases), "target": target},
    )


def run_all() -> list[CheckResult]:
    """Run every diagnostic in display order."""
    return [
        check_intervals(),
        check_coach_db(),
        check_embedding_model(),
        check_active_plan(),
    ]


__all__ = [
    "CheckResult",
    "Status",
    "check_active_plan",
    "check_coach_db",
    "check_embedding_model",
    "check_intervals",
    "run_all",
]
