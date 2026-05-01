"""Tempo ``coach`` CLI entrypoint.

Deterministic verbs:
- ``coach sync``       — pull intervals data → coach.db → derive load.
- ``coach status``     — single-pane current state (plan + week + load
  + wellness + calibration debt + sync freshness + injury flags).
- ``coach push-week``  — idempotent upsert of planned sessions to
  intervals.icu, with conflict detection + post-write verify.
- ``coach plan amend`` — atomic plan amendments (shift-target,
  switch-target, insert-test). Each writes a single commit-worthy diff
  across plan.yaml + goal.yaml + changelog.md + decisions row.
- ``coach week amend-session`` — single-session amendment with auto
  changelog + log_decision.
- ``coach doctor``     — preflight; enumerates calibration debt.
- ``coach check-in``   — morning wellness capture.
- ``coach vectors *``  — knowledge / sessions / memory embedding rebuilds.
- ``coach dashboard *``— render HTML coaching dashboards.

Agentic verbs (``/plan-training-week``, ``/review-week``, ``/bootstrap-plan``,
``/draft-race-plan``, ``/ingest-research``, ``/morning-check-in``) live in
``.claude/skills/`` and are invoked through Claude Code as slash commands.
"""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

from .db import connect, init_schema
from .derive import derive
from .display import (
    console,
)
from .sync import sync

app = typer.Typer(
    name="coach",
    help="Tempo — local-first Ironman coaching agent.",
    no_args_is_help=True,
)
vectors_app = typer.Typer(
    name="vectors",
    help="Manage the knowledge vector index (data/vectors/knowledge.lance).",
    no_args_is_help=True,
)
app.add_typer(vectors_app)

dashboard_app = typer.Typer(
    name="dashboard",
    help="Render HTML coaching dashboards into ./dashboards/.",
    no_args_is_help=True,
)
app.add_typer(dashboard_app)

plan_app = typer.Typer(
    name="plan",
    help="Plan-level operations (amendments, recompose, etc.).",
    no_args_is_help=True,
)
app.add_typer(plan_app)
plan_amend_app = typer.Typer(
    name="amend",
    help="Atomic plan amendments — date shifts, race switches, test inserts.",
    no_args_is_help=True,
)
plan_app.add_typer(plan_amend_app)

week_app = typer.Typer(
    name="week",
    help="Week-level operations (per-session amendments).",
    no_args_is_help=True,
)
app.add_typer(week_app)


def main() -> None:
    """Entry point declared in ``pyproject.toml`` ``[project.scripts]``."""
    app()


@app.command("init")
def init_cmd(
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Skip already-complete sections and pick up at the first incomplete one.",
    ),
    validate_only: bool = typer.Option(
        False,
        "--validate-only",
        help="Print a per-section completeness table and exit non-zero if any section is incomplete. CI-friendly; never prompts.",
    ),
    sync_days: int = typer.Option(
        90,
        "--sync-days",
        help="Days of history to backfill in the sync section.",
    ),
) -> None:
    """Guided onboarding — profile, race/goal, preferences, injury, creds, sync, status.

    Safe to re-run: ``--resume`` skips sections whose completeness markers
    are already satisfied. ``--validate-only`` runs the same checks
    without prompting and exits non-zero if any section is incomplete
    (suitable for CI gating).
    """
    from .init_wizard import WizardOptions, run_wizard

    opts = WizardOptions(
        resume=resume,
        validate_only=validate_only,
        sync_days=sync_days,
    )
    _, code = run_wizard(options=opts)
    if code != 0:
        raise typer.Exit(code=code)


@app.command("sync")
def sync_cmd(
    days: int = typer.Option(90, "--days", help="Window size in days back from today."),
    full: bool = typer.Option(False, "--full", help="Deep backfill (~3 years)."),
) -> None:
    """Pull activities + wellness from intervals → coach.db, then derive load."""
    window = 365 * 3 if full else days
    console.print(f"[bold]Sync[/bold] — fetching last {window} days…")

    try:
        sync_stats = asyncio.run(sync(days=window))
    except Exception as e:
        console.print(f"[red]sync failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(
        f"  activities: [green]{sync_stats.activities_upserted}[/green] upserted "
        f"• wellness: [green]{sync_stats.wellness_upserted}[/green] upserted "
        f"• {sync_stats.duration_ms}ms"
    )

    console.print("[bold]Derive[/bold] — recomputing load_daily…")
    derive_stats = derive()
    console.print(
        f"  {derive_stats.days_written} days written • "
        f"{derive_stats.activities_scored} activities scored • "
        f"{derive_stats.duration_ms}ms"
    )


@app.command("status")
def status_cmd(
    week: bool = typer.Option(
        False,
        "--week",
        help="Append the current week's session list to the snapshot.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the snapshot as JSON instead of a Rich table (for scripts).",
    ),
) -> None:
    """One-screen current state: plan + phase + week + load + wellness + debt + sync.

    Severities: ``green`` everything's on track; ``yellow`` worth a glance;
    ``red`` action recommended (stale sync, large CTL drift, active injury).
    """
    from .status import build_snapshot, render

    conn = connect()
    try:
        init_schema(conn)
        snap = build_snapshot(conn=conn)
    finally:
        conn.close()

    if json_out:
        console.print_json(snap.to_json())
        return

    console.print(render(snap, show_week_sessions=week))


@app.command("push-week")
def push_week_cmd(
    week_id: str = typer.Argument(..., help="Week identifier, e.g. 2026-W17."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview the events + any conflicts without writing.",
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="Skip the post-write re-fetch + diff (default: verify on).",
    ),
    force_overwrite: bool = typer.Option(
        False,
        "--force-overwrite",
        help="Proceed past conflicts (manually-created events) without prompting.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Don't prompt before writing — useful for scripted invocations.",
    ),
) -> None:
    """Push the week's planned sessions to intervals.icu, idempotently.

    Default behavior:
    1. Fetch the week's existing events.
    2. Detect conflicts (manually-created events in planned slots).
    3. Prompt unless ``--force-overwrite`` or ``--yes``.
    4. Upsert each planned session by ``external_id = "<plan_id>/<session_id>"``.
    5. Re-fetch and diff (skip with ``--no-verify``).
    6. Append a summary line to ``data/events.jsonl``.

    Exit codes: 0 on clean push, 1 on verification mismatches, 2 on
    aborted-due-to-conflicts.
    """
    from .push import (
        PushAborted,
        load_planned_sessions,
        push_week,
        render_conflicts_text,
        render_mismatches_text,
        render_session_table_rows,
    )

    conn = connect()
    init_schema(conn)
    try:
        planned = load_planned_sessions(conn, week_id=week_id)
    except Exception:
        conn.close()
        raise

    if not planned:
        conn.close()
        console.print(
            f"[yellow]No planned sessions for {week_id}. Run a planning skill first.[/yellow]"
        )
        raise typer.Exit(code=0)

    # Always render the session table — both dry-run and real-push users
    # benefit from seeing what's about to land.
    table = Table(
        title=("[DRY RUN] " if dry_run else "") + f"{week_id} planned sessions",
        show_header=True,
        header_style="bold",
    )
    for col in ("Date", "Sport", "Library", "Target TSS", "Duration", "Purpose"):
        table.add_column(col)
    for row in render_session_table_rows(planned):
        table.add_row(*row)
    console.print(table)

    plan_id = planned[0].plan_id

    def _on_conflict(conflicts) -> bool:
        console.print(f"\n[yellow]{render_conflicts_text(conflicts)}[/yellow]")
        if yes:
            return True
        return typer.confirm("Overwrite these events?", default=False)

    try:
        result = push_week(
            plan_id=plan_id,
            week_id=week_id,
            planned=planned,
            dry_run=dry_run,
            verify=not no_verify,
            force_overwrite=force_overwrite,
            on_conflict_prompt=_on_conflict,
            mark_pushed_conn=None if dry_run else conn,
        )
    except PushAborted as e:
        conn.close()
        console.print(f"\n[yellow]Aborted:[/yellow] {e}")
        raise typer.Exit(code=2) from e
    except Exception as e:
        if dry_run:
            conn.close()
            # Dry-run shouldn't block on intervals reachability — the table
            # above still answers "what would land". Keep the warning but
            # exit clean.
            console.print(
                f"\n[yellow]Could not reach intervals for conflict check:[/yellow] {e}\n"
                "[dim]This dry-run only shows local sessions; conflict detection "
                "requires intervals creds (see `coach doctor`).[/dim]"
            )
            return
        conn.close()
        console.print(f"\n[red]push failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    conn.close()

    if result.conflicts:
        console.print(f"\n[yellow]{render_conflicts_text(result.conflicts)}[/yellow]")

    if dry_run:
        console.print(
            f"\n[dim]Dry-run only — {result.planned_count} sessions ready to push. "
            "Re-run without --dry-run (or pipe a --yes confirmation) to write.[/dim]"
        )
        return

    summary = result.summary()
    console.print(
        f"\n[green]push complete[/green] — {summary['written_count']} written "
        f"({summary['created_count']} created, {summary['updated_count']} updated)"
        + (f", [red]{summary['error_count']} error(s)[/red]" if summary["error_count"] else "")
    )

    if result.errors:
        for err in result.errors:
            console.print(f"  [red]✗[/red] {err}")

    if result.verified:
        if result.mismatches:
            console.print(f"\n[red]{render_mismatches_text(result.mismatches)}[/red]")
            raise typer.Exit(code=1)
        console.print("[dim]post-write verify: clean[/dim]")
    elif not no_verify:
        console.print("[yellow]verification skipped (errors during write)[/yellow]")


@app.command("decoupling")
def decoupling_cmd(
    limit: int = typer.Option(
        50,
        "--limit",
        help="Max activities to process this run (oldest-first).",
    ),
    sleep_ms: int = typer.Option(
        250,
        "--sleep-ms",
        help="Pause between stream fetches; courteous to the upstream rate limit.",
    ),
    recompute: bool = typer.Option(
        False,
        "--recompute",
        help="Recompute even where decoupling is already populated.",
    ),
) -> None:
    """Backfill aerobic decoupling (Pw:HR / Pa:HR) from activity streams.

    Lazy by design: walks ``activities WHERE decoupling IS NULL`` oldest
    first, fetches streams one at a time, persists the raw response under
    ``data/raw/intervals/`` for rebuildability, and writes back to
    ``activities.decoupling``.

    Run after ``coach sync`` when you want fresher signal, or in
    chunks (``--limit 50``) to backfill a long history without exhausting
    the upstream rate budget.
    """
    from .decoupling import backfill

    console.print(
        f"[bold]Decoupling[/bold] — backfilling up to {limit} activities "
        f"(sleep {sleep_ms}ms, recompute={recompute})…"
    )

    try:
        stats = asyncio.run(
            backfill(
                limit=limit,
                sleep_s=sleep_ms / 1000.0,
                recompute=recompute,
            )
        )
    except Exception as e:
        console.print(f"[red]decoupling backfill failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(
        f"  candidates: {stats.candidates} • "
        f"fetched: [green]{stats.fetched}[/green] • "
        f"computed: [green]{stats.computed}[/green] • "
        f"skipped: {stats.skipped} • "
        f"errors: [red]{stats.errors}[/red] • "
        f"{stats.duration_ms}ms"
    )
    if stats.candidates == 0:
        console.print("[dim]Nothing to do — all eligible activities have decoupling.[/dim]")


@app.command("doctor")
def doctor_cmd() -> None:
    """Run preflight checks against intervals.icu, coach.db, vectors, plans.

    Also lists outstanding calibration debt against the active plan
    (placeholder race date, missing FTP, empty load history, etc.).

    Exits non-zero if any system check fails OR any calibration debt is
    severity ``fail``. Warns are visible but don't fail the command.
    """
    from .calibration import calibration_debt
    from .diagnostics import run_all

    results = run_all()

    badge = {
        "ok": "[green]ok[/green]",
        "warn": "[yellow]warn[/yellow]",
        "fail": "[red]fail[/red]",
    }

    table = Table(title="Tempo doctor — system", show_header=True, header_style="bold")
    for col in ("Status", "Check", "Detail"):
        table.add_column(col)
    for r in results:
        cell = r.message
        if r.suggested_fix:
            cell += f"\n[dim]→ {r.suggested_fix}[/dim]"
        table.add_row(badge[r.status], r.name, cell)
    console.print(table)

    debts = calibration_debt()
    if debts:
        debt_table = Table(
            title="Calibration debt — active plan",
            show_header=True,
            header_style="bold",
        )
        for col in ("Severity", "Field", "Detail"):
            debt_table.add_column(col)
        for d in debts:
            cell = d.message + f"\n[dim]→ {d.suggested_fix}[/dim]"
            if d.blocks:
                cell += f"\n[dim]   blocks: {', '.join(d.blocks)}[/dim]"
            debt_table.add_row(badge[d.severity], d.field, cell)
        console.print(debt_table)
    else:
        console.print("[dim]Calibration debt: none.[/dim]")

    has_fail = any(r.status == "fail" for r in results) or any(d.severity == "fail" for d in debts)
    if has_fail:
        raise typer.Exit(code=1)


@app.command("research-gap")
def research_gap_cmd(
    query: str = typer.Argument(..., help="What you wish you had local knowledge on."),
    topic: str = typer.Option(
        "",
        "--topic",
        help="Optional sources.yaml topic filter (e.g. 'injury', 'nutrition').",
    ),
    k: int = typer.Option(5, "--k", help="Max suggestions to print."),
    execute: bool = typer.Option(
        False,
        "--execute",
        help=(
            "Emit a JSON brief listing the top-K constrained suggestion queries "
            "for the /research-gap-fetch slash command to drive WebSearch + "
            "approval + ingest. Never fetches; the agent does that under an "
            "explicit approval gate."
        ),
    ),
    top_k: int = typer.Option(
        3,
        "--top-k",
        help="With --execute: how many suggestion queries to surface (default 3).",
    ),
) -> None:
    """Detect insufficient local coverage and propose trusted-source queries.

    Runs a knowledge search, computes confidence (n_hits, max_score,
    credibility distribution), and either shows the local hits if they
    pass the bar or prints site-scoped queries against sources.yaml for
    you to paste into a browser. Approved URLs go through /ingest-research.

    With ``--execute``, prints a JSON brief (queries + credibility tags +
    runbook fields) so the ``/research-gap-fetch`` slash command can
    orchestrate WebSearch → AskUserQuestion → /ingest-research without
    free-form queries leaking into the loop.

    No web fetch happens here — this is a suggestion surface, not an
    autonomous researcher. Approval is always explicit.
    """
    import json as _json
    from typing import Any

    from .gap_search import (
        KnowledgeGap,
        detect_gap,
        suggest_research_queries,
    )

    topic_arg = topic or None
    result = detect_gap(query, topic=topic_arg)

    if execute:
        # Machine-readable brief for the /research-gap-fetch slash command.
        if not isinstance(result, KnowledgeGap):
            hits, confidence = result
            payload = {
                "gap_detected": False,
                "query": query,
                "topic": topic_arg,
                "confidence": {
                    "n_hits": confidence.n_hits,
                    "max_score": confidence.max_score,
                    "mean_credibility_rank": confidence.mean_credibility_rank,
                },
                "hits": [
                    {
                        "score": h.score,
                        "credibility": h.credibility,
                        "path": h.path,
                        "snippet": h.text[:200],
                    }
                    for h in hits
                ],
                "suggestions": [],
                "runbook": "Local knowledge sufficient — no web fetch needed.",
            }
            typer.echo(_json.dumps(payload, indent=2))
            return

        gap = result
        suggestions = suggest_research_queries(gap, k=top_k, topic_filter=topic_arg)
        payload: dict[str, Any] = {
            "gap_detected": True,
            "query": query,
            "topic": topic_arg,
            "reason": gap.reason,
            "confidence": {
                "n_hits": gap.confidence.n_hits,
                "max_score": gap.confidence.max_score,
                "mean_credibility_rank": gap.confidence.mean_credibility_rank,
            },
            "suggestions": [
                {
                    "source_id": s.source_id,
                    "source_name": s.source_name,
                    "credibility": s.credibility,
                    "query": s.query,
                    "domain": s.domain,
                }
                for s in suggestions
            ],
            "constraints": {
                "queries_constrained_to_suggestions": True,
                "approval_required": True,
                "ingest_via": "research-gap",
            },
            "runbook": (
                "For each suggestion, call WebSearch with the EXACT query string "
                "(never a free-form variant). Collect URL+title results, present "
                "them with credibility tags via AskUserQuestion. On approval, "
                "feed each URL through /ingest-research and add ingest_via, "
                "gap_query, suggestion, source_id to the resulting frontmatter."
            ),
        }
        if not suggestions:
            # No registered source matches the topic. Switch into discovery
            # mode: the slash command is allowed to fire ONE unconstrained
            # WebSearch with the raw gap query. The brief flips
            # `queries_constrained_to_suggestions` so the slash command
            # routes to the discovery runbook (and the matching log_decision
            # gap_reason). This is the ticket-uqc behaviour and the only
            # path on which an unconstrained search may run.
            payload["discovery_required"] = True
            payload["constraints"]["queries_constrained_to_suggestions"] = False
            payload["constraints"]["unconstrained_query"] = query
            payload["constraints"]["log_decision_gap_reason_prefix"] = (
                "no_registered_sources"
            )
            payload["runbook"] = (
                "No registered source matched this topic. Run a single "
                "unconstrained WebSearch with the raw query, classify each "
                "returned domain (heuristic: .gov/.edu/known peer-review "
                "publishers => peer_reviewed; mass-media TLD => "
                "evidence_based_journalism (vetted_needed); forum/blog host "
                "=> unvetted; matched registered domain => its registered "
                "tag). Surface URLs via AskUserQuestion with the tentative "
                "credibility AND a per-domain 'register this source?' "
                "toggle. On approval, feed approved URLs through "
                "/ingest-research (credibility stays unvetted unless the "
                "human upgrades it explicitly) AND append register-toggled "
                "domains to knowledge/sources-pending.yaml — NEVER to "
                "sources.yaml. Cancel writes nothing. log_decision "
                "rationale MUST start with 'no_registered_sources' so "
                "future search_memory finds these first-time-on-topic "
                "cases."
            )
        typer.echo(_json.dumps(payload, indent=2))
        return

    if not isinstance(result, KnowledgeGap):
        hits, confidence = result
        console.print(
            f"[green]Local knowledge sufficient[/green] — "
            f"{confidence.n_hits} hits, max_score {confidence.max_score:.2f}, "
            f"mean credibility rank {confidence.mean_credibility_rank:.1f}."
        )
        table = Table(title=f"Top hits for {query!r}", show_header=True, header_style="bold")
        for col in ("Score", "Credibility", "Path", "Snippet"):
            table.add_column(col)
        for h in hits:
            preview = h.text.replace("\n", " ")[:80]
            table.add_row(f"{h.score:.3f}", h.credibility, h.path, preview + "…")
        console.print(table)
        return

    gap = result
    console.print(
        f"[yellow]Knowledge gap[/yellow] — reason: [bold]{gap.reason}[/bold] "
        f"(n_hits={gap.confidence.n_hits}, max_score={gap.confidence.max_score:.2f}, "
        f"mean credibility rank={gap.confidence.mean_credibility_rank:.1f})."
    )

    suggestions = suggest_research_queries(gap, k=k)
    if not suggestions:
        console.print("[red]No matching sources in sources.yaml[/red] — consider adding one.")
        raise typer.Exit(code=2)

    console.print(
        "[dim]Paste any of these into your browser; URLs that look credible "
        "go through /ingest-research. Or run with --execute to drive "
        "/research-gap-fetch:[/dim]"
    )
    table = Table(show_header=True, header_style="bold")
    for col in ("#", "Credibility", "Source", "Suggested query"):
        table.add_column(col)
    for i, sug in enumerate(suggestions, start=1):
        table.add_row(
            str(i),
            sug.credibility,
            sug.source_name,
            sug.query,
        )
    console.print(table)


@app.command("check-in")
def check_in_cmd(
    for_date: str = typer.Option(
        "",
        "--date",
        help="ISO date to log against. Defaults to today.",
    ),
    no_push: bool = typer.Option(
        False,
        "--no-push",
        help="Skip the intervals.icu push. DB write still happens.",
    ),
) -> None:
    """Morning wellness capture: sleep, HRV, RHR, readiness, notes.

    Writes to ``coach.db.wellness_daily`` and pushes to intervals.icu
    (unless ``--no-push``). Re-running on the same day upserts.
    """
    from datetime import date as _date

    from .check_in import CheckInInput, check_in

    iso_date = for_date or _date.today().isoformat()
    console.print(f"[bold]Check-in[/bold] — {iso_date}")
    console.print(
        "[dim]Leave optional fields blank to skip. Sleep hours and readiness are required.[/dim]"
    )

    sleep_h = typer.prompt("Sleep hours", type=float)
    sleep_score_raw = typer.prompt(
        "Sleep score (0–100, blank to skip)", default="", show_default=False
    )
    hrv_raw = typer.prompt("HRV ms (blank to skip)", default="", show_default=False)
    rhr_raw = typer.prompt("Resting HR bpm (blank to skip)", default="", show_default=False)
    readiness = typer.prompt("Readiness (1–10)", type=int)
    soreness = typer.prompt("Soreness (free text, blank to skip)", default="", show_default=False)
    notes = typer.prompt("Notes (blank to skip)", default="", show_default=False)

    data = CheckInInput(
        for_date=iso_date,
        sleep_h=sleep_h,
        sleep_score=int(sleep_score_raw) if sleep_score_raw.strip() else None,
        hrv=float(hrv_raw) if hrv_raw.strip() else None,
        rhr=int(rhr_raw) if rhr_raw.strip() else None,
        readiness=readiness,
        soreness=soreness.strip() or None,
        notes=notes.strip() or None,
    )

    result = check_in(data, push=not no_push)

    bits = [f"sleep {sleep_h:.1f}h"]
    if data.hrv is not None:
        bits.append(f"HRV {data.hrv:.0f}")
    if data.rhr is not None:
        bits.append(f"RHR {data.rhr}")
    bits.append(f"readiness {readiness}")
    console.print(f"[green]logged[/green] {iso_date}: {' · '.join(bits)}")

    if result.intervals_pushed:
        console.print("[dim]  pushed to intervals.icu[/dim]")
    elif no_push:
        console.print("[dim]  intervals push skipped (--no-push)[/dim]")
    else:
        console.print(f"[yellow]  intervals push failed:[/yellow] {result.intervals_error}")


@vectors_app.command("rebuild")
def vectors_rebuild_cmd(
    force: bool = typer.Option(False, "--force", help="Re-embed even if file hash matches."),
    paths: str = typer.Option(
        "",
        "--paths",
        help="Comma-separated .md files to limit the rebuild to. Default: all of knowledge/.",
    ),
) -> None:
    """Embed knowledge/ into data/vectors/knowledge.lance."""
    from pathlib import Path as _Path

    from .embed import rebuild

    console.print("[bold]Vectors[/bold] — embedding knowledge/…")
    targets = [_Path(p.strip()) for p in paths.split(",") if p.strip()] or None
    try:
        stats = rebuild(paths=targets, force=force)
    except Exception as e:
        console.print(f"[red]rebuild failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(
        f"  scanned: [green]{stats.files_scanned}[/green] • "
        f"embedded: [green]{stats.files_embedded}[/green] • "
        f"skipped: {stats.files_skipped} • "
        f"chunks: [green]{stats.chunks_written}[/green] "
        f"(deleted {stats.chunks_deleted}) • "
        f"{stats.duration_ms}ms"
    )
    if stats.paths_indexed:
        for p in stats.paths_indexed:
            console.print(f"  [dim]•[/dim] {p}")


@vectors_app.command("rebuild-sessions")
def vectors_rebuild_sessions_cmd(
    force: bool = typer.Option(False, "--force", help="Re-embed even if file hash matches."),
) -> None:
    """Embed knowledge/methodology/session-library/ into data/vectors/sessions.lance."""
    from .embed import rebuild_sessions

    console.print("[bold]Sessions[/bold] — embedding session-library/…")
    try:
        stats = rebuild_sessions(force=force)
    except Exception as e:
        console.print(f"[red]rebuild failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(
        f"  entries: [green]{stats.entries_scanned}[/green] scanned • "
        f"embedded: [green]{stats.entries_embedded}[/green] • "
        f"skipped: {stats.entries_skipped} • "
        f"deleted: {stats.rows_deleted} • "
        f"{stats.duration_ms}ms"
    )


@vectors_app.command("rebuild-memory")
def vectors_rebuild_memory_cmd(
    force: bool = typer.Option(False, "--force", help="Re-embed even if entry hash matches."),
) -> None:
    """Embed decisions + journals + plan changelogs into data/vectors/memory.lance."""
    from .embed import rebuild_memory

    console.print("[bold]Memory[/bold] — embedding decisions / journals / changelogs…")
    try:
        stats = rebuild_memory(force=force)
    except Exception as e:
        console.print(f"[red]rebuild failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(
        f"  sources: [green]{stats.sources_scanned}[/green] scanned • "
        f"embedded: [green]{stats.sources_embedded}[/green] • "
        f"skipped: {stats.sources_skipped} • "
        f"rows: [green]{stats.rows_written}[/green] "
        f"(deleted {stats.rows_deleted}) • "
        f"{stats.duration_ms}ms"
    )


@vectors_app.command("search")
def vectors_search_cmd(
    query: str = typer.Argument(..., help="Natural-language query."),
    k: int = typer.Option(5, "--k", help="Max hits to return."),
    topic: str = typer.Option(None, "--topic", help="Filter to a specific frontmatter topic."),
    credibility_min: str = typer.Option(
        None,
        "--credibility-min",
        help="Drop hits weaker than this level "
        "(peer_reviewed|expert_practitioner|evidence_based_journalism|experiential).",
    ),
) -> None:
    """Semantic search against knowledge.lance."""
    from .embed import search

    hits = search(query, k=k, topic=topic, credibility_min=credibility_min)
    if not hits:
        console.print("[yellow]No hits. Run `coach vectors rebuild` first?[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"Knowledge search: {query!r}", show_header=True, header_style="bold")
    for col in ("Score", "Credibility", "Path", "Chunk"):
        table.add_column(col)
    for h in hits:
        cred = h.credibility
        cred_fmt = f"[red]{cred}[/red]" if cred == "unvetted" else cred
        preview = h.text.replace("\n", " ")[:80]
        table.add_row(f"{h.score:.3f}", cred_fmt, h.path, preview + "…")
    console.print(table)


def _resolve_plan_id(plan_id: str | None) -> str:
    """Resolve --plan-id from the single active plan when omitted."""
    from . import plans as _plans

    if plan_id:
        return plan_id
    try:
        found = _plans.find_single_plan()
    except _plans.MultiplePlansError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e
    if found is None:
        console.print("[red]No plan under plans/. Pass --plan-id explicitly.[/red]")
        raise typer.Exit(code=2)
    return found[0]


def _print_amend_result(result, *, dry_run: bool) -> None:
    """Render an AmendResult to the console — header + per-file unified diff."""
    import difflib

    badge = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]applied[/green]"
    console.print(f"\n[bold]{result.operation}[/bold] {badge} — {result.summary}")
    if result.violations:
        for v in result.violations:
            severity = "[red]HARD[/red]" if result.hard_block else "[yellow]SOFT[/yellow]"
            console.print(f"  {severity}: {v}")
        if result.hard_block:
            console.print(
                "[red]Refusing to apply — HARD validator blocked. Use --dry-run to inspect.[/red]"
            )

    for change in result.files:
        if not change.changed:
            continue
        console.print(f"\n[bold]— {change.label}[/bold]")
        diff = difflib.unified_diff(
            change.before.splitlines(keepends=True),
            change.after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=2,
        )
        text = "".join(diff).rstrip()
        if text:
            for line in text.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    console.print(f"[green]{line}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    console.print(f"[red]{line}[/red]")
                elif line.startswith("@@"):
                    console.print(f"[cyan]{line}[/cyan]")
                else:
                    console.print(line, highlight=False)
        else:
            console.print("  [dim](no diff)[/dim]")

    if not dry_run and not result.hard_block:
        scope = result.decision_scope or "—"
        console.print(
            f"\n[dim]decisions row inserted (scope={scope}, kind={result.decision_kind})[/dim]"
        )


@plan_amend_app.command("shift-target")
def plan_amend_shift_target_cmd(
    delta: str = typer.Option(
        "",
        "--delta",
        "-d",
        help="Signed shift like '+6d' or '-1w'. Mutually exclusive with --target.",
    ),
    target: str = typer.Option(
        "",
        "--target",
        help="ISO date YYYY-MM-DD for the new race day. Mutually exclusive with --delta.",
    ),
    reason: str = typer.Option(
        ...,
        "--reason",
        help="Why the shift — written to changelog.md and the decisions table.",
    ),
    plan_id: str = typer.Option(
        "",
        "--plan-id",
        help="Plan id under plans/. Defaults to single auto-detected plan.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the diff without writing.",
    ),
) -> None:
    """Move the A-race / target date by N days, shift future phases uniformly."""
    from .amend import AmendError, shift_target

    pid = _resolve_plan_id(plan_id or None)
    try:
        days_delta: int | None = None
        target_arg: str | None = None
        if delta and target:
            console.print("[red]Pass exactly one of --delta or --target.[/red]")
            raise typer.Exit(code=2)
        if delta:
            from .amend import _parse_shift_delta  # private but stable

            days_delta = _parse_shift_delta(delta)
        elif target:
            target_arg = target
        else:
            console.print("[red]Pass --delta or --target.[/red]")
            raise typer.Exit(code=2)

        result = shift_target(
            pid,
            days_delta=days_delta,
            target=target_arg,
            reason=reason,
            dry_run=dry_run,
        )
    except AmendError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    _print_amend_result(result, dry_run=dry_run)
    if result.hard_block:
        raise typer.Exit(code=1)


@plan_amend_app.command("switch-target")
def plan_amend_switch_target_cmd(
    new_race_id: str = typer.Argument(..., help="ID of the new A-race in race-calendar.yaml."),
    reason: str = typer.Option(
        ...,
        "--reason",
        help="Why the switch — written to changelog.md and the decisions table.",
    ),
    plan_id: str = typer.Option("", "--plan-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Re-anchor the plan on a different race; carry forward completed phases."""
    from .amend import AmendError, switch_target

    pid = _resolve_plan_id(plan_id or None)
    try:
        result = switch_target(
            pid,
            new_race_id=new_race_id,
            reason=reason,
            dry_run=dry_run,
        )
    except AmendError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    _print_amend_result(result, dry_run=dry_run)
    if result.hard_block:
        raise typer.Exit(code=1)


@plan_amend_app.command("insert-test")
def plan_amend_insert_test_cmd(
    slot: str = typer.Argument(
        ...,
        help="Test slot, e.g. '2026-W22-Wed'. Day = mon|tue|...|sun.",
    ),
    kind: str = typer.Option(
        ...,
        "--type",
        help="One of ftp_test, css_test, 5k_tt, run_threshold.",
    ),
    reason: str = typer.Option(..., "--reason"),
    no_recalibrate: bool = typer.Option(
        False,
        "--no-recalibrate",
        help="Skip the calibration follow-up file (default: register a TODO).",
    ),
    plan_id: str = typer.Option("", "--plan-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Insert a calibration test into a week + sessions_planned + follow-up TODO."""
    from .amend import AmendError, insert_test

    pid = _resolve_plan_id(plan_id or None)
    try:
        result = insert_test(
            pid,
            slot=slot,
            kind=kind,  # type: ignore[arg-type]
            reason=reason,
            recalibrate_on_result=not no_recalibrate,
            dry_run=dry_run,
        )
    except AmendError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    _print_amend_result(result, dry_run=dry_run)


@week_app.command("amend-session")
def week_amend_session_cmd(
    week_id: str = typer.Argument(..., help="ISO week id, e.g. 2026-W18."),
    day: str = typer.Argument(..., help="Day of week — mon|tue|...|sun."),
    duration: str = typer.Option(
        "",
        "--duration",
        help="New duration ('45min', '1.5h', '14km'). Distance is informational.",
    ),
    zone: str = typer.Option("", "--zone", help="Target zone tag, e.g. 'z1', 'z2'."),
    swap_sport: str = typer.Option(
        "",
        "--swap-sport",
        help="Replace the planned sport — bike|run|swim|strength|brick.",
    ),
    reason: str = typer.Option(..., "--reason"),
    plan_id: str = typer.Option("", "--plan-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Atomic single-session amendment: append to week file + log decision."""
    from .amend import AmendError, amend_session

    pid = _resolve_plan_id(plan_id or None)
    try:
        result = amend_session(
            pid,
            week_id=week_id,
            day=day,
            duration=duration or None,
            zone=zone or None,
            swap_sport=swap_sport or None,
            reason=reason,
            dry_run=dry_run,
        )
    except AmendError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    _print_amend_result(result, dry_run=dry_run)


@dashboard_app.command("week")
def dashboard_week_cmd(
    week_id: str = typer.Argument(
        "",
        help="ISO week id (e.g. 2026-W17). Defaults to last completed week.",
    ),
    plan_id: str = typer.Option(
        "",
        "--plan-id",
        help="Plan id under plans/. Defaults to auto-detection.",
    ),
    open_in_browser: bool = typer.Option(
        False,
        "--open",
        help="Launch the rendered HTML in the default browser.",
    ),
) -> None:
    """Render a single-week dashboard: planned vs actual + wellness + load."""
    from .dashboards import render_week
    from .dashboards.common import output_path, write_html

    wid = week_id or None
    pid = plan_id or None
    try:
        html = render_week(week_id=wid, plan_id=pid)
    except Exception as e:
        console.print(f"[red]render failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    from datetime import date as _date
    from datetime import timedelta as _td

    from . import plans as _plans

    resolved_wid = wid or _plans.week_id_for(_date.today() - _td(days=7))
    scope = f"{plan_id or 'auto'}-{resolved_wid}"
    out = write_html(html, output_path("week", scope))
    console.print(f"[green]wrote[/green] {out}")

    if open_in_browser:
        import webbrowser

        webbrowser.open(out.as_uri())


@dashboard_app.command("macro")
def dashboard_macro_cmd(
    plan_id: str = typer.Option(
        "",
        "--plan-id",
        help="Plan id under plans/. Defaults to auto-detection.",
    ),
    open_in_browser: bool = typer.Option(
        False,
        "--open",
        help="Launch the rendered HTML in the default browser.",
    ),
) -> None:
    """Render the macro 24-week timeline dashboard."""
    from .dashboards import render_macro
    from .dashboards.common import output_path, write_html

    pid = plan_id or None
    try:
        html = render_macro(plan_id=pid)
    except Exception as e:
        console.print(f"[red]render failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    out = write_html(html, output_path("macro", plan_id or "auto"))
    console.print(f"[green]wrote[/green] {out}")
    if open_in_browser:
        import webbrowser

        webbrowser.open(out.as_uri())


@dashboard_app.command("decisions")
def dashboard_decisions_cmd(
    scope: str = typer.Option(
        "",
        "--scope",
        help="Filter to a decisions.scope value (e.g. week:2026-W17, plan:2026-im).",
    ),
    since: str = typer.Option(
        "",
        "--since",
        help="ISO date — only show decisions on or after this date. Default: 28d.",
    ),
    open_in_browser: bool = typer.Option(
        False,
        "--open",
        help="Launch the rendered HTML in the default browser.",
    ),
) -> None:
    """Render a scope-filtered decision-trace dashboard."""
    from .dashboards import render_decisions
    from .dashboards.common import output_path, write_html

    try:
        html = render_decisions(scope=scope or None, since=since or None)
    except Exception as e:
        console.print(f"[red]render failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    out = write_html(html, output_path("decisions", scope or "all"))
    console.print(f"[green]wrote[/green] {out}")
    if open_in_browser:
        import webbrowser

        webbrowser.open(out.as_uri())


if __name__ == "__main__":  # pragma: no cover - defensive entry
    main()
