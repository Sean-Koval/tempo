"""Append-only JSONL audit trail of raw API responses.

Every upstream response (intervals, strava) is persisted verbatim to
``data/raw/<source>/YYYY-MM-DD.jsonl`` so that ``coach.db`` is fully
rebuildable from this trail plus ``plans/``. Writes are append-only and
O(1) — never read prior content.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import raw_dir


def append_raw(
    source: str,
    endpoint: str,
    response: Any,
    params: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Path:
    """Append one JSON line to ``data/raw/<source>/YYYY-MM-DD.jsonl``.

    Args:
        source: Upstream identifier (``"intervals"``, ``"strava"``).
        endpoint: API path or logical operation name, e.g. ``"/activities"``.
        response: JSON-serializable payload (dict or list) from upstream.
        params: Request parameters for reproducibility.
        now: Timestamp override for testing; defaults to UTC now.

    Returns:
        Absolute path to the file the line was appended to.
    """
    ts = now or datetime.now(UTC)
    day = ts.date().isoformat()
    path = raw_dir(source) / f"{day}.jsonl"

    record = {
        "ts": ts.isoformat(),
        "source": source,
        "endpoint": endpoint,
        "params": params or {},
        "response": response,
    }
    line = json.dumps(record, separators=(",", ":"), default=str)
    if "\n" in line:
        raise ValueError("Serialized payload contains a newline; refusing to corrupt JSONL.")

    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path
