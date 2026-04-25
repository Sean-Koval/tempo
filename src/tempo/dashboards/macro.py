"""24-week macro timeline dashboard. Implemented under tempo-mvh.2."""

from __future__ import annotations

from pathlib import Path


def render_macro(plan_id: str | None = None, *, root: Path | None = None) -> str:
    raise NotImplementedError("tempo-mvh.2: render_macro lands in the next commit.")


__all__ = ["render_macro"]
