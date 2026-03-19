"""
Phase 16 — Tests for position-specific DvP

Tests cover:
  - load_dvp_by_position() returning per-position multipliers
  - Position fallback to team-level multiplier
  - backtester.snapshot() writing positions to player_positions
  - Projection engine Layer 4 using position-specific DvP when available
  - Various edge cases (empty data, missing positions, min_games guard)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from analysis.nba.dvp import load_dvp_by_position, load_dvp_table
from analysis.core.backtester import ProjectionBacktester


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _bt(tmp_path: Path) -> ProjectionBacktester:
    return ProjectionBacktester(
        db_path=tmp_path / "dfs_edge.duckdb",
        game_logs_db_path=None,  # same DB — tests run in one file
    )


_GL_DDL = """
    CREATE TABLE IF NOT EXISTS player_game_logs (
        game_id      VARCHAR PRIMARY KEY,
        player_id    VARCHAR,
        player_name  VARCHAR,
        team         VARCHAR,
        opponent     VARCHAR,
        game_date    DATE,
        season       VARCHAR,
        wl           VARCHAR,
        is_home      BOOLEAN,
        minutes      DOUBLE DEFAULT 0,
        points       DOUBLE,
        rebounds     DOUBLE,
        assists      DOUBLE,
        steals       DOUBLE,
        blocks       DOUBLE,
        turnovers    DOUBLE,
        pf           DOUBLE,
        three_pointers DOUBLE,
        fg_attempted DOUBLE,
        fg_made      DOUBLE,
        fg_pct       DOUBLE,
        ft_attempted DOUBLE,
        ft_made      DOUBLE,
        dk_pts       DOUBLE,
        fd_pts       DOUBLE,
        ingested_at  TIMESTAMPTZ DEFAULT now()
    );
"""


def _insert_game_log(
    bt: ProjectionBacktester,
    player_name: str,
    opponent: str,
    game_date: date,
    dk_pts: float,
    fd_pts: float,
    minutes: float = 32,
) -> None:
    from analysis.shared.db import get_conn
    con = get_conn(bt.db_path)
    con.execute(_GL_DDL)
    con.execute(
        """
        INSERT INTO player_game_logs
            (game_id, player_name, team, opponent, game_date,
             season, minutes, dk_pts, fd_pts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [
            f"{player_name}_{game_date}_{opponent}",
            player_name, "LAL", opponent,
            game_date, "2025-26",
            minutes, dk_pts, fd_pts,
        ],
    )


def _insert_player_position(
    bt: ProjectionBacktester,
    player_name: str,
    position: str,
    site: str = "DK",
) -> None:
    from analysis.shared.db import get_conn
    from analysis.core.projection_engine import _slugify
    con = get_conn(bt.db_path)
    con.execute(
        """
        INSERT INTO player_positions (player_slug, player_name, position, site)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (player_slug, site) DO UPDATE SET
            position = excluded.position
        """,
        [_slugify(player_name), player_name, position, site.upper()],
    )


# ===========================================================================
# TestLoadDvPByPosition
# ===========================================================================

class TestLoadDvPByPosition:
    """Unit tests for load_dvp_by_position()."""

    def test_returns_empty_when_no_game_logs(self, tmp_path):
        bt = _bt(tmp_path)
        # Trigger DB creation via get_conn (dfs_edge migrations → player_positions exists)
        from analysis.shared.db import get_conn
        get_conn(bt.db_path)  # creates tables

        result = load_dvp_by_position(site="DK", db_path=bt.db_path)
        assert isinstance(result, dict)
        assert result == {}

    def test_returns_empty_when_no_positions(self, tmp_path):
        bt = _bt(tmp_path)
        # Insert game logs but no player_positions
        base = date(2026, 3, 1)
        for i in range(6):
            _insert_game_log(bt, f"Player{i}", "BOS", base + timedelta(days=i), 35.0, 30.0)

        result = load_dvp_by_position(site="DK", db_path=bt.db_path)
        assert result == {}

    def test_returns_position_buckets(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        # 6 PGs vs BOS scoring 30, 6 SFs vs BOS scoring 45
        pg_names = [f"PG{i}" for i in range(6)]
        sf_names = [f"SF{i}" for i in range(6)]
        for name in pg_names:
            _insert_game_log(bt, name, "BOS", base, 30.0, 28.0)
            _insert_player_position(bt, name, "PG")
        for name in sf_names:
            _insert_game_log(bt, name, "BOS", base, 45.0, 40.0)
            _insert_player_position(bt, name, "SF")

        result = load_dvp_by_position(site="DK", db_path=bt.db_path, min_games=5)
        assert "PG" in result
        assert "SF" in result
        assert isinstance(result["PG"], dict)
        assert "BOS" in result["PG"]

    def test_multipliers_are_clamped(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        # Extreme case: 6 PGs vs BOS score 1000 pts (impossible in real life)
        for i in range(6):
            _insert_game_log(bt, f"PG{i}", "BOS", base + timedelta(days=i), 100.0, 90.0)
            _insert_player_position(bt, f"PG{i}", "PG")
        # 6 PGs vs MIA score normal
        for i in range(6):
            _insert_game_log(bt, f"PG{i+10}", "MIA", base + timedelta(days=i), 35.0, 30.0)
            _insert_player_position(bt, f"PG{i+10}", "PG")

        result = load_dvp_by_position(site="DK", db_path=bt.db_path, min_games=5, max_adj=0.12)
        pg_bos = result.get("PG", {}).get("BOS", 1.0)
        assert pg_bos <= 1.12
        assert pg_bos >= 0.88

    def test_multiplier_above_one_for_soft_defense(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        # BOS allows 50 pts to PGs, MIA allows 20 pts — BOS is softer
        for i in range(6):
            _insert_game_log(bt, f"PG{i}", "BOS", base + timedelta(days=i), 50.0, 45.0)
            _insert_player_position(bt, f"PG{i}", "PG")
        for i in range(6):
            _insert_game_log(bt, f"PG{i+10}", "MIA", base + timedelta(days=i), 20.0, 18.0)
            _insert_player_position(bt, f"PG{i+10}", "PG")

        result = load_dvp_by_position(site="DK", db_path=bt.db_path, min_games=5)
        pg = result.get("PG", {})
        assert pg.get("BOS", 1.0) > pg.get("MIA", 1.0)

    def test_below_min_games_excluded(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        # Only 3 games for PGs vs BOS — below default min_games=5
        for i in range(3):
            _insert_game_log(bt, f"PG{i}", "BOS", base + timedelta(days=i), 40.0, 35.0)
            _insert_player_position(bt, f"PG{i}", "PG")

        result = load_dvp_by_position(site="DK", db_path=bt.db_path, min_games=5)
        pg = result.get("PG", {})
        assert "BOS" not in pg

    def test_fd_pts_used_for_fd_site(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        # DK pts are 50, FD pts are 20 — with site=FD the mult should reflect FD
        for i in range(6):
            _insert_game_log(bt, f"PG{i}", "BOS", base + timedelta(days=i), 50.0, 20.0)
            _insert_player_position(bt, f"PG{i}", "PG", site="FD")
        for i in range(6):
            _insert_game_log(bt, f"PG{i+10}", "MIA", base + timedelta(days=i), 50.0, 40.0)
            _insert_player_position(bt, f"PG{i+10}", "PG", site="FD")

        result = load_dvp_by_position(site="FD", db_path=bt.db_path, min_games=5)
        pg = result.get("PG", {})
        # BOS gave up 20 FD pts, MIA gave up 40 — BOS should be tougher
        bos_mult = pg.get("BOS", 1.0)
        mia_mult = pg.get("MIA", 1.0)
        assert bos_mult < mia_mult

    def test_nonexistent_db_returns_empty(self, tmp_path):
        result = load_dvp_by_position(
            site="DK",
            db_path=tmp_path / "nonexistent.duckdb",
        )
        assert result == {}

    def test_positions_are_site_specific(self, tmp_path):
        """DK and FD can have different position labels for the same player."""
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        for i in range(6):
            _insert_game_log(bt, f"PG{i}", "BOS", base + timedelta(days=i), 40.0, 38.0)
            # DK positions as PG, FD as G (different position labeling)
            _insert_player_position(bt, f"PG{i}", "PG", site="DK")
            _insert_player_position(bt, f"PG{i}", "G", site="FD")
        for i in range(6):
            _insert_game_log(bt, f"PG{i+10}", "MIA", base + timedelta(days=i), 35.0, 33.0)
            _insert_player_position(bt, f"PG{i+10}", "PG", site="DK")
            _insert_player_position(bt, f"PG{i+10}", "G", site="FD")

        dk_result = load_dvp_by_position(site="DK", db_path=bt.db_path, min_games=5)
        fd_result = load_dvp_by_position(site="FD", db_path=bt.db_path, min_games=5)
        assert "PG" in dk_result
        assert "G" in fd_result
        assert "PG" not in fd_result  # FD players weren't labelled PG


# ===========================================================================
# TestSnapshotWritesPositions
# ===========================================================================

class TestSnapshotWritesPositions:
    def test_positions_written_when_column_present(self, tmp_path):
        bt = _bt(tmp_path)
        df = pd.DataFrame({
            "Name": ["LeBron James", "Jayson Tatum"],
            "Proj": [48.5, 42.0],
            "Position": ["SF", "SF"],
        })
        bt.snapshot(df, slate_date=date(2026, 3, 10), site="DK")

        from analysis.shared.db import get_conn
        rows = get_conn(bt.db_path).execute(
            "SELECT player_name, position, site FROM player_positions ORDER BY player_name"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == "SF"  # Jayson Tatum
        assert rows[0][2] == "DK"

    def test_positions_skipped_when_no_column(self, tmp_path):
        bt = _bt(tmp_path)
        df = pd.DataFrame({"Name": ["LeBron James"], "Proj": [48.5]})
        bt.snapshot(df, slate_date=date(2026, 3, 10), site="DK")

        from analysis.shared.db import get_conn
        rows = get_conn(bt.db_path).execute(
            "SELECT COUNT(*) FROM player_positions"
        ).fetchone()
        assert rows[0] == 0

    def test_position_upsert_updates_position(self, tmp_path):
        bt = _bt(tmp_path)
        df1 = pd.DataFrame({"Name": ["LeBron James"], "Proj": [48.5], "Position": ["SF"]})
        bt.snapshot(df1, slate_date=date(2026, 3, 10), site="DK")

        # Re-snapshot with updated position label
        df2 = pd.DataFrame({"Name": ["LeBron James"], "Proj": [50.0], "Position": ["PF"]})
        bt.snapshot(df2, slate_date=date(2026, 3, 11), site="DK")

        from analysis.shared.db import get_conn
        row = get_conn(bt.db_path).execute(
            "SELECT position FROM player_positions WHERE player_name = 'LeBron James'"
        ).fetchone()
        assert row[0] == "PF"

    def test_site_specific_positions_stored_separately(self, tmp_path):
        bt = _bt(tmp_path)
        dk_df = pd.DataFrame({
            "Name": ["Anthony Davis"], "Proj": [52.0], "Position": ["PF/C"],
        })
        fd_df = pd.DataFrame({
            "Name": ["Anthony Davis"], "Proj": [48.0], "Position": ["PF"],
        })
        bt.snapshot(dk_df, slate_date=date(2026, 3, 10), site="DK")
        bt.snapshot(fd_df, slate_date=date(2026, 3, 10), site="FD")

        from analysis.shared.db import get_conn
        rows = get_conn(bt.db_path).execute(
            "SELECT site, position FROM player_positions ORDER BY site"
        ).fetchall()
        assert len(rows) == 2
        sites = {r[0]: r[1] for r in rows}
        assert sites["DK"] == "PF/C"
        assert sites["FD"] == "PF"


# ===========================================================================
# TestProjectionEnginePositionDvP
# ===========================================================================

class TestProjectionEnginePositionDvP:
    """Integration tests: engine uses position-specific DvP when data exists."""

    def _setup_db_with_positions(self, tmp_path):
        """Create and return a DB path with player_positions and game_logs populated."""
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)

        # PGs: BOS allows 50 (soft) vs MIA allows 20 (tough) pts
        for i in range(6):
            _insert_game_log(bt, f"PG{i}", "BOS", base + timedelta(days=i), 50.0, 48.0)
            _insert_player_position(bt, f"PG{i}", "PG", site="DK")
        for i in range(6):
            _insert_game_log(bt, f"PG{i+10}", "MIA", base + timedelta(days=i), 20.0, 18.0)
            _insert_player_position(bt, f"PG{i+10}", "PG", site="DK")

        # SFs: neutral (both 35)
        for i in range(6):
            _insert_game_log(bt, f"SF{i}", "BOS", base + timedelta(days=i), 35.0, 33.0)
            _insert_player_position(bt, f"SF{i}", "SF", site="DK")
        for i in range(6):
            _insert_game_log(bt, f"SF{i+10}", "MIA", base + timedelta(days=i), 35.0, 33.0)
            _insert_player_position(bt, f"SF{i+10}", "SF", site="DK")

        return bt.db_path

    def test_position_dvp_bumps_soft_matchup(self, tmp_path):
        db_path = self._setup_db_with_positions(tmp_path)

        # Build a slate with one PG vs BOS (soft for PGs) and one PG vs MIA (tough)
        slate = pd.DataFrame({
            "Name": ["PG Alice", "PG Bob"],
            "Base_Proj": [40.0, 40.0],
            "Proj": [40.0, 40.0],
            "Salary": [8000, 8000],
            "Position": ["PG", "PG"],
            "Opp": ["BOS", "MIA"],
        })

        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=db_path,
            dvp_enabled=True,
            b2b_enabled=False,
            blowout_enabled=False,
        )
        ctx = ProjectionContext(sport="NBA", site="DK", slate_date=date(2026, 3, 19))
        result = engine.generate(slate, ctx)

        alice_proj = result.loc[result["Name"] == "PG Alice", "Proj"].iloc[0]
        bob_proj   = result.loc[result["Name"] == "PG Bob",   "Proj"].iloc[0]

        # Alice (vs BOS soft) should have higher proj than Bob (vs MIA tough)
        assert alice_proj > bob_proj

    def test_position_dvp_column_added_to_output(self, tmp_path):
        db_path = self._setup_db_with_positions(tmp_path)
        slate = pd.DataFrame({
            "Name": ["PG Alice"],
            "Base_Proj": [40.0],
            "Proj": [40.0],
            "Salary": [8000],
            "Position": ["PG"],
            "Opp": ["BOS"],
        })

        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=db_path, dvp_enabled=True,
            b2b_enabled=False, blowout_enabled=False,
        )
        ctx = ProjectionContext(sport="NBA", site="DK")
        result = engine.generate(slate, ctx)

        assert "DvP" in result.columns

    def test_falls_back_to_team_level_without_positions(self, tmp_path):
        """When player_positions is empty, team-level DvP is used."""
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        # Only game logs, no player_positions
        for i in range(6):
            _insert_game_log(bt, f"Player{i}", "BOS", base + timedelta(days=i), 50.0, 48.0)
        for i in range(6):
            _insert_game_log(bt, f"Player{i+10}", "MIA", base + timedelta(days=i), 20.0, 18.0)

        slate = pd.DataFrame({
            "Name": ["Alice", "Bob"],
            "Base_Proj": [40.0, 40.0],
            "Proj": [40.0, 40.0],
            "Salary": [8000, 8000],
            "Position": ["PG", "PG"],
            "Opp": ["BOS", "MIA"],
        })

        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=bt.db_path, dvp_enabled=True,
            b2b_enabled=False, blowout_enabled=False,
        )
        ctx = ProjectionContext(sport="NBA", site="DK")
        result = engine.generate(slate, ctx)

        alice = result.loc[result["Name"] == "Alice", "Proj"].iloc[0]
        bob   = result.loc[result["Name"] == "Bob",   "Proj"].iloc[0]
        # Team-level DvP: BOS allows more → alice higher
        assert alice > bob

    def test_dvp_disabled_returns_neutral(self, tmp_path):
        db_path = self._setup_db_with_positions(tmp_path)
        slate = pd.DataFrame({
            "Name": ["PG Alice", "PG Bob"],
            "Base_Proj": [40.0, 40.0],
            "Proj": [40.0, 40.0],
            "Salary": [8000, 8000],
            "Position": ["PG", "PG"],
            "Opp": ["BOS", "MIA"],
        })

        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=db_path, dvp_enabled=False,
            b2b_enabled=False, blowout_enabled=False,
        )
        ctx = ProjectionContext(sport="NBA", site="DK")
        result = engine.generate(slate, ctx)

        assert all(result["DvP"] == 1.0)
        # Both projections stay equal (no DvP adjustments)
        assert result.loc[0, "Proj"] == result.loc[1, "Proj"]
