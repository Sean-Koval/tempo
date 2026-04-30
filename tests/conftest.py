"""Shared pytest fixtures for Tempo."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Keep tests off the dev ``.env``.

    ICUConfig now uses ``extra="ignore"`` so STRAVA_* keys are tolerated,
    but tests still construct configs with synthetic creds and shouldn't
    inherit whatever real keys live on disk. chdir into a clean tmp so
    pydantic-settings finds no ``.env`` to load.
    """
    monkeypatch.chdir(tmp_path_factory.mktemp("dotenv-isolated"))


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``tempo.paths.data_dir`` to an isolated tmp directory."""
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    yield tmp_path
