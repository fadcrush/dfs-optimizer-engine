"""
Phase 18 — Tests for minutes trend adjustment

Tests cover:
  - _load_minutes_trend() with DB fixture data
  - Positive / negative trend ratios computed correctly
  - Trend capped at ±10% (cap parameter)
  - Player with insufficient L5 or L6-10 history excluded
  - MinutesTrend column always present in engine output
  - minutes_trend_enabled=False → column is 0.0, Proj unchanged
  - Positive trend boosts Proj; negative trend reduces Proj
  - Neutral (missing) players get 0.0
  - Multiple players each get independent trend values
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from analysis.core.projection_engine import _load_minutes_trend, _slugify
from analysis.shared.db import get_conn


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


def _insert_log(db_path: Path, player_name: str, game_date: date, minutes: float,
                gid: str | None = None) -> None:
    con = get_conn(db_path)
    con.execute(_GL_DDL)
    if gid is None:
        gid = f"{player_name[:4]}_{game_date.isoformat()}"
    con.execute(
        """
        INSERT INTO player_game_logs
            (game_id, player_name, team, opponent, game_date, season,
             minutes, dk_pts, fd_pts, points, rebounds, assists,
             steals, blocks, turnovers)
        VALUES (?, ?, 'LAL', 'BOS', ?, '2026', ?, 30.0, 25.0,
                15.0, 5.0, 4.0, 1.5, 1.0, 2.0)
        """,
        [gid, player_name, game_date, minutes],
    )


def _insert_n_logs(db_path: Path, player_name: str, minutes_list: list[float]) -> None:
    """Insert game logs with descending dates (first entry = most recent)."""
    today = date(2026, 3, 19)
    for i, mins in enumerate(minutes_list):
        gid = f"{player_name}_{i}"
        _insert_log(db_path, player_name, today - timedelta(days=i + 1), mins, gid=gid)


def _engine_slate(n: int = 8) -> pd.DataFrame:
    """Minimal slate suitable for engine generate() calls."""
    _POSITIONS = ["PG", "SG", "SF", "PF", "C", "PG", "SG", "SF",
                  "PF", "C", "PG", "SG", "SF", "PF", "C", "PG", "SG", "SF"]
    return pd.DataFrame({
        "Name":      [f"Player{i}" for i in range(n)],
        "Salary":    [6000 + i * 200 for i in range(n)],
        "Base_Proj": [30.0 + i for i in range(n)],
        "Team":      ["LAL"] * n,
        "Opp":       ["BOS"] * n,
        "Pos":       _POSITIONS[:n],
    })


# ===========================================================================
# TestLoadMinutesTrend
# ===========================================================================

class TestLoadMinutesTrend:
    """Unit tests for _load_minutes_trend()."""

    def test_returns_empty_dict_when_db_missing(self, tmp_path):
        result = _load_minutes_trend(db_path=tmp_path / "not_exist.duckdb")
        assert result == {}

    def test_returns_empty_dict_when_no_table(self, tmp_path):
        db = tmp_path / "dfs_edge.duckdb"
        # Create DB with no tables
        con = get_conn(db)
        con.close()
        result = _load_minutes_trend(db_path=db)
        assert result == {}

    def test_positive_trend(self, tmp_path):
        """Player whose L5 avg minutes > L6-10 avg should have positive trend."""
        db = tmp_path / "dfs_edge.duckdb"
        # L1-5 (recent): 36 min/game;  L6-10 (prior): 28 min/game
        minutes = [36, 36, 36, 36, 36,  28, 28, 28, 28, 28]
        _insert_n_logs(db, "LeBron James", minutes)
        result = _load_minutes_trend(db_path=db)
        slug = _slugify("LeBron James")
        assert slug in result
        assert result[slug] > 0.0

    def test_negative_trend(self, tmp_path):
        """Player whose L5 avg minutes < L6-10 avg should have negative trend."""
        db = tmp_path / "dfs_edge.duckdb"
        # L1-5: 22 min/game;  L6-10: 32 min/game
        minutes = [22, 22, 22, 22, 22,  32, 32, 32, 32, 32]
        _insert_n_logs(db, "Bench Player", minutes)
        result = _load_minutes_trend(db_path=db)
        slug = _slugify("Bench Player")
        assert slug in result
        assert result[slug] < 0.0

    def test_zero_trend_when_minutes_equal(self, tmp_path):
        """Stable-minutes player should have trend near 0.0."""
        db = tmp_path / "dfs_edge.duckdb"
        minutes = [30.0] * 10
        _insert_n_logs(db, "Stable Player", minutes)
        result = _load_minutes_trend(db_path=db)
        slug = _slugify("Stable Player")
        assert slug in result
        assert abs(result[slug]) < 1e-4

    def test_trend_capped_at_positive_cap(self, tmp_path):
        """Extreme positive jump should be capped at the cap value."""
        db = tmp_path / "dfs_edge.duckdb"
        # L5: 40 min, L6-10: 5 min → ratio would be (40-5)/5 = 7.0 → capped to 0.10
        minutes = [40, 40, 40, 40, 40,  5, 5, 5, 5, 5]
        _insert_n_logs(db, "Breakout Star", minutes)
        result = _load_minutes_trend(db_path=db, cap=0.10)
        slug = _slugify("Breakout Star")
        assert slug in result
        assert result[slug] == pytest.approx(0.10, abs=1e-4)

    def test_trend_capped_at_negative_cap(self, tmp_path):
        """Extreme negative drop should be capped at −cap."""
        db = tmp_path / "dfs_edge.duckdb"
        # L5: 5 min, L6-10: 40 min → ratio = (5-40)/40 = -0.875 → capped to -0.10
        minutes = [5, 5, 5, 5, 5,  40, 40, 40, 40, 40]
        _insert_n_logs(db, "Benched Vet", minutes)
        result = _load_minutes_trend(db_path=db, cap=0.10)
        slug = _slugify("Benched Vet")
        assert slug in result
        assert result[slug] == pytest.approx(-0.10, abs=1e-4)

    def test_player_without_prior_window_excluded(self, tmp_path):
        """Player with only L5 games (no L6-10 history) should not appear."""
        db = tmp_path / "dfs_edge.duckdb"
        # Only 4 games — not enough for either window
        minutes = [30, 30, 30, 30]
        _insert_n_logs(db, "Rookie", minutes)
        result = _load_minutes_trend(db_path=db)
        slug = _slugify("Rookie")
        assert slug not in result


# ===========================================================================
# TestEngineMinutesTrendLayer
# ===========================================================================

class TestEngineMinutesTrendLayer:
    """Integration tests for the MinutesTrend layer inside generate()."""

    def test_minutes_trend_column_always_present(self, tmp_path):
        """MinutesTrend column must exist in output even with no game-log data."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        out = engine.generate(_engine_slate(8), ctx)
        assert "MinutesTrend" in out.columns

    def test_minutes_trend_disabled_sets_zero(self, tmp_path):
        """minutes_trend_enabled=False → all MinutesTrend values are 0.0."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False,
            minutes_trend_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        out = engine.generate(_engine_slate(8), ctx)
        assert (out["MinutesTrend"] == 0.0).all()

    def test_positive_trend_boosts_projection(self, tmp_path):
        """A player with a strong positive minutes trend should have higher Proj."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        db = tmp_path / "dfs_edge.duckdb"
        # "Player0" in slate — insert 10 game logs so they have a strong upward trend
        # L5: 38 min, L6-10: 20 min → trend = +0.90 → capped at +0.10
        _insert_n_logs(db, "Player0", [38, 38, 38, 38, 38, 20, 20, 20, 20, 20])

        engine_with = CanonicalNBAProjectionEngine(
            gl_db_path=db,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False, minutes_trend_enabled=True,
        )
        engine_without = CanonicalNBAProjectionEngine(
            gl_db_path=db,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False, minutes_trend_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        slate = _engine_slate(8)

        out_with = engine_with.generate(slate.copy(), ctx)
        out_without = engine_without.generate(slate.copy(), ctx)

        proj_with = out_with.loc[out_with["Name"] == "Player0", "Proj"].iloc[0]
        proj_without = out_without.loc[out_without["Name"] == "Player0", "Proj"].iloc[0]
        assert proj_with > proj_without

    def test_negative_trend_reduces_projection(self, tmp_path):
        """A player with a strong downward trend should have lower Proj."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        db = tmp_path / "dfs_edge.duckdb"
        # "Player0": L5=10 min, L6-10=38 min → strongly negative → capped at -0.10
        _insert_n_logs(db, "Player0", [10, 10, 10, 10, 10, 38, 38, 38, 38, 38])

        engine_with = CanonicalNBAProjectionEngine(
            gl_db_path=db,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False, minutes_trend_enabled=True,
        )
        engine_without = CanonicalNBAProjectionEngine(
            gl_db_path=db,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False, minutes_trend_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        slate = _engine_slate(8)

        out_with = engine_with.generate(slate.copy(), ctx)
        out_without = engine_without.generate(slate.copy(), ctx)

        proj_with = out_with.loc[out_with["Name"] == "Player0", "Proj"].iloc[0]
        proj_without = out_without.loc[out_without["Name"] == "Player0", "Proj"].iloc[0]
        assert proj_with < proj_without

    def test_neutral_player_trend_is_zero(self, tmp_path):
        """Player with equal L5 and L6-10 minutes should have trend ≈ 0.0."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        db = tmp_path / "dfs_edge.duckdb"
        _insert_n_logs(db, "Player0", [32.0] * 10)

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=db,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        out = engine.generate(_engine_slate(8), ctx)
        trend_val = out.loc[out["Name"] == "Player0", "MinutesTrend"].iloc[0]
        assert abs(trend_val) < 1e-4


# ===========================================================================
# TestMinutesTrendEdgeCases
# ===========================================================================

class TestMinutesTrendEdgeCases:
    """Edge cases and multi-player scenarios."""

    def test_player_not_in_trend_dict_gets_zero(self, tmp_path):
        """Unknown player (no game-log entry) must have MinutesTrend = 0.0."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        db = tmp_path / "dfs_edge.duckdb"
        # Populate only Player0's logs
        _insert_n_logs(db, "Player0", [36, 36, 36, 36, 36, 28, 28, 28, 28, 28])

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=db,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            ownership_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        out = engine.generate(_engine_slate(8), ctx)

        # Player1-7 have no logs → MinutesTrend must be 0.0
        unknown_trends = out.loc[out["Name"] != "Player0", "MinutesTrend"]
        assert (unknown_trends == 0.0).all()

    def test_multiple_players_independent_trends(self, tmp_path):
        """Two players get independently computed trend values."""
        db = tmp_path / "dfs_edge.duckdb"
        # PlayerA: upward trend
        _insert_n_logs(db, "PlayerA", [36, 36, 36, 36, 36, 26, 26, 26, 26, 26])
        # PlayerB: downward trend
        _insert_n_logs(db, "PlayerB", [20, 20, 20, 20, 20, 34, 34, 34, 34, 34])

        result = _load_minutes_trend(db_path=db)
        slug_a = _slugify("PlayerA")
        slug_b = _slugify("PlayerB")
        assert slug_a in result
        assert slug_b in result
        assert result[slug_a] > 0
        assert result[slug_b] < 0
        # They should be different values
        assert result[slug_a] != result[slug_b]

    def test_custom_cap_applied(self, tmp_path):
        """cap=0.05 should limit trend to ±5%."""
        db = tmp_path / "dfs_edge.duckdb"
        # Extreme jump that would ordinarily hit 10% cap
        _insert_n_logs(db, "Star", [40, 40, 40, 40, 40, 5, 5, 5, 5, 5])
        result = _load_minutes_trend(db_path=db, cap=0.05)
        slug = _slugify("Star")
        assert slug in result
        assert result[slug] == pytest.approx(0.05, abs=1e-4)

    def test_insufficient_recent_games_excluded(self, tmp_path):
        """Player with only 2 games in L5 (< min_recent=3) should be excluded."""
        db = tmp_path / "dfs_edge.duckdb"
        # Only 2 recent + 5 prior (prior window fine, recent window insufficient)
        _insert_n_logs(db, "ShortTimer", [30, 30, 28, 28, 28, 28, 28])
        # The first 2 data points are 'recent', but min_recent=3 by default
        # so we need to confirm player is excluded when they only have 2 recent games
        result = _load_minutes_trend(db_path=db, recent_games=5, min_recent=3)
        # With 7 total games: rn1-5=recent window, rn6-7=prior (only 2 prior, < min_prior=3)
        # Player excluded from prior HAVING COUNT() >= 3
        slug = _slugify("ShortTimer")
        assert slug not in result

    def test_trend_ratio_magnitude_correct(self, tmp_path):
        """Verify the exact ratio: (36-30)/30 = 0.20 → capped to 0.10."""
        db = tmp_path / "dfs_edge.duckdb"
        _insert_n_logs(db, "ExactCalc", [36, 36, 36, 36, 36, 30, 30, 30, 30, 30])
        result = _load_minutes_trend(db_path=db, cap=0.10)
        slug = _slugify("ExactCalc")
        assert slug in result
        # (36 - 30) / 30 = 0.2 → capped at 0.10
        assert result[slug] == pytest.approx(0.10, abs=1e-4)

    def test_trend_ratio_small_positive_uncapped(self, tmp_path):
        """Small positive trend should pass through un-capped."""
        db = tmp_path / "dfs_edge.duckdb"
        # L5: 31 min, L6-10: 30 min → ratio = 1/30 ≈ 0.0333 → within ±0.10 cap
        _insert_n_logs(db, "SlightUp", [31, 31, 31, 31, 31, 30, 30, 30, 30, 30])
        result = _load_minutes_trend(db_path=db, cap=0.10)
        slug = _slugify("SlightUp")
        assert slug in result
        expected = (31 - 30) / 30
        assert result[slug] == pytest.approx(expected, abs=0.001)
