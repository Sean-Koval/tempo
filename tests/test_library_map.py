"""Tests for ``tempo.library_map`` and ``coach library`` CLI verbs (Track A of tempo-d5e)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from intervals_icu_mcp.models import Workout
from typer.testing import CliRunner

from tempo.cli import app
from tempo.db import connect, init_schema
from tempo.library_map import (
    LibraryEntry,
    LibraryMappingError,
    delete_mapping,
    fuzzy_rank,
    get_mapping,
    list_session_library,
    score_status,
    upsert_mapping,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect repo_root() to a tmp tree owning knowledge/methodology/session-library/."""
    from tempo import library_map as lm_mod
    from tempo import paths as paths_mod
    from tempo import plans as plans_mod

    monkeypatch.setattr(paths_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(plans_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(lm_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    _seed_session_library(tmp_path)
    return tmp_path


def _seed_session_library(root: Path) -> None:
    base = root / "knowledge" / "methodology" / "session-library"
    base.mkdir(parents=True)
    (base / "bike.md").write_text(
        "---\nsport: bike\n---\n\n"
        "## Bike\n\n"
        "### `tempo_bike_block`\n- Purpose: foo\n\n"
        "### `long_ride_z2`\n- Purpose: bar\n",
        encoding="utf-8",
    )
    (base / "run.md").write_text(
        "---\nsport: run\n---\n\n"
        "## Run\n\n"
        "### `easy_aerobic_run`\n- Purpose: baz\n",
        encoding="utf-8",
    )
    (base / "strength.md").write_text(
        "---\nsport: strength\n---\n\n"
        "## Strength\n\n"
        "### `strength_foundation`\n- Purpose: qux\n",
        encoding="utf-8",
    )


@pytest.fixture
def db(isolated_root: Path):
    c = connect()
    init_schema(c)
    yield c
    c.close()


def _wk(
    *,
    id: int,
    name: str,
    folder_id: int | None = None,
    type_: str | None = None,
    tags: list[str] | None = None,
) -> Workout:
    return Workout(
        id=id,
        athlete_id="i1",
        name=name,
        description=None,
        folder_id=folder_id,
        moving_time=None,
        distance=None,
        icu_training_load=None,
        icu_intensity=None,
        joules=None,
        joules_above_ftp=None,
        indoor=None,
        color=None,
        type=type_,
        workout_doc=None,
        target=None,
        tags=tags,
        file_contents=None,
        filename=None,
        updated=None,
    )


# ---------------------------------------------------------------------------
# Session library parsing
# ---------------------------------------------------------------------------


class TestListSessionLibrary:
    def test_extracts_refs_with_sport(self, isolated_root: Path):
        refs = list_session_library()
        names = sorted(r.library_ref for r in refs)
        assert names == [
            "easy_aerobic_run",
            "long_ride_z2",
            "strength_foundation",
            "tempo_bike_block",
        ]
        by_name = {r.library_ref: r for r in refs}
        assert by_name["tempo_bike_block"].sport == "bike"
        assert by_name["easy_aerobic_run"].sport == "run"
        assert by_name["strength_foundation"].sport == "strength"


# ---------------------------------------------------------------------------
# Mapping CRUD
# ---------------------------------------------------------------------------


class TestMappingCrud:
    def test_upsert_inserts_then_updates(self, isolated_root: Path, db):
        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=42,
            intervals_name="Tempo block v1",
            intervals_folder_id=1,
            sport="bike",
        )
        row = get_mapping(db, library_ref="tempo_bike_block")
        assert row is not None
        assert row["intervals_workout_id"] == 42
        assert row["intervals_name"] == "Tempo block v1"

        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=42,
            intervals_name="Tempo block v2",
            intervals_folder_id=2,
            sport="bike",
        )
        row = get_mapping(db, library_ref="tempo_bike_block")
        assert row is not None
        assert row["intervals_name"] == "Tempo block v2"
        assert row["intervals_folder_id"] == 2

    def test_delete_mapping_returns_bool(self, isolated_root: Path, db):
        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=5,
            intervals_name="x",
            intervals_folder_id=None,
            sport="bike",
        )
        assert delete_mapping(db, library_ref="tempo_bike_block") is True
        assert get_mapping(db, library_ref="tempo_bike_block") is None
        # Re-deleting a now-absent row reports False.
        assert delete_mapping(db, library_ref="tempo_bike_block") is False


# ---------------------------------------------------------------------------
# Status classifier
# ---------------------------------------------------------------------------


class TestScoreStatus:
    def test_unmapped(self):
        entry = LibraryEntry(library_ref="tempo_bike_block", sport="bike", source_path=Path("x"))
        assert score_status(entry, mapping=None, intervals_ids=set()).status == "unmapped"

    def test_mapped(self):
        entry = LibraryEntry(library_ref="tempo_bike_block", sport="bike", source_path=Path("x"))
        m = {"intervals_workout_id": 42, "intervals_name": "Tempo block", "intervals_folder_id": None}
        result = score_status(entry, mapping=m, intervals_ids={42})
        assert result.status == "mapped"
        assert result.intervals_workout_id == 42

    def test_stale(self):
        entry = LibraryEntry(library_ref="tempo_bike_block", sport="bike", source_path=Path("x"))
        m = {"intervals_workout_id": 99, "intervals_name": "Gone", "intervals_folder_id": None}
        result = score_status(entry, mapping=m, intervals_ids={42, 43})
        assert result.status == "stale"

    def test_stale_skipped_when_ids_unknown(self):
        entry = LibraryEntry(library_ref="x", sport="bike", source_path=Path("x"))
        m = {"intervals_workout_id": 99, "intervals_name": "n", "intervals_folder_id": None}
        # intervals_ids=None means "we don't know" — never mark stale on the basis of a missing fetch.
        result = score_status(entry, mapping=m, intervals_ids=None)
        assert result.status == "mapped"


# ---------------------------------------------------------------------------
# Fuzzy ranking
# ---------------------------------------------------------------------------


class TestFuzzyRank:
    def test_substring_beats_unrelated(self):
        wks = [
            _wk(id=1, name="Easy spin"),
            _wk(id=2, name="Tempo Bike Block v1"),
            _wk(id=3, name="Long Z2 ride"),
        ]
        ranked = fuzzy_rank("tempo_bike_block", workouts=wks)
        assert ranked[0].id == 2

    def test_sport_underscore_stripped(self):
        wks = [
            _wk(id=10, name="long ride z2"),
            _wk(id=11, name="threshold"),
        ]
        ranked = fuzzy_rank("long_ride_z2", workouts=wks)
        assert ranked[0].id == 10


# ---------------------------------------------------------------------------
# CLI: list / show / unmap
# ---------------------------------------------------------------------------


class TestCliReadOnly:
    def test_library_list_all_unmapped(self, isolated_root: Path):
        result = runner.invoke(
            app,
            ["library", "list", "--no-fetch"],
        )
        assert result.exit_code == 0, result.output
        assert "tempo_bike_block" in result.output
        assert "unmapped" in result.output

    def test_library_list_with_mapping(self, isolated_root: Path, db):
        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=42,
            intervals_name="Tempo block v1",
            intervals_folder_id=None,
            sport="bike",
        )
        db.close()
        result = runner.invoke(
            app,
            ["library", "list", "--no-fetch"],
        )
        assert result.exit_code == 0, result.output
        assert "mapped" in result.output
        assert "42" in result.output

    def test_library_show_mapped(self, isolated_root: Path, db):
        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=42,
            intervals_name="Tempo block v1",
            intervals_folder_id=7,
            sport="bike",
        )
        db.close()
        result = runner.invoke(app, ["library", "show", "tempo_bike_block"])
        assert result.exit_code == 0, result.output
        assert "tempo_bike_block" in result.output
        assert "42" in result.output

    def test_library_show_unmapped_exits_nonzero(self, isolated_root: Path):
        result = runner.invoke(app, ["library", "show", "tempo_bike_block"])
        assert result.exit_code != 0
        assert "not mapped" in result.output.lower() or "no mapping" in result.output.lower()

    def test_library_show_unknown_ref_errors(self, isolated_root: Path):
        result = runner.invoke(app, ["library", "show", "made_up_ref"])
        assert result.exit_code != 0
        assert "not in the session library" in result.output.lower() or "unknown" in result.output.lower()

    def test_library_unmap_dry_run(self, isolated_root: Path, db):
        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=42,
            intervals_name="x",
            intervals_folder_id=None,
            sport="bike",
        )
        db.close()
        result = runner.invoke(
            app,
            ["library", "unmap", "tempo_bike_block", "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        # Should still be present.
        c = connect()
        init_schema(c)
        try:
            row = get_mapping(c, library_ref="tempo_bike_block")
        finally:
            c.close()
        assert row is not None

    def test_library_unmap_applies(self, isolated_root: Path, db):
        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=42,
            intervals_name="x",
            intervals_folder_id=None,
            sport="bike",
        )
        db.close()
        result = runner.invoke(app, ["library", "unmap", "tempo_bike_block"])
        assert result.exit_code == 0, result.output
        c = connect()
        init_schema(c)
        try:
            row = get_mapping(c, library_ref="tempo_bike_block")
        finally:
            c.close()
        assert row is None


# ---------------------------------------------------------------------------
# CLI: import (interactive flow, stubbed)
# ---------------------------------------------------------------------------


class TestCliImport:
    def test_import_dry_run_no_writes(
        self, isolated_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tempo import library_map as lm

        async def _fake_fetch(config: Any | None = None) -> list[Workout]:
            return [
                _wk(id=42, name="Tempo Bike Block v1", folder_id=10, type_="Ride"),
                _wk(id=43, name="Long Z2", folder_id=10, type_="Ride"),
            ]

        monkeypatch.setattr(lm, "fetch_intervals_workouts", _fake_fetch)
        # Pick the first suggestion on every prompt.
        monkeypatch.setattr(lm, "_default_picker", lambda *args, **kwargs: 1)

        result = runner.invoke(
            app,
            ["library", "import", "--dry-run", "--yes"],
        )
        assert result.exit_code == 0, result.output
        c = connect()
        init_schema(c)
        try:
            n = c.execute("SELECT COUNT(*) AS n FROM library_workout_map").fetchone()["n"]
        finally:
            c.close()
        assert n == 0

    def test_import_writes_mappings(
        self, isolated_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from tempo import library_map as lm

        async def _fake_fetch(config: Any | None = None) -> list[Workout]:
            return [
                _wk(id=42, name="Tempo Bike Block v1", folder_id=10, type_="Ride"),
                _wk(id=43, name="Long Z2 ride", folder_id=10, type_="Ride"),
                _wk(id=44, name="Easy aerobic run", folder_id=11, type_="Run"),
                _wk(id=45, name="Strength foundation A", folder_id=12, type_="WeightTraining"),
            ]

        monkeypatch.setattr(lm, "fetch_intervals_workouts", _fake_fetch)
        # Always confirm the top candidate.
        monkeypatch.setattr(lm, "_default_picker", lambda *args, **kwargs: 1)

        result = runner.invoke(app, ["library", "import", "--yes"])
        assert result.exit_code == 0, result.output

        c = connect()
        init_schema(c)
        try:
            rows = c.execute(
                "SELECT library_ref, intervals_workout_id FROM library_workout_map "
                "ORDER BY library_ref"
            ).fetchall()
        finally:
            c.close()
        as_dict = {r["library_ref"]: r["intervals_workout_id"] for r in rows}
        # All four refs should be mapped to the highest-similarity candidate.
        assert "tempo_bike_block" in as_dict
        assert as_dict["tempo_bike_block"] == 42
        assert as_dict["long_ride_z2"] == 43
        assert as_dict["easy_aerobic_run"] == 44
        assert as_dict["strength_foundation"] == 45


# ---------------------------------------------------------------------------
# Lookup helpers used by push
# ---------------------------------------------------------------------------


class TestLookupForPush:
    def test_resolve_workout_ids_for_refs(self, isolated_root: Path, db):
        upsert_mapping(
            db,
            library_ref="tempo_bike_block",
            intervals_workout_id=42,
            intervals_name="x",
            intervals_folder_id=None,
            sport="bike",
        )
        upsert_mapping(
            db,
            library_ref="long_ride_z2",
            intervals_workout_id=43,
            intervals_name="y",
            intervals_folder_id=None,
            sport="bike",
        )
        from tempo.library_map import lookup_workout_ids

        ids = lookup_workout_ids(db, refs=["tempo_bike_block", "long_ride_z2", "missing_ref"])
        assert ids == {"tempo_bike_block": 42, "long_ride_z2": 43}


class TestErrors:
    def test_upsert_rejects_missing_required(self, isolated_root: Path, db):
        with pytest.raises(LibraryMappingError):
            upsert_mapping(
                db,
                library_ref="",
                intervals_workout_id=1,
                intervals_name=None,
                intervals_folder_id=None,
                sport="bike",
            )
