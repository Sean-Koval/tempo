"""Shared pytest fixtures for Tempo."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``tempo.paths.data_dir`` to an isolated tmp directory."""
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    yield tmp_path
