"""Shared Rich ``console`` instance for the CLI.

The previous module hosted hand-rolled ``print_load`` / ``print_week`` /
``print_wellness`` / ``print_active_injuries`` helpers. Those have been
folded into :mod:`tempo.status` which assembles a typed
:class:`StatusSnapshot` and renders the whole thing in one place.
"""

from __future__ import annotations

from rich.console import Console

console = Console()


__all__ = ["console"]
