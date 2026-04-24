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


if __name__ == "__main__":  # pragma: no cover - defensive entry
    main()
