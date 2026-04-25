"""Decision-trace dashboard. Implemented under tempo-mvh.3."""

from __future__ import annotations

from pathlib import Path


def render_decisions(
    scope: str | None = None,
    since: str | None = None,
    *,
    root: Path | None = None,
) -> str:
    raise NotImplementedError("tempo-mvh.3: render_decisions lands in the next commit.")


__all__ = ["render_decisions"]
