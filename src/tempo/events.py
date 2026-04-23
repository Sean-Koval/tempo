"""Agentic-command event log at ``data/events.jsonl``.

Every deterministic CLI verb and agentic skill invocation appends one
line summarizing inputs and outputs. Read-only from the agent's
perspective — strictly audit/debug.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import events_log_path


def log_event(
    command: str,
    summary: dict[str, Any],
    now: datetime | None = None,
    path: Path | None = None,
) -> Path:
    """Append one ``{ts, command, summary}`` line to ``events.jsonl``."""
    ts = now or datetime.now(UTC)
    target = path or events_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": ts.isoformat(),
        "command": command,
        "summary": summary,
    }
    line = json.dumps(record, separators=(",", ":"), default=str)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return target
