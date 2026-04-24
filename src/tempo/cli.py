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
import re
import sqlite3
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from .db import connect, init_schema
from .derive import derive
from .paths import repo_root
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

console = Console()


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
        _print_load(conn)
        _print_week(conn)
        _print_wellness(conn)
    finally:
        conn.close()

    _print_active_injuries()


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


def _print_load(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT date, ctl, atl, tsb, ctl_bike, ctl_run, ctl_swim, ramp_7d "
        "FROM load_daily ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        console.print("[yellow]No load data. Run `coach sync` first.[/yellow]")
        return

    table = Table(title=f"Fitness — as of {row['date']}", show_header=False)
    table.add_row("CTL", f"{row['ctl']:.1f}")
    table.add_row("ATL", f"{row['atl']:.1f}")
    table.add_row("TSB", f"{row['tsb']:+.1f}")
    table.add_row("Ramp (7d)", f"{row['ramp_7d']:+.2f}")
    table.add_row("  bike CTL", f"{row['ctl_bike']:.1f}")
    table.add_row("  run CTL", f"{row['ctl_run']:.1f}")
    table.add_row("  swim CTL", f"{row['ctl_swim']:.1f}")
    console.print(table)


def _print_week(conn: sqlite3.Connection) -> None:
    iso_year, iso_week, _ = date.today().isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"

    planned = conn.execute(
        "SELECT COUNT(*) AS c FROM sessions_planned WHERE week_id = ?",
        (week_id,),
    ).fetchone()["c"]

    completed = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM sessions_planned sp
        JOIN adherence ad ON ad.planned_session_id = sp.id
        WHERE sp.week_id = ? AND ad.completed = 1
        """,
        (week_id,),
    ).fetchone()["c"]

    console.print(f"[bold]Week {week_id}[/bold] — {completed}/{planned} completed")


def _print_wellness(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT date, sleep_h, sleep_score, hrv, rhr, readiness "
        "FROM wellness_daily ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        console.print("[yellow]No wellness data yet.[/yellow]")
        return

    table = Table(title=f"Latest wellness — {row['date']}", show_header=False)
    table.add_row("Sleep (h)", f"{row['sleep_h']:.1f}" if row["sleep_h"] else "—")
    table.add_row("Sleep score", str(row["sleep_score"]) if row["sleep_score"] else "—")
    table.add_row("HRV", f"{row['hrv']:.1f}" if row["hrv"] else "—")
    table.add_row("RHR", str(row["rhr"]) if row["rhr"] else "—")
    table.add_row("Readiness", str(row["readiness"]) if row["readiness"] else "—")
    console.print(table)


_ACTIVE_HEADING = re.compile(r"^##\s+active\b", re.IGNORECASE | re.MULTILINE)


def _print_active_injuries() -> None:
    path = repo_root() / "athlete" / "injury-log.md"
    if not path.is_file():
        return

    text = path.read_text(encoding="utf-8")
    m = _ACTIVE_HEADING.search(text)
    if not m:
        return

    # Capture lines up to the next ## heading, stripping HTML-comment templates.
    tail = text[m.end():]
    next_h = re.search(r"^##\s+", tail, re.MULTILINE)
    section = tail[: next_h.start()] if next_h else tail
    section = re.sub(r"<!--.*?-->", "", section, flags=re.DOTALL)

    # Active entries are ### headings (one per injury); bullet lines are details.
    flags = [
        ln[4:].strip()
        for ln in section.splitlines()
        if ln.startswith("### ")
    ]

    if flags:
        console.print("[bold red]Active injury flags[/bold red]")
        for f in flags:
            console.print(f"  • {f}")


@vectors_app.command("rebuild")
def vectors_rebuild_cmd(
    force: bool = typer.Option(False, "--force", help="Re-embed even if file hash matches."),
    path: list[str] = typer.Option(
        None,
        "--path",
        help="Limit to specific .md files (repeatable). Default: all of knowledge/.",
    ),
) -> None:
    """Embed knowledge/ into data/vectors/knowledge.lance."""
    from pathlib import Path as _Path

    from .embed import rebuild

    console.print("[bold]Vectors[/bold] — embedding knowledge/…")
    targets = [_Path(p) for p in path] if path else None
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
