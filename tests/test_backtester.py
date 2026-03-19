"""
Phase 15 — Tests for analysis.core.backtester.ProjectionBacktester

Uses tmp_path (pytest fixture) so each test gets a fresh DuckDB.  Because
get_conn uses the file stem "dfs_edge" the full migration chain (v1–v6) runs
automatically, including the projection_snapshots and projection_accuracy_log
tables added in Phase 15.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from analysis.core.backtester import ProjectionBacktester, _r_squared


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _bt(tmp_path: Path) -> ProjectionBacktester:
    """
    Return a backtester pointing at a fresh (migrated) test DB.

    ``game_logs_db_path=None`` means the backtester uses the same file for
    player_game_logs, which the helper below creates when first needed.
    """
    return ProjectionBacktester(
        db_path=tmp_path / "dfs_edge.duckdb",
        game_logs_db_path=None,
    )


_GL_DDL = """
    CREATE TABLE IF NOT EXISTS player_game_logs (
        game_id     VARCHAR PRIMARY KEY,
        player_id   VARCHAR,
        player_name VARCHAR,
        team        VARCHAR,
        opponent    VARCHAR,
        game_date   DATE,
        season      VARCHAR,
        wl          VARCHAR,
        is_home     BOOLEAN,
        minutes     INTEGER DEFAULT 0,
        points      DOUBLE,
        rebounds    DOUBLE,
        assists     DOUBLE,
        steals      DOUBLE,
        blocks      DOUBLE,
        turnovers   DOUBLE,
        pf          DOUBLE,
        three_pointers DOUBLE,
        fg_attempted DOUBLE,
        fg_made      DOUBLE,
        fg_pct       DOUBLE,
        ft_attempted DOUBLE,
        ft_made      DOUBLE,
        dk_pts      DOUBLE,
        fd_pts      DOUBLE,
        ingested_at TIMESTAMPTZ DEFAULT now()
    );
"""


def _insert_game_log(
    bt: ProjectionBacktester,
    player_name: str,
    game_date: date,
    dk_pts: float,
    fd_pts: float,
    minutes: int = 32,
) -> None:
    """Create player_game_logs if needed and insert a minimal row."""
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
            f"{player_name}_{game_date}",
            player_name, "LAL", "BOS",
            game_date, "2025-26",
            minutes, dk_pts, fd_pts,
        ],
    )


def _proj_df(names: list[str], projs: list[float]) -> pd.DataFrame:
    """Build a minimal projection DataFrame."""
    return pd.DataFrame({"Name": names, "Proj": projs})


# ===========================================================================
# TestProjectionSnapshot
# ===========================================================================

class TestProjectionSnapshot:
    def test_snapshot_writes_rows(self, tmp_path):
        bt = _bt(tmp_path)
        df = _proj_df(["LeBron James", "Anthony Davis"], [48.5, 42.0])
        n = bt.snapshot(df, slate_date=date(2026, 3, 10), site="DK")

        assert n == 2

        from analysis.shared.db import get_conn
        rows = get_conn(bt.db_path).execute(
            "SELECT player_name, proj FROM projection_snapshots ORDER BY proj DESC"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "LeBron James"
        assert rows[0][1] == pytest.approx(48.5)

    def test_snapshot_upsert_idempotent(self, tmp_path):
        bt = _bt(tmp_path)
        df = _proj_df(["LeBron James"], [48.5])
        bt.snapshot(df, slate_date=date(2026, 3, 10), site="DK")

        # Update the projection and snapshot again — should upsert, not duplicate
        df2 = _proj_df(["LeBron James"], [50.0])
        n = bt.snapshot(df2, slate_date=date(2026, 3, 10), site="DK")
        assert n == 1

        from analysis.shared.db import get_conn
        rows = get_conn(bt.db_path).execute(
            "SELECT proj FROM projection_snapshots WHERE site = 'DK'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(50.0)

    def test_snapshot_empty_df_returns_zero(self, tmp_path):
        bt = _bt(tmp_path)
        n = bt.snapshot(pd.DataFrame(), slate_date=date(2026, 3, 10), site="DK")
        assert n == 0

    def test_snapshot_missing_name_raises(self, tmp_path):
        bt = _bt(tmp_path)
        bad = pd.DataFrame({"Proj": [40.0]})
        with pytest.raises(ValueError, match="Name"):
            bt.snapshot(bad, slate_date=date(2026, 3, 10), site="DK")

    def test_snapshot_missing_proj_raises(self, tmp_path):
        bt = _bt(tmp_path)
        bad = pd.DataFrame({"Name": ["LeBron James"]})
        with pytest.raises(ValueError, match="Proj"):
            bt.snapshot(bad, slate_date=date(2026, 3, 10), site="DK")

    def test_snapshot_stores_optional_columns(self, tmp_path):
        bt = _bt(tmp_path)
        df = pd.DataFrame({
            "Name":    ["Jayson Tatum"],
            "Proj":    [44.0],
            "StdDev":  [8.5],
            "Floor":   [28.0],
            "Ceiling": [62.0],
            "Salary":  [8800],
        })
        bt.snapshot(df, slate_date=date(2026, 3, 15), site="FD")

        from analysis.shared.db import get_conn
        row = get_conn(bt.db_path).execute(
            "SELECT std_dev, floor_val, ceiling_val, salary FROM projection_snapshots"
        ).fetchone()
        assert row[0] == pytest.approx(8.5)
        assert row[1] == pytest.approx(28.0)
        assert row[2] == pytest.approx(62.0)
        assert row[3] == 8800


# ===========================================================================
# TestScoreDate
# ===========================================================================

class TestScoreDate:
    def test_returns_none_when_no_actuals(self, tmp_path):
        bt = _bt(tmp_path)
        names = [f"Player{i}" for i in range(6)]
        projs = [40.0 + i for i in range(6)]
        bt.snapshot(_proj_df(names, projs), slate_date=date(2026, 3, 10), site="DK")

        result = bt.score_date(date(2026, 3, 10), site="DK")
        assert result is None

    def test_computes_correct_mae_rmse(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 12)
        # 6 players: projected always 10 points above actual → MAE=RMSE=10
        names   = [f"Player{i}" for i in range(6)]
        projs   = [40.0] * 6
        actuals = [30.0] * 6

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual - 1)

        result = bt.score_date(slate, site="DK")
        assert result is not None
        assert result["mae"]  == pytest.approx(10.0, abs=0.01)
        assert result["rmse"] == pytest.approx(10.0, abs=0.01)

    def test_bias_positive_when_over_projected(self, tmp_path):
        """Projected > actual → bias > 0."""
        bt = _bt(tmp_path)
        slate = date(2026, 3, 13)
        names   = [f"Player{i}" for i in range(6)]
        projs   = [45.0] * 6
        actuals = [35.0] * 6  # always over-projected

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        result = bt.score_date(slate, site="DK")
        assert result is not None
        assert result["bias"] > 0

    def test_returns_none_below_min_players(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 14)
        # Only 4 matched rows — below default min_players=5
        names   = [f"Player{i}" for i in range(4)]
        projs   = [40.0] * 4
        actuals = [38.0] * 4

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        result = bt.score_date(slate, site="DK", min_players=5)
        assert result is None

    def test_custom_min_players_allows_small_slate(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 15)
        names   = [f"Player{i}" for i in range(3)]
        projs   = [40.0] * 3
        actuals = [38.0] * 3

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        result = bt.score_date(slate, site="DK", min_players=3)
        assert result is not None

    def test_uses_fd_pts_for_fd_site(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 16)
        names   = [f"Player{i}" for i in range(6)]
        projs   = [40.0] * 6
        # DK = 30, FD = 38 — for site=FD, error should be 40-38 = 2
        for name in names:
            _insert_game_log(bt, name, slate, dk_pts=30.0, fd_pts=38.0)
        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="FD")

        result = bt.score_date(slate, site="FD")
        assert result is not None
        assert result["mae"] == pytest.approx(2.0, abs=0.01)

    def test_score_date_persists_to_log(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 17)
        names   = [f"Player{i}" for i in range(6)]
        projs   = [40.0] * 6
        actuals = [35.0] * 6

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        bt.score_date(slate, site="DK")

        from analysis.shared.db import get_conn
        row = get_conn(bt.db_path).execute(
            "SELECT mae, n_players FROM projection_accuracy_log WHERE run_date = ? AND site = 'DK'",
            [slate],
        ).fetchone()
        assert row is not None
        assert row[1] == 6

    def test_pct_within_5_all_close(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 18)
        names   = [f"Player{i}" for i in range(6)]
        projs   = [40.0] * 6
        actuals = [38.0] * 6  # all within 2 pts → 100% within 5

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        result = bt.score_date(slate, site="DK")
        assert result["pct_within_5"]  == pytest.approx(100.0)
        assert result["pct_within_10"] == pytest.approx(100.0)


# ===========================================================================
# TestGetTrend
# ===========================================================================

def _insert_accuracy_row(bt: ProjectionBacktester, run_date: date, site: str, mae: float) -> None:
    """Directly insert a projection_accuracy_log row for test setup."""
    from analysis.shared.db import get_conn
    get_conn(bt.db_path).execute(
        """
        INSERT INTO projection_accuracy_log
            (run_date, site, n_players, mae, rmse, bias, r_squared,
             pct_within_5, pct_within_10)
        VALUES (?, ?, 10, ?, ?, 0.0, 0.8, 60.0, 90.0)
        ON CONFLICT DO NOTHING
        """,
        [run_date, site.upper(), mae, mae],
    )


class TestGetTrend:
    def test_empty_returns_empty_df(self, tmp_path):
        bt = _bt(tmp_path)
        df = bt.get_trend()
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_returns_sorted_ascending(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        for i in range(5):
            _insert_accuracy_row(bt, base + timedelta(days=i), "DK", 7.0 + i)

        df = bt.get_trend(n_days=10)
        assert len(df) == 5
        dates = df["run_date"].tolist()
        assert dates == sorted(dates)

    def test_n_days_limits_rows(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        for i in range(10):
            _insert_accuracy_row(bt, base + timedelta(days=i), "DK", 6.0)

        df = bt.get_trend(n_days=4)
        assert len(df) == 4

    def test_site_filter(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        for i in range(3):
            _insert_accuracy_row(bt, base + timedelta(days=i), "DK", 6.0)
            _insert_accuracy_row(bt, base + timedelta(days=i), "FD", 7.0)

        dk_df = bt.get_trend(n_days=30, site="DK")
        fd_df = bt.get_trend(n_days=30, site="FD")
        assert all(dk_df["site"] == "DK")
        assert all(fd_df["site"] == "FD")
        assert len(dk_df) == 3
        assert len(fd_df) == 3

    def test_no_site_filter_returns_both_sites(self, tmp_path):
        bt = _bt(tmp_path)
        base = date(2026, 3, 1)
        for i in range(3):
            _insert_accuracy_row(bt, base + timedelta(days=i), "DK", 6.0)
            _insert_accuracy_row(bt, base + timedelta(days=i), "FD", 7.0)

        df = bt.get_trend(n_days=30)
        assert set(df["site"].unique()) >= {"DK", "FD"}


# ===========================================================================
# TestBiggestMisses
# ===========================================================================

class TestBiggestMisses:
    def test_empty_on_no_data(self, tmp_path):
        bt = _bt(tmp_path)
        df = bt.get_biggest_misses(date(2026, 3, 10), site="DK")
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_sorted_by_abs_error_desc(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 20)
        names   = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]
        projs   = [40.0,    40.0,   40.0,    40.0,    40.0,      40.0]
        actuals = [25.0,    37.0,   40.0,    45.0,    10.0,      50.0]

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        df = bt.get_biggest_misses(slate, site="DK")
        assert not df.empty
        assert df["abs_error"].is_monotonic_decreasing

    def test_top_n_respected(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 21)
        names   = [f"Player{i}" for i in range(8)]
        projs   = [40.0] * 8
        actuals = [30.0 + i for i in range(8)]

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        df = bt.get_biggest_misses(slate, site="DK", top_n=3)
        assert len(df) == 3

    def test_columns_present(self, tmp_path):
        bt = _bt(tmp_path)
        slate = date(2026, 3, 22)
        names   = [f"Player{i}" for i in range(6)]
        projs   = [40.0] * 6
        actuals = [35.0] * 6

        bt.snapshot(_proj_df(names, projs), slate_date=slate, site="DK")
        for name, actual in zip(names, actuals):
            _insert_game_log(bt, name, slate, dk_pts=actual, fd_pts=actual)

        df = bt.get_biggest_misses(slate, site="DK")
        assert {"player_name", "projected", "actual", "error", "abs_error"}.issubset(df.columns)


# ===========================================================================
# TestRSquared (unit tests for the standalone helper)
# ===========================================================================

class TestRSquared:
    def test_perfect_prediction(self):
        actual    = pd.Series([10.0, 20.0, 30.0, 40.0])
        predicted = pd.Series([10.0, 20.0, 30.0, 40.0])
        assert _r_squared(actual, predicted) == pytest.approx(1.0)

    def test_mean_baseline_is_zero(self):
        """Predicting the mean constant for every player gives R²=0."""
        actual    = pd.Series([10.0, 20.0, 30.0, 40.0])
        mean_val  = actual.mean()
        predicted = pd.Series([mean_val] * 4)
        r2 = _r_squared(actual, predicted)
        assert r2 == pytest.approx(0.0, abs=1e-9)

    def test_zero_variance_actual_perfect(self):
        """All actuals identical, projected also identical → R²=1."""
        actual    = pd.Series([25.0, 25.0, 25.0])
        predicted = pd.Series([25.0, 25.0, 25.0])
        assert _r_squared(actual, predicted) == pytest.approx(1.0)

    def test_zero_variance_actual_imperfect(self):
        """All actuals identical but projections differ → R²=0."""
        actual    = pd.Series([25.0, 25.0, 25.0])
        predicted = pd.Series([20.0, 25.0, 30.0])
        assert _r_squared(actual, predicted) == 0.0

    def test_reasonable_value(self):
        """Spot-check: known inputs produce expected R²."""
        actual    = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        predicted = pd.Series([12.0, 18.0, 31.0, 39.0, 52.0])
        r2 = _r_squared(actual, predicted)
        # SS_res = (10-12)²+(20-18)²+(30-31)²+(40-39)²+(50-52)² = 4+4+1+1+4 = 14
        # SS_tot = (10-30)²+(20-30)²+(30-30)²+(40-30)²+(50-30)² = 400+100+0+100+400 = 1000
        # R² = 1 - 14/1000 = 0.986
        assert r2 == pytest.approx(0.986, abs=0.001)
