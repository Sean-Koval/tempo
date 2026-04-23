"""Filesystem path resolution for Tempo.

All derived artifacts (coach.db, raw/, vectors/, events.jsonl) live under
``<repo-root>/data/`` which is gitignored. Tests can override the root via
the ``TEMPO_DATA_DIR`` env var.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path


@cache
def repo_root() -> Path:
    """Locate the repo root by walking up for pyproject.toml."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml found).")


def data_dir() -> Path:
    """Root for all derived artifacts. Gitignored. Created on access."""
    override = os.environ.get("TEMPO_DATA_DIR")
    root = Path(override) if override else repo_root() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def coach_db_path() -> Path:
    return data_dir() / "coach.db"


def raw_dir(source: str) -> Path:
    d = data_dir() / "raw" / source
    d.mkdir(parents=True, exist_ok=True)
    return d


def events_log_path() -> Path:
    return data_dir() / "events.jsonl"
