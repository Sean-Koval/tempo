"""Tempo ``coach`` CLI entrypoint.

Phase 1 verbs (deterministic):
- ``coach sync``     — fetch activities/wellness, upsert coach.db, re-derive.
- ``coach status``   — print current CTL/ATL/TSB + week progress + wellness.
- ``coach push-week``— dry-run preview of planned sessions (real push lands
  with Phase 4 skills).

Agentic verbs (``plan week``, ``review week``, ``bootstrap-plan``, ``research``,
``ingest``, ``draft-race-plan``, ``check-in``) land with Phase 4+ skills.
"""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

from .db import connect, init_schema
from .derive import derive
from .display import (
    console,
    print_active_injuries,
    print_load,
    print_week,
    print_wellness,
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


def main() -> None:
    """Entry point declared in ``pyproject.toml`` ``[project.scripts]``."""
    app()


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
def status_cmd() -> None:
    """Show current CTL/ATL/TSB, week progress, and latest wellness."""
    conn = connect()
    try:
        init_schema(conn)
        print_load(conn)
        print_week(conn)
        print_wellness(conn)
    finally:
        conn.close()

    print_active_injuries()


@app.command("push-week")
def push_week_cmd(
    week_id: str = typer.Argument(..., help="Week identifier, e.g. 2026-W17."),
) -> None:
    """DRY RUN: preview planned sessions for <week_id>.

    Real push to intervals.icu lands in Phase 4 once the planning skills
    exist and generate sessions_planned rows.
    """
    conn = connect()
    try:
        init_schema(conn)
        rows = conn.execute(
            """
            SELECT id, plan_id, date, sport, library_ref,
                   target_tss, target_duration_s, purpose, pushed_to_intervals
            FROM sessions_planned
            WHERE week_id = ?
            ORDER BY date
            """,
            (week_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        console.print(
            f"[yellow]No planned sessions for {week_id}. "
            f"Run a planning skill first (available in Phase 4+).[/yellow]"
        )
        raise typer.Exit(code=0)

    table = Table(
        title=f"[DRY RUN] {week_id} planned sessions",
        show_header=True,
        header_style="bold",
    )
    for col in ("Date", "Sport", "Library", "Target TSS", "Duration (min)", "Purpose", "Pushed?"):
        table.add_column(col)

    for r in rows:
        dur_min = r["target_duration_s"] // 60 if r["target_duration_s"] else "—"
        pushed = "[green]yes[/green]" if r["pushed_to_intervals"] else "no"
        table.add_row(
            str(r["date"]),
            r["sport"] or "—",
            r["library_ref"] or "—",
            str(r["target_tss"]) if r["target_tss"] else "—",
            str(dur_min),
            r["purpose"] or "—",
            pushed,
        )
    console.print(table)
    console.print(
        "[dim]push-week in Phase 1 is a dry-run only. "
        "Real push uses bulk_upsert_tagged_events in Phase 4.[/dim]"
    )


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

    has_fail = any(r.status == "fail" for r in results) or any(
        d.severity == "fail" for d in debts
    )
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
) -> None:
    """Detect insufficient local coverage and propose trusted-source queries.

    Runs a knowledge search, computes confidence (n_hits, max_score,
    credibility distribution), and either shows the local hits if they
    pass the bar or prints site-scoped queries against sources.yaml for
    you to paste into a browser. Approved URLs go through /ingest-research.

    No web fetch happens here — this is a suggestion surface, not an
    autonomous researcher.
    """
    from .gap_search import (
        KnowledgeGap,
        detect_gap,
        suggest_research_queries,
    )

    topic_arg = topic or None
    result = detect_gap(query, topic=topic_arg)

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
        "go through /ingest-research:[/dim]"
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
    rhr_raw = typer.prompt(
        "Resting HR bpm (blank to skip)", default="", show_default=False
    )
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
        console.print(
            f"[yellow]  intervals push failed:[/yellow] {result.intervals_error}"
        )


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
    """Embed session-library.md into data/vectors/sessions.lance."""
    from .embed import rebuild_sessions

    console.print("[bold]Sessions[/bold] — embedding session-library.md…")
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
