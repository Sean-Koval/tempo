"""Tests for ``tempo.diagnostics`` — the ``coach doctor`` backend.

The HTTP-probing branch in ``check_intervals`` is exercised against a
stubbed httpx response so we don't hit the real API.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tempo import diagnostics


def _write_env(path: Path, **vals: str) -> None:
    path.write_text("\n".join(f"{k}={v}" for k, v in vals.items()) + "\n", encoding="utf-8")


@pytest.fixture
def repo_with_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``repo_root()`` at a tmp dir and clear intervals env vars."""
    monkeypatch.setattr(diagnostics, "repo_root", lambda: tmp_path)
    monkeypatch.delenv("INTERVALS_ICU_API_KEY", raising=False)
    monkeypatch.delenv("INTERVALS_ICU_ATHLETE_ID", raising=False)
    return tmp_path


def test_check_intervals_no_creds(repo_with_env: Path) -> None:
    result = diagnostics.check_intervals()
    assert result.status == "fail"
    assert "No credentials" in result.message


def test_check_intervals_missing_api_key(repo_with_env: Path) -> None:
    _write_env(repo_with_env / ".env", INTERVALS_ICU_ATHLETE_ID="i123456")
    result = diagnostics.check_intervals()
    assert result.status == "fail"
    assert "INTERVALS_ICU_API_KEY" in result.message


def test_check_intervals_malformed_athlete_id(repo_with_env: Path) -> None:
    _write_env(
        repo_with_env / ".env",
        INTERVALS_ICU_ATHLETE_ID="123456",  # missing leading 'i'
        INTERVALS_ICU_API_KEY="real-looking-key",
    )
    result = diagnostics.check_intervals()
    assert result.status == "fail"
    assert "malformed" in result.message
    assert "i123456" in result.message


def test_check_intervals_placeholder_key(repo_with_env: Path) -> None:
    _write_env(
        repo_with_env / ".env",
        INTERVALS_ICU_ATHLETE_ID="i123456",
        INTERVALS_ICU_API_KEY="your_api_key_here",
    )
    result = diagnostics.check_intervals()
    assert result.status == "fail"
    assert "placeholder" in result.message


@pytest.mark.parametrize(
    ("status_code", "expected_match"),
    [(401, "API key rejected"), (403, "cannot read athlete"), (404, "not found")],
)
def test_check_intervals_classifies_http_errors(
    repo_with_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_match: str,
) -> None:
    _write_env(
        repo_with_env / ".env",
        INTERVALS_ICU_ATHLETE_ID="i123456",
        INTERVALS_ICU_API_KEY="real-key",
    )

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(status_code=status_code, request=httpx.Request("GET", url))

    monkeypatch.setattr(diagnostics.httpx, "get", fake_get)

    result = diagnostics.check_intervals()
    assert result.status == "fail"
    assert expected_match in result.message
    assert result.detail.get("http_status") == status_code


def test_check_intervals_success(
    repo_with_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_env(
        repo_with_env / ".env",
        INTERVALS_ICU_ATHLETE_ID="i123456",
        INTERVALS_ICU_API_KEY="real-key",
    )

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"name": "Test Athlete"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(diagnostics.httpx, "get", fake_get)

    result = diagnostics.check_intervals()
    assert result.status == "ok"
    assert "Test Athlete" in result.message
    assert result.detail.get("athlete_id") == "i123456"


def test_check_coach_db_missing(tmp_data_dir: Path) -> None:
    result = diagnostics.check_coach_db()
    assert result.status == "warn"
    assert "does not exist" in result.message


def test_check_coach_db_empty(tmp_data_dir: Path) -> None:
    from tempo.db import connect, init_schema

    conn = connect()
    try:
        init_schema(conn)
    finally:
        conn.close()

    result = diagnostics.check_coach_db()
    assert result.status == "warn"
    assert "empty" in result.message


def test_check_active_plan_no_plans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tempo import plans as _plans

    monkeypatch.setattr(_plans, "plans_root", lambda root=None: tmp_path / "plans-empty")
    result = diagnostics.check_active_plan()
    assert result.status == "warn"
    assert "No plan" in result.message


def test_run_all_returns_each_check(repo_with_env: Path) -> None:
    results = diagnostics.run_all()
    names = [r.name for r in results]
    assert names == ["intervals.icu", "coach.db", "embedding model", "active plan"]
