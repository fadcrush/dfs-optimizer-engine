"""Tests for the DuckDB migration utility (analysis/shared/db.py)."""

from __future__ import annotations

import duckdb
import pytest

from analysis.shared.db import (
    migrate,
    get_schema_version,
    open_db,
    register_migration,
    _MIGRATIONS,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _open_mem() -> duckdb.DuckDBPyConnection:
    """Return an in-memory DuckDB connection (not persisted)."""
    return duckdb.connect(":memory:")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrate:
    def test_migrate_creates_schema_version_table(self):
        conn = _open_mem()
        migrate(conn, "dfs_edge")
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        assert "schema_version" in tables

    def test_schema_version_increases(self):
        conn = _open_mem()
        before = get_schema_version(conn)
        migrate(conn, "dfs_edge")
        after = get_schema_version(conn)
        assert after >= before

    def test_idempotent_double_migrate(self):
        conn = _open_mem()
        v1 = migrate(conn, "dfs_edge")
        v2 = migrate(conn, "dfs_edge")
        assert v1 == v2, "Second migration changed version unexpectedly"

    def test_unknown_db_key_returns_zero(self):
        conn = _open_mem()
        version = migrate(conn, "nonexistent_db")
        assert version == 0

    def test_all_registered_dbs_migrate_cleanly(self):
        for db_key in _MIGRATIONS:
            conn = _open_mem()
            version = migrate(conn, db_key)
            assert version > 0, f"Expected version > 0 for {db_key}"
            conn.close()


class TestGetSchemaVersion:
    def test_zero_before_migrate(self):
        conn = _open_mem()
        assert get_schema_version(conn) == 0

    def test_positive_after_migrate(self):
        conn = _open_mem()
        migrate(conn, "nba_news")
        assert get_schema_version(conn) >= 1


class TestOpenDb:
    def test_opens_file_and_creates_tables(self, tmp_path):
        db_path = tmp_path / "dfs_edge.duckdb"
        conn = open_db(db_path, db_key="dfs_edge")
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        assert "player_projections" in tables
        assert "schema_version" in tables
        conn.close()

    def test_db_file_created(self, tmp_path):
        db_path = tmp_path / "test.duckdb"
        assert not db_path.exists()
        conn = open_db(db_path, db_key="projection_cache")
        conn.close()
        assert db_path.exists()

    def test_stem_inferred_as_key(self, tmp_path):
        db_path = tmp_path / "dfs_edge.duckdb"
        conn = open_db(db_path)  # no db_key — inferred from stem
        version = get_schema_version(conn)
        assert version > 0
        conn.close()


class TestRegisterMigration:
    def test_new_migration_applied(self):
        conn = _open_mem()
        register_migration(
            "test_dynamic",
            1,
            "create dummy table",
            "CREATE TABLE IF NOT EXISTS dummy_table (id INTEGER PRIMARY KEY);",
        )
        migrate(conn, "test_dynamic")
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        assert "dummy_table" in tables

    def test_callable_migration(self):
        conn = _open_mem()

        def create_fn(c):
            c.execute("CREATE TABLE IF NOT EXISTS callable_tbl (x VARCHAR);")

        register_migration("test_callable", 1, "callable DDL", create_fn)
        migrate(conn, "test_callable")
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        assert "callable_tbl" in tables
