"""Shared pytest fixtures for Tempo."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Prevent pydantic-settings from picking up the dev ``.env``.

    The repo's ``.env`` carries credentials for several MCP servers
    (intervals, strava). ICUConfig declares only intervals fields with
    ``extra="forbid"``, so loading it under tests with the real ``.env``
    on disk fails with extra-field errors. Tests construct ``ICUConfig``
    directly with synthetic creds; we just need to keep the file out of
    the way.
    """
    # Strip every STRAVA_* var so ICUConfig won't see them via env.
    for key in list(os.environ):
        if key.startswith("STRAVA_"):
            monkeypatch.delenv(key, raising=False)
    # Pretend cwd has no .env by chdir'ing into a clean tmp dir.
    monkeypatch.chdir(tmp_path_factory.mktemp("dotenv-isolated"))


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``tempo.paths.data_dir`` to an isolated tmp directory."""
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    yield tmp_path
