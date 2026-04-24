"""FastMCP server entry point.

Tool modules register themselves by importing this module's ``mcp`` instance
and decorating functions with ``@mcp.tool``. Keeping registration opt-in per
module means tests can import ``mcp`` without pulling every dependency.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

mcp: FastMCP = FastMCP(
    name="coach-db",
    instructions=(
        "Typed tools over Tempo's SQLite (coach.db) and LanceDB substrates. "
        "Use for historical queries, adherence, load curves, knowledge search, "
        "memory recall, and logging coaching decisions."
    ),
)


@mcp.tool
def ping() -> dict[str, Any]:
    """Sentinel tool — confirms the server is wired and reachable."""
    return {"status": "ok", "server": "coach-db", "version": "0.1.0"}


def main() -> None:
    """Entry point declared in pyproject scripts — stdio transport for Claude Code."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
