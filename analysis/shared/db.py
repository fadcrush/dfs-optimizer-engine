"""
DuckDB schema migration utility.

Each DuckDB file used by the analysis layer carries a ``schema_version`` table
that tracks which migrations have been applied.  Call ``migrate(conn)`` at
startup to bring any database up to the current schema automatically.

Usage::

    import duckdb
    from analysis.shared.db import migrate, open_db

    conn = open_db("data/dfs_edge.duckdb")          # opens + migrates
    conn = open_db("data/custom.duckdb", db_key="custom")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import duckdb

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------
# Each entry: (version: int, description: str, up_sql: str | Callable)
# Callable receives a duckdb.DuckDBPyConnection and may execute arbitrary DDL.
# ---------------------------------------------------------------------------

_MigrationFn = Callable[[duckdb.DuckDBPyConnection], None]

_MIGRATIONS: dict[str, list[tuple[int, str, str | _MigrationFn]]] = {
    # ------------------------------------------------------------------ #
    # dfs_edge.duckdb                                                      #
    # ------------------------------------------------------------------ #
    "dfs_edge": [
        (
            1,
            "initial player_projections table",
            """
            CREATE TABLE IF NOT EXISTS player_projections (
                id            INTEGER PRIMARY KEY,
                slate_date    DATE NOT NULL,
                sport         VARCHAR(8) NOT NULL,
                site          VARCHAR(8) NOT NULL,
                player_name   VARCHAR(100) NOT NULL,
                dfs_id        VARCHAR(40),
                position      VARCHAR(12),
                team          VARCHAR(8),
                salary        FLOAT,
                projection    FLOAT,
                ownership_pct FLOAT,
                created_at    TIMESTAMPTZ DEFAULT now()
            );
            """,
        ),
        (
            2,
            "add vegas columns to player_projections",
            """
            ALTER TABLE player_projections
                ADD COLUMN IF NOT EXISTS team_total   FLOAT;
            ALTER TABLE player_projections
                ADD COLUMN IF NOT EXISTS spread       FLOAT;
            ALTER TABLE player_projections
                ADD COLUMN IF NOT EXISTS vegas_boost  FLOAT;
            """,
        ),
        (
            3,
            "lineup_results table",
            """
            CREATE TABLE IF NOT EXISTS lineup_results (
                id            INTEGER PRIMARY KEY,
                slate_date    DATE NOT NULL,
                sport         VARCHAR(8),
                site          VARCHAR(8),
                lineup_json   JSON,
                total_salary  FLOAT,
                total_proj    FLOAT,
                actual_score  FLOAT,
                created_at    TIMESTAMPTZ DEFAULT now()
            );
            """,
        ),
        (
            4,
            "add user_id to player_projections and lineup_results",
            """
            ALTER TABLE player_projections ADD COLUMN IF NOT EXISTS user_id VARCHAR DEFAULT '';
            ALTER TABLE lineup_results ADD COLUMN IF NOT EXISTS user_id VARCHAR DEFAULT '';
            """,
        ),
        (
            5,
            "projection_snapshots table for backtesting",
            """
            CREATE TABLE IF NOT EXISTS projection_snapshots (
                slate_date   DATE        NOT NULL,
                site         VARCHAR     NOT NULL,
                player_slug  VARCHAR     NOT NULL,
                player_name  VARCHAR,
                proj         DOUBLE,
                std_dev      DOUBLE,
                floor_val    DOUBLE,
                ceiling_val  DOUBLE,
                salary       INTEGER,
                snapped_at   TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (slate_date, site, player_slug)
            );
            """,
        ),
        (
            6,
            "projection_accuracy_log table for backtesting",
            """
            CREATE TABLE IF NOT EXISTS projection_accuracy_log (
                run_date      DATE    NOT NULL,
                site          VARCHAR NOT NULL,
                n_players     INTEGER,
                mae           DOUBLE,
                rmse          DOUBLE,
                bias          DOUBLE,
                r_squared     DOUBLE,
                pct_within_5  DOUBLE,
                pct_within_10 DOUBLE,
                computed_at   TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (run_date, site)
            );
            """,
        ),
        (
            7,
            "player_positions table for position-specific DvP",
            """
            CREATE TABLE IF NOT EXISTS player_positions (
                player_slug  VARCHAR  NOT NULL,
                player_name  VARCHAR,
                position     VARCHAR  NOT NULL,
                site         VARCHAR  NOT NULL,
                updated_at   TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (player_slug, site)
            );
            """,
        ),
    ],

    # ------------------------------------------------------------------ #
    # dfs_master.duckdb                                                    #
    # ------------------------------------------------------------------ #
    "dfs_master": [
        (
            1,
            "slates table",
            """
            CREATE TABLE IF NOT EXISTS slates (
                slate_id     VARCHAR(40) PRIMARY KEY,
                sport        VARCHAR(8)  NOT NULL,
                site         VARCHAR(8)  NOT NULL,
                slate_date   DATE,
                file_path    VARCHAR(256),
                imported_at  TIMESTAMPTZ DEFAULT now()
            );
            """,
        ),
        (
            2,
            "ownership_history table",
            """
            CREATE TABLE IF NOT EXISTS ownership_history (
                id            INTEGER PRIMARY KEY,
                slate_date    DATE,
                sport         VARCHAR(8),
                site          VARCHAR(8),
                player_name   VARCHAR(100),
                dfs_id        VARCHAR(40),
                salary        FLOAT,
                projection    FLOAT,
                ownership_pct FLOAT,
                actual_score  FLOAT,
                created_at    TIMESTAMPTZ DEFAULT now()
            );
            """,
        ),
    ],

    # ------------------------------------------------------------------ #
    # projection_cache.duckdb                                              #
    # ------------------------------------------------------------------ #
    "projection_cache": [
        (
            1,
            "projection_cache table",
            """
            CREATE TABLE IF NOT EXISTS projection_cache (
                slate_id    VARCHAR(80)  NOT NULL,
                sport       VARCHAR(8)   NOT NULL,
                site        VARCHAR(8)   NOT NULL,
                payload     JSON         NOT NULL,
                created_at  TIMESTAMPTZ  DEFAULT now(),
                expires_at  TIMESTAMPTZ,
                PRIMARY KEY (slate_id, sport, site)
            );
            """,
        ),
        (
            2,
            "projection_cache: add row_count column",
            "ALTER TABLE projection_cache ADD COLUMN IF NOT EXISTS row_count INTEGER DEFAULT 0;",
        ),
    ],

    # ------------------------------------------------------------------ #
    # contest_results.duckdb                                              #
    # ------------------------------------------------------------------ #
    "contest_results": [
        (
            1,
            "contest_results table",
            """
            CREATE TABLE IF NOT EXISTS contest_results (
                id              INTEGER PRIMARY KEY,
                slate_date      DATE,
                sport           VARCHAR(8),
                site            VARCHAR(8),
                contest_name    VARCHAR(200),
                entry_fee       FLOAT,
                winnings        FLOAT,
                rank            INTEGER,
                total_entrants  INTEGER,
                lineup_json     JSON,
                actual_score    FLOAT,
                imported_at     TIMESTAMPTZ DEFAULT now()
            );
            """,
        ),
    ],

    # ------------------------------------------------------------------ #
    # nba_news.duckdb                                                      #
    # ------------------------------------------------------------------ #
    "nba_news": [
        (
            1,
            "injury_reports table",
            """
            CREATE TABLE IF NOT EXISTS injury_reports (
                id            INTEGER PRIMARY KEY,
                player_name   VARCHAR(100),
                team          VARCHAR(8),
                status        VARCHAR(40),
                note          TEXT,
                source        VARCHAR(80),
                reported_at   TIMESTAMPTZ DEFAULT now()
            );
            """,
        ),
        (
            2,
            "lineup_announcements table",
            """
            CREATE TABLE IF NOT EXISTS lineup_announcements (
                id            INTEGER PRIMARY KEY,
                player_name   VARCHAR(100) NOT NULL,
                status        VARCHAR(40)  NOT NULL,
                source        VARCHAR(80),
                announced_at  TIMESTAMPTZ  DEFAULT now()
            );
            """,
        ),
        (
            3,
            "add game_date to lineup_announcements",
            """
            ALTER TABLE lineup_announcements
                ADD COLUMN IF NOT EXISTS game_date DATE;
            """,
        ),
        (
            4,
            "injury_events table",
            """
            CREATE TABLE IF NOT EXISTS injury_events (
                event_id                 VARCHAR PRIMARY KEY,
                player_id                VARCHAR NOT NULL,
                player_name              VARCHAR(100) NOT NULL,
                team_id                  VARCHAR(12),
                game_id                  VARCHAR(64),
                source                   VARCHAR(80) NOT NULL,
                source_priority          INTEGER NOT NULL,
                raw_status               VARCHAR(80),
                normalized_status        VARCHAR(40),
                detail_text              TEXT,
                parser_confidence        DOUBLE DEFAULT 0.5,
                observed_at              TIMESTAMPTZ NOT NULL,
                effective_at             TIMESTAMPTZ,
                is_retraction            BOOLEAN DEFAULT FALSE,
                correlation_id           VARCHAR(80),
                market_impact_estimate   DOUBLE DEFAULT 0.0,
                event_priority_score     DOUBLE DEFAULT 0.0,
                news_quality_score       DOUBLE DEFAULT 0.0,
                event_classification     VARCHAR(32) DEFAULT 'pending',
                created_at               TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_injury_events_player_observed
                ON injury_events(player_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_injury_events_priority
                ON injury_events(event_priority_score, observed_at);
            """,
        ),
        (
            5,
            "player_injury_state table",
            """
            CREATE TABLE IF NOT EXISTS player_injury_state (
                player_id                VARCHAR PRIMARY KEY,
                player_name              VARCHAR(100) NOT NULL,
                team_id                  VARCHAR(12),
                game_id                  VARCHAR(64),
                current_status           VARCHAR(40),
                p_play                   DOUBLE DEFAULT 1.0,
                expected_minutes_low     DOUBLE DEFAULT 0.0,
                expected_minutes_mid     DOUBLE DEFAULT 0.0,
                expected_minutes_high    DOUBLE DEFAULT 0.0,
                p_start                  DOUBLE DEFAULT 0.0,
                p_limited                DOUBLE DEFAULT 0.0,
                p_late_scratch           DOUBLE DEFAULT 0.0,
                confidence_score         DOUBLE DEFAULT 0.0,
                news_quality_score       DOUBLE DEFAULT 0.0,
                source_agreement_score   DOUBLE DEFAULT 0.0,
                staleness_score          DOUBLE DEFAULT 0.0,
                last_event_at            TIMESTAMPTZ,
                state_version            BIGINT DEFAULT 1,
                arbitration_context      JSON,
                updated_at               TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_player_injury_state_team
                ON player_injury_state(team_id, current_status);
            """,
        ),
        (
            6,
            "injury_scenarios table",
            """
            CREATE TABLE IF NOT EXISTS injury_scenarios (
                scenario_id              VARCHAR PRIMARY KEY,
                player_id                VARCHAR NOT NULL,
                scenario_name            VARCHAR(32) NOT NULL,
                scenario_probability     DOUBLE NOT NULL,
                expected_minutes         DOUBLE DEFAULT 0.0,
                usage_multiplier         DOUBLE DEFAULT 1.0,
                volatility_multiplier    DOUBLE DEFAULT 1.0,
                p_start                  DOUBLE DEFAULT 0.0,
                generated_at             TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_injury_scenarios_player
                ON injury_scenarios(player_id, scenario_name);
            """,
        ),
        (
            7,
            "injury_beneficiaries table",
            """
            CREATE TABLE IF NOT EXISTS injury_beneficiaries (
                beneficiary_id           VARCHAR PRIMARY KEY,
                player_id                VARCHAR NOT NULL,
                beneficiary_player_id    VARCHAR NOT NULL,
                beneficiary_name         VARCHAR(100) NOT NULL,
                team_id                  VARCHAR(12),
                scenario_name            VARCHAR(32) NOT NULL,
                delta_minutes            DOUBLE DEFAULT 0.0,
                delta_usage              DOUBLE DEFAULT 0.0,
                delta_assist_rate        DOUBLE DEFAULT 0.0,
                delta_rebound_rate       DOUBLE DEFAULT 0.0,
                p_start                  DOUBLE DEFAULT 0.0,
                p_close                  DOUBLE DEFAULT 0.0,
                volatility_uplift        DOUBLE DEFAULT 0.0,
                confidence               DOUBLE DEFAULT 0.0,
                rank_score               DOUBLE DEFAULT 0.0,
                generated_at             TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_injury_beneficiaries_player
                ON injury_beneficiaries(player_id, scenario_name);
            CREATE INDEX IF NOT EXISTS idx_injury_beneficiaries_beneficiary
                ON injury_beneficiaries(beneficiary_player_id, rank_score);
            """,
        ),
        (
            8,
            "ownership_scenarios table",
            """
            CREATE TABLE IF NOT EXISTS ownership_scenarios (
                ownership_scenario_id    VARCHAR PRIMARY KEY,
                player_id                VARCHAR NOT NULL,
                site                     VARCHAR(8) NOT NULL,
                scenario_name            VARCHAR(32) NOT NULL,
                ownership_pct            DOUBLE DEFAULT 0.0,
                weighted_ownership_pct   DOUBLE DEFAULT 0.0,
                reaction_lag_minutes     DOUBLE DEFAULT 0.0,
                generated_at             TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_ownership_scenarios_player
                ON ownership_scenarios(player_id, site, scenario_name);
            """,
        ),
        (
            9,
            "swap_option_metrics table",
            """
            CREATE TABLE IF NOT EXISTS swap_option_metrics (
                swap_metric_id           VARCHAR PRIMARY KEY,
                slate_id                 VARCHAR(80),
                site                     VARCHAR(8) NOT NULL,
                player_id                VARCHAR NOT NULL,
                swap_option_value        DOUBLE DEFAULT 0.0,
                valid_pivots_remaining   INTEGER DEFAULT 0,
                pivot_quality            DOUBLE DEFAULT 0.0,
                ownership_leverage       DOUBLE DEFAULT 0.0,
                time_remaining_weight    DOUBLE DEFAULT 0.0,
                updated_at               TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_swap_option_metrics_player
                ON swap_option_metrics(slate_id, site, player_id);
            """,
        ),
        (
            10,
            "add reason_codes and position to injury_beneficiaries; add position to player_injury_state; add position to injury_events",
            """
            ALTER TABLE injury_beneficiaries
                ADD COLUMN IF NOT EXISTS position        VARCHAR(12) DEFAULT '';
            ALTER TABLE injury_beneficiaries
                ADD COLUMN IF NOT EXISTS reason_codes    JSON DEFAULT '[]';
            ALTER TABLE player_injury_state
                ADD COLUMN IF NOT EXISTS position        VARCHAR(12) DEFAULT '';
            ALTER TABLE injury_events
                ADD COLUMN IF NOT EXISTS position        VARCHAR(12) DEFAULT '';
            """,
        ),
        (
            11,
            "user_injury_overrides table",
            """
            CREATE TABLE IF NOT EXISTS user_injury_overrides (
                override_id              VARCHAR PRIMARY KEY,
                user_id                  VARCHAR NOT NULL,
                slate_id                 VARCHAR(80),
                player_id                VARCHAR NOT NULL,
                player_name              VARCHAR(100),
                action                   VARCHAR(16) NOT NULL,
                projection_bump          DOUBLE DEFAULT 0.0,
                ownership_adjustment     DOUBLE DEFAULT 0.0,
                exclude_from_pool        BOOLEAN DEFAULT FALSE,
                notes                    TEXT DEFAULT '',
                created_at               TIMESTAMPTZ DEFAULT now(),
                updated_at               TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_user_injury_overrides_user_slate
                ON user_injury_overrides(user_id, slate_id);
            CREATE INDEX IF NOT EXISTS idx_user_injury_overrides_player
                ON user_injury_overrides(player_id);
            """,
        ),
    ],
}

# Shared migrations applied to every database (sentinel table only)
_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version      INTEGER NOT NULL,
    description  VARCHAR(200),
    applied_at   TIMESTAMPTZ DEFAULT now()
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def migrate(conn: duckdb.DuckDBPyConnection, db_key: str) -> int:
    """Apply all pending migrations for *db_key* to *conn*.

    Returns the final schema version.
    """
    conn.execute(_SCHEMA_VERSION_DDL)

    applied: set[int] = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_version").fetchall()
    }

    migrations = _MIGRATIONS.get(db_key, [])
    new_applied = 0

    for version, description, up in sorted(migrations, key=lambda m: m[0]):
        if version in applied:
            continue
        log.info("[db_migrations] %s v%d — %s", db_key, version, description)
        try:
            if callable(up):
                up(conn)
            else:
                # Execute potentially multi-statement DDL
                for stmt in up.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)

            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                [version, description],
            )
            new_applied += 1
        except Exception as exc:
            log.error("[db_migrations] FAILED applying v%d to %s: %s", version, db_key, exc)
            raise

    if new_applied:
        log.info("[db_migrations] %s: %d migration(s) applied.", db_key, new_applied)
    else:
        log.debug("[db_migrations] %s: schema already current.", db_key)

    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    return int(current)


def get_schema_version(conn: duckdb.DuckDBPyConnection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    try:
        result = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(result[0]) if result and result[0] is not None else 0
    except Exception:
        return 0


def open_db(path: str | Path, db_key: str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection and auto-migrate.

    *db_key* defaults to the stem of *path* (e.g. ``"dfs_edge"`` for
    ``data/dfs_edge.duckdb``).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = db_key or path.stem
    conn = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        migrate(conn, key)
    return conn


def register_migration(db_key: str, version: int, description: str, up: str | _MigrationFn) -> None:
    """Dynamically register an additional migration at runtime.

    Third-party modules can call this before the first ``open_db`` /
    ``migrate`` call to extend the migration set.
    """
    _MIGRATIONS.setdefault(db_key, []).append((version, description, up))
    _MIGRATIONS[db_key].sort(key=lambda m: m[0])


# ---------------------------------------------------------------------------
# Per-process connection singleton registry
# ---------------------------------------------------------------------------
# DuckDB only allows ONE writer per file across OS processes.  With uvicorn
# --workers N > 1, each worker is a separate process that would compete to
# hold an exclusive write lock — causing "Could not set lock" errors.
#
# This registry ensures:
#   • Within a single process, connections are opened once and reused.
#   • Each forked worker process gets its own fresh connections (pid-keyed).
#   • Thread-safe via a per-process lock.
# ---------------------------------------------------------------------------

import os
import threading
from contextlib import contextmanager

_registry_lock = threading.Lock()
# { (pid, str_path, read_only): DuckDBPyConnection }
_conn_registry: dict[tuple[int, str, bool], "duckdb.DuckDBPyConnection"] = {}

# Per-database write-serialisation locks.  Separate from _registry_lock so
# readers never contend with writers for the connection itself.
_write_locks: dict[str, threading.Lock] = {}


@contextmanager
def write_lock(db_key: str):
    """Context manager that serialises write operations for *db_key*.

    Usage::

        with write_lock("dfs_edge"):
            conn.execute("INSERT INTO ...")

    Uses a double-checked pattern to create per-key locks lazily without
    holding the registry lock during the actual write operation.
    """
    with _registry_lock:
        if db_key not in _write_locks:
            _write_locks[db_key] = threading.Lock()
        lock = _write_locks[db_key]
    with lock:
        yield


def get_conn(
    path: str | Path,
    *,
    db_key: str | None = None,
    read_only: bool = False,
) -> "duckdb.DuckDBPyConnection":
    """Return the per-process singleton connection for *path*.

    Opens and migrates the database on first call from this process.
    Subsequent calls return the same open connection.  Automatically
    recreates the connection if it was closed (e.g. by a prior ``close()``
    call or after a fork).

    Prefer this over bare ``duckdb.connect()`` everywhere in the backend so
    each worker process holds exactly one connection per database file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = db_key or path.stem
    reg_id = (os.getpid(), str(path), read_only)

    with _registry_lock:
        conn = _conn_registry.get(reg_id)
        if conn is not None:
            # Verify the connection is still alive
            try:
                conn.execute("SELECT 1")
                return conn
            except Exception:
                # Stale / closed — fall through to reopen
                _conn_registry.pop(reg_id, None)

        conn = duckdb.connect(str(path), read_only=read_only)
        if not read_only:
            try:
                migrate(conn, key)
            except Exception as exc:
                log.warning("[db] Migration warning for %s: %s", key, exc)
        _conn_registry[reg_id] = conn
        return conn


def close_all() -> None:
    """Close all registry connections for the current process.  Call this
    during shutdown / testing teardown to release file locks cleanly."""
    pid = os.getpid()
    with _registry_lock:
        to_close = [k for k in _conn_registry if k[0] == pid]
        for k in to_close:
            try:
                _conn_registry.pop(k).close()
            except Exception:
                pass

