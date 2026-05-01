"""Tests for the `coach init` onboarding wizard."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from rich.console import Console

from tempo import init_wizard
from tempo.diagnostics import CheckResult
from tempo.init_wizard import (
    SectionStatus,
    WizardOptions,
    injury_complete,
    intervals_complete,
    preferences_complete,
    profile_complete,
    race_or_goal_complete,
    run_injury_section,
    run_preferences_section,
    run_profile_section,
    run_race_section,
    run_wizard,
)


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    (tmp_path / "athlete").mkdir()
    return tmp_path


@pytest.fixture
def quiet_console() -> Console:
    """Console that captures output without ANSI noise."""
    return Console(record=True, width=120, force_terminal=False)


@pytest.fixture
def stub_intervals_ok(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        "tempo.init_wizard.check_intervals",
        lambda: CheckResult(name="intervals.icu", status="ok", message="auth ok"),
    )
    yield


@pytest.fixture
def stub_intervals_fail(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        "tempo.init_wizard.check_intervals",
        lambda: CheckResult(
            name="intervals.icu",
            status="fail",
            message="No credentials found.",
            suggested_fix="Add INTERVALS_ICU_ATHLETE_ID and INTERVALS_ICU_API_KEY to .env.",
        ),
    )
    yield


# ---- Completeness checks ------------------------------------------------


def test_profile_incomplete_on_stub(fake_root: Path) -> None:
    (fake_root / "athlete" / "profile.yaml").write_text(
        "thresholds:\n  ftp_w:\n    value:\n  lthr_bpm:\n    value:\n",
        encoding="utf-8",
    )
    ok, why = profile_complete(root=fake_root)
    assert not ok
    assert "no thresholds" in why


def test_profile_complete_with_one_threshold(fake_root: Path) -> None:
    (fake_root / "athlete" / "profile.yaml").write_text(
        "thresholds:\n  ftp_w:\n    value: 250\n    set_at: 2026-04-01\n",
        encoding="utf-8",
    )
    ok, why = profile_complete(root=fake_root)
    assert ok
    assert "ftp_w" in why


def test_race_or_goal_incomplete_when_both_empty(fake_root: Path) -> None:
    (fake_root / "athlete" / "race-calendar.yaml").write_text("races: []\n", encoding="utf-8")
    (fake_root / "athlete" / "goals.yaml").write_text("goals: []\n", encoding="utf-8")
    ok, why = race_or_goal_complete(root=fake_root)
    assert not ok
    assert "no race" in why


def test_race_or_goal_complete_when_race_present(fake_root: Path) -> None:
    (fake_root / "athlete" / "race-calendar.yaml").write_text(
        "races:\n  - id: r1\n    date: 2026-09-01\n    priority: A\n",
        encoding="utf-8",
    )
    ok, _ = race_or_goal_complete(root=fake_root)
    assert ok


def test_preferences_incomplete_on_todo(fake_root: Path) -> None:
    (fake_root / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Schedule & logistics\n\n- Hours: # TODO\n",
        encoding="utf-8",
    )
    ok, why = preferences_complete(root=fake_root)
    assert not ok
    assert "TODO" in why


def test_preferences_complete_on_filled(fake_root: Path) -> None:
    (fake_root / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Schedule & logistics\n\n- Hours: 10\n",
        encoding="utf-8",
    )
    ok, _ = preferences_complete(root=fake_root)
    assert ok


def test_injury_complete_when_no_active(fake_root: Path) -> None:
    (fake_root / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n_No active flags._\n\n## Resolved\n",
        encoding="utf-8",
    )
    ok, why = injury_complete(root=fake_root)
    assert ok
    assert "no active" in why


def test_injury_incomplete_when_file_missing(fake_root: Path) -> None:
    ok, _ = injury_complete(root=fake_root)
    assert not ok


def test_intervals_complete_uses_diagnostics(stub_intervals_ok) -> None:
    ok, why = intervals_complete()
    assert ok
    assert "auth" in why


def test_intervals_incomplete_when_creds_missing(stub_intervals_fail) -> None:
    ok, why = intervals_complete()
    assert not ok
    assert "No credentials" in why


# ---- Section runners (interactive=True with mocked prompts) ------------


def test_run_profile_section_skipped_when_module_missing(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without tempo.profile_init the section short-circuits gracefully."""
    import builtins

    real_import = builtins.__import__

    def _no_profile_init(name, *args, **kwargs):
        if "profile_init" in name:
            raise ImportError("not yet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_profile_init)
    res = run_profile_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.SKIPPED
    assert "tempo-4us" in res.message


def test_run_race_section_writes_race(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(
        [
            "race",  # type
            "2026-im-lp",  # id
            "Ironman Lake Placid",  # name
            "2026-07-26",  # date
            "ironman",  # distance
            "A",  # priority
            "Lake Placid, NY",  # location
        ]
    )
    monkeypatch.setattr(
        "tempo.init_wizard.Prompt.ask", lambda *a, **kw: next(answers)
    )
    res = run_race_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.COMPLETE
    doc = yaml.safe_load((fake_root / "athlete" / "race-calendar.yaml").read_text())
    assert doc["races"][0]["id"] == "2026-im-lp"
    assert doc["races"][0]["distance"] == "ironman"


def test_run_race_section_writes_goal(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(
        [
            "goal",  # type
            "2026-ftp-280",  # id
            "ftp_w",  # metric
            "248",  # current
            "280",  # target
            "2026-08-16",  # by_date
        ]
    )
    monkeypatch.setattr(
        "tempo.init_wizard.Prompt.ask", lambda *a, **kw: next(answers)
    )
    res = run_race_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.COMPLETE
    doc = yaml.safe_load((fake_root / "athlete" / "goals.yaml").read_text())
    assert doc["goals"][0]["id"] == "2026-ftp-280"
    assert doc["goals"][0]["target"] == 280


def test_run_race_section_skipped_on_user_choice(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tempo.init_wizard.Prompt.ask", lambda *a, **kw: "skip")
    res = run_race_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.SKIPPED


def test_run_race_section_aborted_on_ctrl_c(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr("tempo.init_wizard.Prompt.ask", _raise)
    res = run_race_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.USER_ABORTED


def test_run_preferences_section_writes_section(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_answers = iter(
        [
            "Sat, Sun",  # long_days
            "Tue + Thu + Sat",  # hard_pattern
            "10:30pm — 6:30am",  # sleep_window
            "bike, run, swim",  # sport_priority
            "outdoor weekends; trainer midweek",  # indoor_outdoor
        ]
    )
    monkeypatch.setattr(
        "tempo.init_wizard.Prompt.ask", lambda *a, **kw: next(prompt_answers)
    )
    monkeypatch.setattr("tempo.init_wizard.IntPrompt.ask", lambda *a, **kw: 10)

    res = run_preferences_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.COMPLETE
    text = (fake_root / "athlete" / "preferences.md").read_text()
    assert "Sat, Sun" in text
    assert "Typical weekly training hours: 10" in text
    assert "TODO" not in text


def test_run_preferences_section_preserves_other_sections(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (fake_root / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Coaching style\n\n- Bias toward consistency.\n\n"
        "## Schedule & logistics\n\n- Hours: # TODO\n\n"
        "## Hard constraints\n\n- Respect injury-log flags.\n",
        encoding="utf-8",
    )
    answers = iter(["Sat", "Tue/Sat", "10pm-6am", "bike,run", "outdoor"])
    monkeypatch.setattr(
        "tempo.init_wizard.Prompt.ask", lambda *a, **kw: next(answers)
    )
    monkeypatch.setattr("tempo.init_wizard.IntPrompt.ask", lambda *a, **kw: 8)
    res = run_preferences_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.COMPLETE
    text = (fake_root / "athlete" / "preferences.md").read_text()
    assert "Bias toward consistency" in text
    assert "Respect injury-log flags" in text
    assert "TODO" not in text


def test_run_injury_section_no_injuries(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tempo.init_wizard.Confirm.ask", lambda *a, **kw: False)
    res = run_injury_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.COMPLETE
    text = (fake_root / "athlete" / "injury-log.md").read_text()
    assert "## Active" in text
    assert "_No active flags._" in text


def test_run_injury_section_records_active_injury(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tempo.init_wizard.Confirm.ask", lambda *a, **kw: True)
    answers = iter(
        [
            "2026-04-25",  # date
            "left tibia BSI g2",  # body part
            "no impact loading",  # symptoms
            "no running for 6 weeks",  # constraints
        ]
    )
    monkeypatch.setattr(
        "tempo.init_wizard.Prompt.ask", lambda *a, **kw: next(answers)
    )
    monkeypatch.setattr("tempo.init_wizard.IntPrompt.ask", lambda *a, **kw: 4)
    res = run_injury_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.COMPLETE
    text = (fake_root / "athlete" / "injury-log.md").read_text()
    assert "left tibia BSI g2" in text
    assert "severity 4" in text
    # And the parser sees it.
    from tempo import athlete

    flags = athlete.active_injury_flags(root=fake_root)
    assert any("left tibia" in f for f in flags)


def test_run_injury_section_skips_when_already_populated(
    fake_root: Path,
    quiet_console: Console,
) -> None:
    (fake_root / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n### 2026-04-10 — calf — 3\n- Status: active\n\n## Resolved\n",
        encoding="utf-8",
    )
    res = run_injury_section(root=fake_root, console=quiet_console)
    assert res.status == SectionStatus.COMPLETE
    assert "1 active" in res.message


# ---- Validate-only orchestrator ----------------------------------------


def test_validate_only_zero_when_all_complete(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
    stub_intervals_ok,
) -> None:
    _seed_complete(fake_root)
    monkeypatch.setattr(init_wizard, "_default_console", quiet_console)
    opts = WizardOptions(validate_only=True)
    _, code = run_wizard(options=opts, root=fake_root, console=quiet_console)
    assert code == 0


def test_validate_only_nonzero_on_partial(
    fake_root: Path,
    quiet_console: Console,
    stub_intervals_fail,
) -> None:
    # Only profile populated; everything else missing.
    (fake_root / "athlete" / "profile.yaml").write_text(
        "thresholds:\n  ftp_w:\n    value: 250\n    set_at: 2026-04-01\n",
        encoding="utf-8",
    )
    opts = WizardOptions(validate_only=True)
    _, code = run_wizard(options=opts, root=fake_root, console=quiet_console)
    assert code == 1


# ---- --resume orchestrator ---------------------------------------------


def test_resume_skips_complete_and_runs_first_incomplete(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
    stub_intervals_ok,
) -> None:
    """Profile + race + preferences pre-seeded; injury + creds + sync drive the run."""
    _seed_profile(fake_root)
    _seed_race(fake_root)
    _seed_preferences(fake_root)
    # injury, intervals (stubbed ok), then sync/status — sync we stub too.

    # Injury: no active flag
    monkeypatch.setattr("tempo.init_wizard.Confirm.ask", lambda *a, **kw: False)

    called: dict[str, bool] = {}

    async def _fake_sync(days: int = 90):
        called["sync"] = True

        class S:
            activities_upserted = 0
            wellness_upserted = 0
            duration_ms = 1

        return S()

    def _fake_derive():
        called["derive"] = True

        class D:
            days_written = 0

        return D()

    monkeypatch.setattr("tempo.sync.sync", _fake_sync)
    monkeypatch.setattr("tempo.derive.derive", _fake_derive)

    # Status section will hit the real DB; isolate via TEMPO_DATA_DIR.
    monkeypatch.setenv("TEMPO_DATA_DIR", str(fake_root / ".data"))

    opts = WizardOptions(resume=True, sync_days=1)
    results, code = run_wizard(options=opts, root=fake_root, console=quiet_console)

    by_name = {r.name: r for r in results}
    assert by_name["profile"].status == SectionStatus.COMPLETE
    assert "already complete" in by_name["profile"].message
    assert by_name["race_or_goal"].status == SectionStatus.COMPLETE
    assert "already complete" in by_name["race_or_goal"].message
    assert by_name["preferences"].status == SectionStatus.COMPLETE
    assert called.get("sync") is True
    assert code == 0


def test_resume_aborts_cleanly_on_ctrl_c(
    fake_root: Path,
    quiet_console: Console,
    monkeypatch: pytest.MonkeyPatch,
    stub_intervals_ok,
) -> None:
    _seed_profile(fake_root)

    def _raise(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr("tempo.init_wizard.Prompt.ask", _raise)
    opts = WizardOptions(resume=True)
    results, code = run_wizard(options=opts, root=fake_root, console=quiet_console)
    assert code == 1
    # First incomplete = race_or_goal; aborted there.
    assert any(
        r.name == "race_or_goal" and r.status == SectionStatus.USER_ABORTED for r in results
    )


# ---- Helpers -----------------------------------------------------------


def _seed_profile(root: Path) -> None:
    (root / "athlete" / "profile.yaml").write_text(
        "thresholds:\n  ftp_w:\n    value: 250\n    set_at: 2026-04-01\n"
        "    source: field_test\n",
        encoding="utf-8",
    )


def _seed_race(root: Path) -> None:
    (root / "athlete" / "race-calendar.yaml").write_text(
        "races:\n  - id: r1\n    name: race\n    date: 2026-09-01\n"
        "    distance: ironman\n    priority: A\n",
        encoding="utf-8",
    )


def _seed_preferences(root: Path) -> None:
    (root / "athlete" / "preferences.md").write_text(
        "# Prefs\n\n## Schedule & logistics\n\n- Hours: 10\n- Long days: Sat, Sun\n",
        encoding="utf-8",
    )


def _seed_injury(root: Path) -> None:
    (root / "athlete" / "injury-log.md").write_text(
        "# Log\n\n## Active\n\n_No active flags._\n\n## Resolved\n",
        encoding="utf-8",
    )


def _seed_complete(root: Path) -> None:
    _seed_profile(root)
    _seed_race(root)
    _seed_preferences(root)
    _seed_injury(root)
