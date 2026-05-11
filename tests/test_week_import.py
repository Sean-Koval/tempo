"""Unit + CLI tests for ``tempo.week_import`` / ``coach week import``.

Covers the acceptance criteria from tempo-1q2:
- Happy path — parse + INSERT
- No-op re-import is a byte-no-op against the DB
- Edit propagation — markdown change → UPSERT
- pushed_to_intervals + intervals_event_id NOT clobbered
- Missing markdown / malformed YAML / no YAML blocks — clean errors
- Plan resolver: missing, single, ambiguous
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempo.cli import app
from tempo.db import connect, init_schema
from tempo.week_import import (
    ImportError as WeekImportError,
)
from tempo.week_import import (
    import_week,
    parse_week_markdown,
    resolve_plan_id,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect plans/, athlete/, data/ to an isolated tmp tree."""
    from tempo import paths as paths_mod
    from tempo import plans as plans_mod
    from tempo import week_import as wi_mod

    # Force repo_root() to point at tmp for every module that uses it.
    monkeypatch.setattr(paths_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(plans_mod, "repo_root", lambda: tmp_path)
    # week_import imports plans as _plans — the patch above already affects it.
    _ = wi_mod  # silence linter
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def db(isolated_root: Path):
    c = connect()
    init_schema(c)
    yield c
    c.close()


def _seed_plan(root: Path, *, plan_id: str = "demo-plan") -> Path:
    plan_dir = root / "plans" / plan_id
    (plan_dir / "weeks").mkdir(parents=True)
    (plan_dir / "plan.yaml").write_text(
        f"plan_id: {plan_id}\ngoal_ref: {plan_id}\nstart_date: 2026-05-04\n",
        encoding="utf-8",
    )
    return plan_dir


WEEK_MARKDOWN_BASIC = """\
---
week_id: 2026-W19
plan_id: demo-plan
---

# Week 2026-W19

## Sessions

### Monday 2026-05-04 — Strength A
- Duration: 45 min
- Purpose: posterior chain

```yaml tempo:session
id_slug: mon-strength-a
date: 2026-05-04
sport: strength
library_ref: strength_foundation
target_tss: 25
target_duration_s: 2700
purpose: Day A posterior chain
notes: Hip thrust 4x10
```

### Tuesday 2026-05-05 — Tempo
- Duration: 90 min

```yaml tempo:session
id_slug: tue-am-tempo
date: 2026-05-05
sport: bike
library_ref: tempo_bike_block
target_tss: 95
target_duration_s: 5400
purpose: sweet-spot intro
notes: 15 WU / 2x15 @ 76-88% FTP / 30 Z2 / 5 CD
```
"""


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_extracts_blocks(self):
        sessions = parse_week_markdown(WEEK_MARKDOWN_BASIC)
        assert len(sessions) == 2
        assert sessions[0].id_slug == "mon-strength-a"
        assert sessions[0].sport == "strength"
        assert sessions[0].target_tss == 25.0
        assert sessions[1].id_slug == "tue-am-tempo"
        assert sessions[1].target_duration_s == 5400

    def test_no_blocks_raises_with_format_hint(self):
        text = "# Some week\n\nJust prose, no yaml blocks."
        with pytest.raises(WeekImportError) as exc:
            parse_week_markdown(text)
        assert "tempo:session" in str(exc.value)

    def test_malformed_yaml_line_number(self):
        text = (
            "before\n"
            "```yaml tempo:session\n"
            "id_slug: foo\n"
            "date: 2026-05-04\n"
            "sport: bike\n"
            "    bad_indent_here: [unclosed\n"
            "```\n"
        )
        with pytest.raises(WeekImportError) as exc:
            parse_week_markdown(text)
        msg = str(exc.value)
        # The fence opens at line 2 — the error must surface a line
        # number anchored on the block so a human can find it.
        assert "line 2" in msg
        assert "YAML parse error" in msg

    def test_missing_required_field(self):
        text = (
            "```yaml tempo:session\n"
            "id_slug: foo\n"
            "date: 2026-05-04\n"
            "```\n"
        )
        with pytest.raises(WeekImportError) as exc:
            parse_week_markdown(text)
        assert "sport" in str(exc.value)
        assert "line 1" in str(exc.value)

    def test_unterminated_block(self):
        text = "```yaml tempo:session\nid_slug: foo\ndate: 2026-05-04\n"
        with pytest.raises(WeekImportError) as exc:
            parse_week_markdown(text)
        assert "Unterminated" in str(exc.value)

    def test_unknown_field_rejected(self):
        text = (
            "```yaml tempo:session\n"
            "id_slug: foo\n"
            "date: 2026-05-04\n"
            "sport: bike\n"
            "made_up_key: 1\n"
            "```\n"
        )
        with pytest.raises(WeekImportError) as exc:
            parse_week_markdown(text)
        assert "made_up_key" in str(exc.value)

    def test_duplicate_slug_rejected(self):
        text = (
            "```yaml tempo:session\n"
            "id_slug: dup\n"
            "date: 2026-05-04\n"
            "sport: bike\n"
            "```\n"
            "```yaml tempo:session\n"
            "id_slug: dup\n"
            "date: 2026-05-05\n"
            "sport: bike\n"
            "```\n"
        )
        with pytest.raises(WeekImportError) as exc:
            parse_week_markdown(text)
        assert "Duplicate" in str(exc.value)


# ---------------------------------------------------------------------------
# Plan resolution
# ---------------------------------------------------------------------------


class TestResolver:
    def test_explicit_plan_id_passes_through(self, isolated_root: Path):
        assert resolve_plan_id("my-plan") == "my-plan"

    def test_no_plans_under_root(self, isolated_root: Path):
        (isolated_root / "plans").mkdir()
        with pytest.raises(WeekImportError) as exc:
            resolve_plan_id(None)
        assert "No plan" in str(exc.value)

    def test_single_plan_auto_detect(self, isolated_root: Path):
        _seed_plan(isolated_root, plan_id="solo")
        assert resolve_plan_id(None) == "solo"

    def test_ambiguous_plan_errors(self, isolated_root: Path):
        _seed_plan(isolated_root, plan_id="plan-a")
        _seed_plan(isolated_root, plan_id="plan-b")
        with pytest.raises(WeekImportError) as exc:
            resolve_plan_id(None)
        assert "multiple" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# End-to-end import_week()
# ---------------------------------------------------------------------------


class TestImportWeek:
    def _write_week(self, root: Path, plan_id: str, week_id: str, text: str) -> Path:
        plan_dir = _seed_plan(root, plan_id=plan_id)
        path = plan_dir / "weeks" / f"{week_id}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_happy_path_inserts(self, isolated_root: Path, db):
        self._write_week(
            isolated_root, "demo-plan", "2026-W19", WEEK_MARKDOWN_BASIC
        )
        result = import_week(db, week_id="2026-W19", plan_id="demo-plan")
        assert result.n_inserted == 2
        assert result.n_updated == 0
        assert result.n_noop == 0

        rows = db.execute(
            "SELECT id, plan_id, week_id, sport, target_tss "
            "FROM sessions_planned ORDER BY id"
        ).fetchall()
        ids = [r["id"] for r in rows]
        assert ids == [
            "demo-plan/2026-W19/mon-strength-a",
            "demo-plan/2026-W19/tue-am-tempo",
        ]
        assert rows[0]["sport"] == "strength"
        assert rows[1]["target_tss"] == 95.0

    def test_idempotent_no_op_reimport(self, isolated_root: Path, db):
        self._write_week(
            isolated_root, "demo-plan", "2026-W19", WEEK_MARKDOWN_BASIC
        )
        import_week(db, week_id="2026-W19", plan_id="demo-plan")
        # Snapshot rowids — a no-op import must not bump them.
        before = db.execute(
            "SELECT id, rowid, plan_id, week_id, date, sport, library_ref, "
            "target_tss, target_duration_s, purpose, notes "
            "FROM sessions_planned ORDER BY id"
        ).fetchall()

        result = import_week(db, week_id="2026-W19", plan_id="demo-plan")
        assert result.n_noop == 2
        assert result.n_inserted == 0
        assert result.n_updated == 0
        assert not result.changed

        after = db.execute(
            "SELECT id, rowid, plan_id, week_id, date, sport, library_ref, "
            "target_tss, target_duration_s, purpose, notes "
            "FROM sessions_planned ORDER BY id"
        ).fetchall()
        assert [tuple(r) for r in before] == [tuple(r) for r in after]

    def test_edit_propagates_via_upsert(self, isolated_root: Path, db):
        path = self._write_week(
            isolated_root, "demo-plan", "2026-W19", WEEK_MARKDOWN_BASIC
        )
        import_week(db, week_id="2026-W19", plan_id="demo-plan")

        bumped = WEEK_MARKDOWN_BASIC.replace("target_tss: 95", "target_tss: 110")
        path.write_text(bumped, encoding="utf-8")

        result = import_week(db, week_id="2026-W19", plan_id="demo-plan")
        assert result.n_updated == 1
        assert result.n_noop == 1
        updated = next(c for c in result.changes if c.action == "update")
        assert "target_tss" in updated.diff_fields

        row = db.execute(
            "SELECT target_tss FROM sessions_planned "
            "WHERE id = 'demo-plan/2026-W19/tue-am-tempo'"
        ).fetchone()
        assert row["target_tss"] == 110.0

    def test_pushed_flag_preserved(self, isolated_root: Path, db):
        self._write_week(
            isolated_root, "demo-plan", "2026-W19", WEEK_MARKDOWN_BASIC
        )
        import_week(db, week_id="2026-W19", plan_id="demo-plan")

        # Simulate push-week landing — set the columns it owns.
        db.execute(
            "UPDATE sessions_planned SET pushed_to_intervals = 1, "
            "intervals_event_id = '12345' "
            "WHERE id = 'demo-plan/2026-W19/tue-am-tempo'"
        )

        bumped = WEEK_MARKDOWN_BASIC.replace("target_tss: 95", "target_tss: 110")
        (
            isolated_root / "plans" / "demo-plan" / "weeks" / "2026-W19.md"
        ).write_text(bumped, encoding="utf-8")

        import_week(db, week_id="2026-W19", plan_id="demo-plan")
        row = db.execute(
            "SELECT pushed_to_intervals, intervals_event_id, target_tss "
            "FROM sessions_planned "
            "WHERE id = 'demo-plan/2026-W19/tue-am-tempo'"
        ).fetchone()
        assert row["pushed_to_intervals"] == 1
        assert row["intervals_event_id"] == "12345"
        assert row["target_tss"] == 110.0

    def test_dry_run_does_not_write(self, isolated_root: Path, db):
        self._write_week(
            isolated_root, "demo-plan", "2026-W19", WEEK_MARKDOWN_BASIC
        )
        result = import_week(
            db, week_id="2026-W19", plan_id="demo-plan", dry_run=True
        )
        assert result.n_inserted == 2
        count = db.execute(
            "SELECT COUNT(*) AS n FROM sessions_planned"
        ).fetchone()["n"]
        assert count == 0

    def test_missing_markdown_clean_error(self, isolated_root: Path, db):
        _seed_plan(isolated_root, plan_id="demo-plan")
        with pytest.raises(WeekImportError) as exc:
            import_week(db, week_id="2026-W19", plan_id="demo-plan")
        assert "not found" in str(exc.value).lower()

    def test_no_yaml_blocks_clean_error(self, isolated_root: Path, db):
        self._write_week(
            isolated_root,
            "demo-plan",
            "2026-W19",
            "# Week 2026-W19\n\nNothing parseable here.\n",
        )
        with pytest.raises(WeekImportError) as exc:
            import_week(db, week_id="2026-W19", plan_id="demo-plan")
        assert "tempo:session" in str(exc.value)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCli:
    def _setup(self, isolated_root: Path) -> Path:
        plan_dir = _seed_plan(isolated_root, plan_id="demo-plan")
        wk = plan_dir / "weeks" / "2026-W19.md"
        wk.write_text(WEEK_MARKDOWN_BASIC, encoding="utf-8")
        return wk

    def test_import_dry_run(self, isolated_root: Path, tmp_data_dir: Path):
        self._setup(isolated_root)
        result = runner.invoke(
            app,
            ["week", "import", "2026-W19", "--plan-id", "demo-plan", "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        assert "2026-W19" in result.output
        # Dry-run shouldn't touch the DB.
        c = connect()
        init_schema(c)
        try:
            n = c.execute(
                "SELECT COUNT(*) AS n FROM sessions_planned"
            ).fetchone()["n"]
        finally:
            c.close()
        assert n == 0

    def test_import_applies(self, isolated_root: Path, tmp_data_dir: Path):
        self._setup(isolated_root)
        result = runner.invoke(
            app, ["week", "import", "2026-W19", "--plan-id", "demo-plan"]
        )
        assert result.exit_code == 0, result.output
        c = connect()
        init_schema(c)
        try:
            n = c.execute(
                "SELECT COUNT(*) AS n FROM sessions_planned"
            ).fetchone()["n"]
        finally:
            c.close()
        assert n == 2

    def test_import_missing_plan_exits_clean(
        self, isolated_root: Path, tmp_data_dir: Path
    ):
        result = runner.invoke(app, ["week", "import", "2026-W19"])
        assert result.exit_code != 0
        assert "No plan" in result.output or "plans/" in result.output
