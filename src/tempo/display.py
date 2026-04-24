"""Rich-formatted ``coach status`` renderers.

Presentation concern, pulled out of ``cli.py`` so the CLI stays focused on
command dispatch. The CLI imports these; preflight scripts emit JSON directly
from ``tempo.queries`` and don't touch this module.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from rich.console import Console
from rich.table import Table

from .paths import repo_root

console = Console()


def print_load(conn: sqlite3.Connection) -> None:
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


def print_week(conn: sqlite3.Connection) -> None:
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


def print_wellness(conn: sqlite3.Connection) -> None:
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


def print_active_injuries() -> None:
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
    flags = [ln[4:].strip() for ln in section.splitlines() if ln.startswith("### ")]

    if flags:
        console.print("[bold red]Active injury flags[/bold red]")
        for f in flags:
            console.print(f"  • {f}")


__all__ = [
    "console",
    "print_active_injuries",
    "print_load",
    "print_week",
    "print_wellness",
]
