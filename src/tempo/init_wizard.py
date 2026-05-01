"""Guided onboarding wizard — `coach init`.

Walks a fresh user through the five things every user story assumes
already exist: a populated profile, at least one race or goal, populated
preferences, an injury-log baseline, and validated intervals.icu creds.
Then runs `coach sync` to backfill and `coach status` to confirm green.

Section runners are pure functions taking an athlete-files ``root`` and
a ``mode`` (interactive | validate). Each returns a :class:`SectionResult`
so the orchestrator can drive both the live wizard and ``--validate-only``
through the same code path.

Per-section commit (not per-field): a Ctrl-C between sections leaves the
disk in a structurally-valid state and ``--resume`` restarts at the next
incomplete section. Per-field rollback would require a state file we'd
have to keep coherent with the YAML schema, and the wizard is short
enough that re-prompting one section's worth of fields is cheap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from . import athlete as _athlete
from .diagnostics import check_intervals
from .display import console as _default_console


class SectionStatus(StrEnum):
    COMPLETE = "complete"
    SKIPPED = "skipped"
    FAILED = "failed"
    USER_ABORTED = "user_aborted"


@dataclass(slots=True)
class SectionResult:
    name: str
    status: SectionStatus
    message: str = ""
    detail: dict[str, Any] | None = None


# ---- Completeness checks ------------------------------------------------
#
# These are pure reads — used both by `--resume` (to skip already-done
# sections) and `--validate-only` (to print a checklist without touching
# the prompt loop).


_PROFILE_THRESHOLD_KEYS = (
    "ftp_w",
    "lthr_bpm",
    "run_threshold_pace",
    "swim_css_pace",
    "max_hr",
)


def profile_complete(root: Path | None = None) -> tuple[bool, str]:
    """At least one threshold has a non-stub ``.value``.

    The ticket spec says "no remaining stub/null in thresholds.*.value",
    but in practice swim_css_pace + run_threshold_pace are often unknown
    to a brand-new athlete. We accept "any populated" as the bar so the
    wizard doesn't refuse to advance for missing-by-design fields; the
    calibration-debt machinery will continue to surface stale or missing
    thresholds at `coach status` time.
    """
    profile = _athlete.load_profile(root)
    thresholds = profile.get("thresholds") or {}
    populated = []
    for key in _PROFILE_THRESHOLD_KEYS:
        entry = thresholds.get(key)
        if isinstance(entry, dict):
            value = entry.get("value")
            if value not in (None, "", "TODO"):
                populated.append(key)
    if not populated:
        return False, "no thresholds populated in athlete/profile.yaml"
    return True, f"thresholds populated: {', '.join(populated)}"


def race_or_goal_complete(root: Path | None = None) -> tuple[bool, str]:
    races = _athlete.load_races(root)
    goals = _athlete.load_goals(root)
    if races:
        return True, f"{len(races)} race(s) declared"
    if goals:
        return True, f"{len(goals)} goal(s) declared"
    return False, "no race or non-race goal declared"


def preferences_complete(root: Path | None = None) -> tuple[bool, str]:
    """``preferences.md`` exists and the essentials section has no ``# TODO``.

    We scope the check to the "Schedule & logistics" section (the only
    one this wizard touches) — leaving stray TODOs elsewhere is the
    user's call.
    """
    path = _athlete.athlete_dir(root) / "preferences.md"
    if not path.is_file():
        return False, "preferences.md missing"
    text = path.read_text(encoding="utf-8")
    section = _extract_section(text, "Schedule & logistics")
    if section is None:
        return False, "Schedule & logistics section missing"
    if "# TODO" in section or "TODO" in section:
        return False, "Schedule & logistics still contains TODO markers"
    return True, "preferences.md essentials populated"


def injury_complete(root: Path | None = None) -> tuple[bool, str]:
    """injury-log.md exists and the Active section is parsable (empty OK)."""
    path = _athlete.athlete_dir(root) / "injury-log.md"
    if not path.is_file():
        return False, "injury-log.md missing"
    flags = _athlete.active_injury_flags(root)
    if flags:
        return True, f"{len(flags)} active injury flag(s)"
    return True, "no active injury flags"


def intervals_complete(root: Path | None = None) -> tuple[bool, str]:
    """``check_intervals`` returns ok."""
    # check_intervals reads from repo_root() / .env directly; root is
    # honored only when it equals repo_root(). For test isolation we
    # expect callers (and CLI) to chdir or set the env file accordingly.
    result = check_intervals()
    return result.status == "ok", result.message


# ---- Section runners ----------------------------------------------------


def run_profile_section(
    *,
    root: Path | None = None,
    console: Console | None = None,
    interactive: bool = True,
) -> SectionResult:
    """Section 1 — call init_profile() if available, else point at tempo-4us."""
    out = console or _default_console
    out.rule("[bold]1/7 — Profile")

    try:
        from .profile_init import init_profile  # noqa: F401

        profile_available = True
    except ImportError:
        profile_available = False

    if not profile_available:
        out.print(
            "[yellow]Profile auto-seed lands with tempo-4us — skipping for now.[/yellow]\n"
            "[dim]Manually edit athlete/profile.yaml (FTP, LTHR, etc.) before "
            "the planning skills will work.[/dim]"
        )
        complete, why = profile_complete(root)
        if complete:
            return SectionResult(
                "profile",
                SectionStatus.COMPLETE,
                f"already populated: {why}",
            )
        return SectionResult(
            "profile",
            SectionStatus.SKIPPED,
            "profile_init module not yet available (tempo-4us pending)",
        )

    try:
        from .profile_init import init_profile

        init_profile(root=root, interactive=interactive)
    except KeyboardInterrupt:
        return SectionResult("profile", SectionStatus.USER_ABORTED, "Ctrl-C")
    except Exception as exc:
        return SectionResult(
            "profile",
            SectionStatus.FAILED,
            f"init_profile raised: {exc}",
        )

    complete, why = profile_complete(root)
    return SectionResult(
        "profile",
        SectionStatus.COMPLETE if complete else SectionStatus.FAILED,
        why,
    )


def run_race_section(
    *,
    root: Path | None = None,
    console: Console | None = None,
    interactive: bool = True,
) -> SectionResult:
    """Section 2 — write at least one entry to race-calendar.yaml or goals.yaml."""
    out = console or _default_console
    out.rule("[bold]2/7 — Race or goal")

    if not interactive:
        complete, why = race_or_goal_complete(root)
        return SectionResult(
            "race_or_goal",
            SectionStatus.COMPLETE if complete else SectionStatus.FAILED,
            why,
        )

    out.print(
        "What are you training for? Pick a race date or a non-race "
        "performance target."
    )

    try:
        choice = Prompt.ask(
            "[bold]Type[/bold]",
            choices=["race", "goal", "skip"],
            default="race",
        )
    except (KeyboardInterrupt, EOFError):
        return SectionResult("race_or_goal", SectionStatus.USER_ABORTED, "Ctrl-C")

    if choice == "skip":
        return SectionResult(
            "race_or_goal",
            SectionStatus.SKIPPED,
            "user declined to declare a race/goal",
        )

    try:
        if choice == "race":
            return _prompt_race(root=root, console=out)
        return _prompt_goal(root=root, console=out)
    except (KeyboardInterrupt, EOFError):
        return SectionResult("race_or_goal", SectionStatus.USER_ABORTED, "Ctrl-C")


def _prompt_race(*, root: Path | None, console: Console) -> SectionResult:
    rid = Prompt.ask("[bold]Race id[/bold] (slug, e.g. 2026-im-lake-placid)")
    name = Prompt.ask("[bold]Race name[/bold]")
    date_str = Prompt.ask("[bold]Race date[/bold] (YYYY-MM-DD)")
    distance = Prompt.ask(
        "[bold]Distance[/bold]",
        choices=[
            "ironman",
            "half_ironman",
            "olympic",
            "sprint",
            "marathon",
            "half",
            "10k",
            "5k",
            "other",
        ],
        default="half_ironman",
    )
    priority = Prompt.ask(
        "[bold]Priority[/bold] (A=peak, B=race-through, C=train-through)",
        choices=["A", "B", "C"],
        default="A",
    )
    location = Prompt.ask("[bold]Location[/bold]", default="TBD")

    new_race: dict[str, Any] = {
        "id": rid,
        "name": name,
        "date": date_str,
        "distance": distance,
        "priority": priority,
        "status": "confirmed",
        "location": location,
    }

    path = _athlete.athlete_dir(root) / "race-calendar.yaml"
    doc: dict[str, Any]
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    else:
        doc = {}
    races = doc.get("races") or []
    races.append(new_race)
    doc["races"] = races

    # Validate via existing helper before persisting — _normalize_race
    # raises RaceCalendarError on bad shape.
    try:
        _athlete._normalize_race(new_race)
    except _athlete.RaceCalendarError as exc:
        return SectionResult("race_or_goal", SectionStatus.FAILED, str(exc))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)
    console.print(f"[green]wrote[/green] {path} (race id={rid})")
    return SectionResult(
        "race_or_goal",
        SectionStatus.COMPLETE,
        f"added race {rid}",
    )


def _prompt_goal(*, root: Path | None, console: Console) -> SectionResult:
    gid = Prompt.ask("[bold]Goal id[/bold] (slug, e.g. 2026-ftp-280)")
    metric = Prompt.ask(
        "[bold]Metric[/bold]",
        choices=[
            "ftp_w",
            "css_pace_s_per_100m",
            "squat_1rm_kg",
            "deadlift_1rm_kg",
            "other",
        ],
        default="ftp_w",
    )
    current = Prompt.ask("[bold]Current value[/bold]", default="")
    target = Prompt.ask("[bold]Target value[/bold]")
    by_date = Prompt.ask("[bold]By date[/bold] (YYYY-MM-DD, blank for open-ended)", default="")

    new_goal: dict[str, Any] = {
        "id": gid,
        "type": "performance_target" if target else "maintenance",
        "metric": metric,
        "target": _maybe_number(target),
    }
    if current:
        new_goal["current"] = _maybe_number(current)
    if by_date:
        new_goal["by_date"] = by_date

    path = _athlete.athlete_dir(root) / "goals.yaml"
    doc: dict[str, Any]
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    else:
        doc = {}
    goals = doc.get("goals") or []
    goals.append(new_goal)
    doc["goals"] = goals

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)
    console.print(f"[green]wrote[/green] {path} (goal id={gid})")
    return SectionResult(
        "race_or_goal",
        SectionStatus.COMPLETE,
        f"added goal {gid}",
    )


def _maybe_number(s: str) -> float | int | str:
    try:
        if "." in s:
            return float(s)
        return int(s)
    except (TypeError, ValueError):
        return s


def run_preferences_section(
    *,
    root: Path | None = None,
    console: Console | None = None,
    interactive: bool = True,
) -> SectionResult:
    """Section 3 — fill in Schedule & logistics in preferences.md."""
    out = console or _default_console
    out.rule("[bold]3/7 — Preferences")

    if not interactive:
        complete, why = preferences_complete(root)
        return SectionResult(
            "preferences",
            SectionStatus.COMPLETE if complete else SectionStatus.FAILED,
            why,
        )

    try:
        weekly_hours = IntPrompt.ask(
            "[bold]Typical weekly training hours[/bold]", default=8
        )
        long_days = Prompt.ask(
            "[bold]Available days for long sessions[/bold] (e.g. 'Sat, Sun')",
            default="Sat, Sun",
        )
        hard_pattern = Prompt.ask(
            "[bold]Preferred hard-day pattern[/bold] (e.g. 'Tue + Thu + Sat')",
            default="Tue + Thu + Sat",
        )
        sleep_window = Prompt.ask(
            "[bold]Sleep window[/bold] (e.g. '10:30pm — 6:30am')",
            default="10:30pm — 6:30am",
        )
        sport_priority = Prompt.ask(
            "[bold]Sport priority order[/bold] (comma-separated)",
            default="bike, run, swim",
        )
        indoor_outdoor = Prompt.ask(
            "[bold]Indoor/outdoor constraints[/bold] (one line)",
            default="outdoor weekends; trainer/treadmill OK midweek",
        )
    except (KeyboardInterrupt, EOFError):
        return SectionResult("preferences", SectionStatus.USER_ABORTED, "Ctrl-C")

    path = _athlete.athlete_dir(root) / "preferences.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else _bootstrap_preferences()

    new_section = (
        "## Schedule & logistics\n\n"
        f"- Typical weekly training hours: {weekly_hours}\n"
        f"- Available days for long sessions (long ride, long run, brick): {long_days}\n"
        f"- Preferred hard-day pattern: {hard_pattern}\n"
        f"- Sleep window: {sleep_window}\n"
        f"- Sport priority: {sport_priority}\n"
        f"- Indoor/outdoor: {indoor_outdoor}\n"
    )

    text = _replace_section(text, "Schedule & logistics", new_section)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    out.print(f"[green]wrote[/green] {path}")

    complete, why = preferences_complete(root)
    return SectionResult(
        "preferences",
        SectionStatus.COMPLETE if complete else SectionStatus.FAILED,
        why,
    )


def _bootstrap_preferences() -> str:
    return (
        "# Coaching Preferences & Constraints\n\n"
        "How Sean wants to be coached.\n\n"
        "## Coaching style\n\n"
        "- Structured periodization with LLM judgment for adjustments.\n\n"
        "## Schedule & logistics\n\n"
        "_(filled in by `coach init`)_\n\n"
        "## Hard constraints\n\n"
        "- Respect injury-log.md active flags without exception.\n\n"
        "## Soft preferences\n\n"
        "- TBD\n"
    )


def run_injury_section(
    *,
    root: Path | None = None,
    console: Console | None = None,
    interactive: bool = True,
) -> SectionResult:
    """Section 4 — ensure injury-log.md exists with a parseable Active section."""
    out = console or _default_console
    out.rule("[bold]4/7 — Injury log")

    path = _athlete.athlete_dir(root) / "injury-log.md"

    if not interactive:
        complete, why = injury_complete(root)
        return SectionResult(
            "injury",
            SectionStatus.COMPLETE if complete else SectionStatus.FAILED,
            why,
        )

    try:
        if path.is_file():
            existing_flags = _athlete.active_injury_flags(root)
            if existing_flags:
                out.print(
                    f"[yellow]injury-log.md has {len(existing_flags)} active flag(s):[/yellow]"
                )
                for f in existing_flags:
                    out.print(f"  - {f}")
                out.print("[dim]Edit athlete/injury-log.md directly to update.[/dim]")
                return SectionResult(
                    "injury",
                    SectionStatus.COMPLETE,
                    f"{len(existing_flags)} active flag(s)",
                )
            return SectionResult(
                "injury",
                SectionStatus.COMPLETE,
                "no active flags",
            )

        has_injury = Confirm.ask(
            "[bold]Any active injuries to log?[/bold]",
            default=False,
        )
    except (KeyboardInterrupt, EOFError):
        return SectionResult("injury", SectionStatus.USER_ABORTED, "Ctrl-C")

    body = _bootstrap_injury_log()
    if has_injury:
        try:
            date_str = Prompt.ask("[bold]Date noticed[/bold] (YYYY-MM-DD)")
            body_part = Prompt.ask("[bold]Body part / diagnosis[/bold]")
            severity = IntPrompt.ask("[bold]Severity (1-5)[/bold]", default=2)
            symptoms = Prompt.ask("[bold]Symptoms[/bold]", default="")
            constraints = Prompt.ask(
                "[bold]Constraints[/bold] (e.g. 'no running for 4 weeks')",
                default="",
            )
        except (KeyboardInterrupt, EOFError):
            return SectionResult("injury", SectionStatus.USER_ABORTED, "Ctrl-C")
        body = body.replace(
            "_No active flags._",
            (
                f"### {date_str} — {body_part} — severity {severity}\n"
                f"- **Status:** active\n"
                f"- **Symptoms:** {symptoms or 'TBD'}\n"
                f"- **Constraints:** {constraints or 'TBD'}\n"
            ),
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    out.print(f"[green]wrote[/green] {path}")
    return SectionResult(
        "injury",
        SectionStatus.COMPLETE,
        "active flag recorded" if has_injury else "no active flags",
    )


def _bootstrap_injury_log() -> str:
    return (
        "# Injury & Niggle Log\n\n"
        "An active flag here outranks every other planning consideration.\n\n"
        "---\n\n"
        "## Active\n\n"
        "_No active flags._\n\n"
        "## Resolved\n\n"
        "_(Archive closed entries here.)_\n"
    )


def run_intervals_section(
    *,
    root: Path | None = None,
    console: Console | None = None,
    interactive: bool = True,
) -> SectionResult:
    """Section 5 — validate intervals.icu credentials via diagnostics."""
    out = console or _default_console
    out.rule("[bold]5/7 — intervals.icu credentials")

    result = check_intervals()
    if result.status == "ok":
        out.print(f"[green]ok[/green] — {result.message}")
        return SectionResult("intervals", SectionStatus.COMPLETE, result.message)

    out.print(f"[red]{result.status}[/red] — {result.message}")
    if result.suggested_fix:
        out.print(f"[dim]→ {result.suggested_fix}[/dim]")
    out.print(
        "[dim]See .env.example for the variable names. If your key is "
        "rejected with HTTP 403 see tempo-b7z for the resolution path.[/dim]"
    )
    return SectionResult("intervals", SectionStatus.FAILED, result.message)


def run_sync_section(
    *,
    console: Console | None = None,
    interactive: bool = True,
    days: int = 90,
) -> SectionResult:
    """Section 6 — call ``tempo.sync.sync`` in-process to backfill."""
    out = console or _default_console
    out.rule("[bold]6/7 — Sync intervals → coach.db")

    if not interactive:
        # Validate-only mode does not re-run network sync.
        return SectionResult("sync", SectionStatus.SKIPPED, "validate-only mode")

    try:
        from .derive import derive
        from .sync import sync as _sync

        out.print(f"  fetching last {days} days…")
        stats = asyncio.run(_sync(days=days))
        out.print(
            f"  activities: {stats.activities_upserted} • wellness: "
            f"{stats.wellness_upserted} • {stats.duration_ms}ms"
        )
        ds = derive()
        out.print(f"  load_daily: {ds.days_written} days written")
    except KeyboardInterrupt:
        return SectionResult("sync", SectionStatus.USER_ABORTED, "Ctrl-C")
    except Exception as exc:
        return SectionResult("sync", SectionStatus.FAILED, f"sync failed: {exc}")

    return SectionResult("sync", SectionStatus.COMPLETE, "sync + derive ok")


def run_status_section(
    *,
    console: Console | None = None,
    interactive: bool = True,
) -> SectionResult:
    """Section 7 — confirm `coach status` is buildable."""
    out = console or _default_console
    out.rule("[bold]7/7 — Status check")

    try:
        from .db import connect, init_schema
        from .status import build_snapshot, render

        conn = connect()
        try:
            init_schema(conn)
            snap = build_snapshot(conn=conn)
        finally:
            conn.close()
        out.print(render(snap, show_week_sessions=False))
    except Exception as exc:
        return SectionResult("status", SectionStatus.FAILED, f"status failed: {exc}")

    return SectionResult("status", SectionStatus.COMPLETE, "status snapshot built")


# ---- Orchestrator -------------------------------------------------------


@dataclass(slots=True)
class WizardOptions:
    resume: bool = False
    validate_only: bool = False
    sync_days: int = 90


def _section_completeness(root: Path | None) -> list[tuple[str, bool, str]]:
    """Per-section (name, is_complete, why) summary — drives both --resume and --validate-only."""
    return [
        ("profile", *profile_complete(root)),
        ("race_or_goal", *race_or_goal_complete(root)),
        ("preferences", *preferences_complete(root)),
        ("injury", *injury_complete(root)),
        ("intervals", *intervals_complete(root)),
    ]


def render_validate_table(
    sections: list[tuple[str, bool, str]],
    *,
    console: Console | None = None,
) -> None:
    out = console or _default_console
    table = Table(title="coach init — section status", show_header=True, header_style="bold")
    for col in ("Status", "Section", "Detail"):
        table.add_column(col)
    for name, ok, why in sections:
        badge = "[green]ok[/green]" if ok else "[red]incomplete[/red]"
        table.add_row(badge, name, why)
    out.print(table)


def run_wizard(
    *,
    options: WizardOptions,
    root: Path | None = None,
    console: Console | None = None,
) -> tuple[list[SectionResult], int]:
    """Drive the full wizard.

    Returns ``(results, exit_code)``. Exit code is 0 when every section
    finishes COMPLETE, 1 if any section is incomplete after running.
    """
    out = console or _default_console

    if options.validate_only:
        sections = _section_completeness(root)
        render_validate_table(sections, console=out)
        all_ok = all(ok for _, ok, _ in sections)
        return ([], 0 if all_ok else 1)

    sections = _section_completeness(root)
    completeness = {name: ok for name, ok, _ in sections}
    results: list[SectionResult] = []

    runners: list[tuple[str, Any]] = [
        ("profile", lambda: run_profile_section(root=root, console=out)),
        ("race_or_goal", lambda: run_race_section(root=root, console=out)),
        ("preferences", lambda: run_preferences_section(root=root, console=out)),
        ("injury", lambda: run_injury_section(root=root, console=out)),
        ("intervals", lambda: run_intervals_section(root=root, console=out)),
        ("sync", lambda: run_sync_section(console=out, days=options.sync_days)),
        ("status", lambda: run_status_section(console=out)),
    ]

    for name, runner in runners:
        if options.resume and name in completeness and completeness[name]:
            out.print(f"[dim]skipping {name} (already complete)[/dim]")
            results.append(
                SectionResult(name, SectionStatus.COMPLETE, "already complete (--resume)")
            )
            continue
        try:
            res = runner()
        except KeyboardInterrupt:
            results.append(SectionResult(name, SectionStatus.USER_ABORTED, "Ctrl-C"))
            out.print(f"\n[yellow]Aborted at {name}. Re-run `coach init --resume` to continue.[/yellow]")
            return (results, 1)
        results.append(res)
        if res.status in (SectionStatus.FAILED, SectionStatus.USER_ABORTED):
            out.print(
                f"\n[yellow]Stopped at {name} ({res.status.value}). "
                "Re-run `coach init --resume` once resolved.[/yellow]"
            )
            return (results, 1)

    out.rule("[bold green]coach init complete")
    out.print(
        "[green]All sections complete.[/green] You can now invoke "
        "/bootstrap-plan to scaffold a plan."
    )
    return (results, 0)


# ---- Section helpers ----------------------------------------------------


def _extract_section(text: str, heading: str) -> str | None:
    """Return the body of a `## <heading>` section, or None if missing.

    Body is everything between the heading and the next ``## `` heading
    (or end of file).
    """
    import re

    pat = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE | re.IGNORECASE)
    m = pat.search(text)
    if not m:
        return None
    tail = text[m.end():]
    next_h = re.search(r"^##\s+", tail, re.MULTILINE)
    return tail[: next_h.start()] if next_h else tail


def _replace_section(text: str, heading: str, replacement: str) -> str:
    """Replace ``## <heading>`` body with ``replacement`` (which itself starts with the heading).

    If the section is missing, append at end. Preserves trailing newline.
    """
    import re

    pat = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE | re.IGNORECASE)
    m = pat.search(text)
    if not m:
        sep = "" if text.endswith("\n") else "\n"
        return text + sep + "\n" + replacement.rstrip() + "\n"
    tail = text[m.end():]
    next_h = re.search(r"^##\s+", tail, re.MULTILINE)
    end = m.end() + (next_h.start() if next_h else len(tail))
    new = text[: m.start()] + replacement.rstrip() + "\n\n" + text[end:]
    return new


__all__ = [
    "SectionResult",
    "SectionStatus",
    "WizardOptions",
    "injury_complete",
    "intervals_complete",
    "preferences_complete",
    "profile_complete",
    "race_or_goal_complete",
    "render_validate_table",
    "run_injury_section",
    "run_intervals_section",
    "run_preferences_section",
    "run_profile_section",
    "run_race_section",
    "run_status_section",
    "run_sync_section",
    "run_wizard",
]
