"""
tests/test_stat_enrichment.py
──────────────────────────────
Tests for Phase 3 stat/minutes enrichment:
  - analysis.nba.minutes_confidence_lite.add_minutes_confidence
  - analysis.nba.stat_projection_breakdown.enrich_with_stat_breakdown

All tests use in-memory DuckDB (tmp_path) — no dfs_edge.duckdb required.
"""
from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path, rows: list[dict]) -> None:
    """Create a minimal player_game_logs DuckDB at *path* with given rows."""
    con = duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE player_game_logs (
            player_name TEXT,
            game_date   DATE,
            minutes     DOUBLE,
            dk_pts      DOUBLE,
            fd_pts      DOUBLE,
            team        TEXT,
            opponent    TEXT
        )
    """)
    if rows:
        df = pd.DataFrame(rows)
        con.execute("INSERT INTO player_game_logs SELECT * FROM df")
    con.close()


def _proj_df(names: list[str]) -> pd.DataFrame:
    """Minimal projections DataFrame with required columns."""
    return pd.DataFrame({
        "DFS_ID": [f"id{i}" for i in range(len(names))],
        "Name": names,
        "Proj": [30.0] * len(names),
        "Floor": [20.0] * len(names),
        "Ceiling": [45.0] * len(names),
        "Salary": [6000] * len(names),
        "Own": [0.10] * len(names),
    })


# ---------------------------------------------------------------------------
# minutes_confidence_lite tests
# ---------------------------------------------------------------------------

class TestMinutesConfidenceLite:
    def test_empty_df_returns_nan(self, tmp_path):
        """Empty input should not crash."""
        db = tmp_path / "test.duckdb"
        _make_db(db, [])
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        result = add_minutes_confidence(pd.DataFrame(columns=["Name"]), db_path=db)
        assert result.empty

    def test_missing_db_adds_nan_columns(self, tmp_path):
        """When DB doesn't exist, columns default to NaN/0."""
        db = tmp_path / "nonexistent.duckdb"
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        df = _proj_df(["LeBron James"])
        result = add_minutes_confidence(df, db_path=db)
        assert "projected_minutes" in result.columns
        assert "minutes_confidence" in result.columns
        assert result["minutes_confidence"].iloc[0] == 0.0

    def test_projected_minutes_in_range(self, tmp_path):
        """projected_minutes must be in [0, 42]."""
        db = tmp_path / "test.duckdb"
        rows = [
            {"player_name": "Stephen Curry", "game_date": f"2026-01-{d:02d}",
             "minutes": 34.0, "dk_pts": 50.0, "fd_pts": 45.0,
             "team": "GSW", "opponent": "LAL"}
            for d in range(1, 16)
        ]
        _make_db(db, rows)
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        df = _proj_df(["Stephen Curry"])
        result = add_minutes_confidence(df, db_path=db)
        pm = result["projected_minutes"].iloc[0]
        assert 0.0 <= pm <= 42.0

    def test_minutes_confidence_in_range(self, tmp_path):
        """minutes_confidence must be in [0, 1]."""
        db = tmp_path / "test.duckdb"
        rows = [
            {"player_name": "Luka Doncic", "game_date": f"2026-01-{d:02d}",
             "minutes": 36.0, "dk_pts": 55.0, "fd_pts": 50.0,
             "team": "DAL", "opponent": "PHX"}
            for d in range(1, 21)
        ]
        _make_db(db, rows)
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        df = _proj_df(["Luka Doncic"])
        result = add_minutes_confidence(df, db_path=db)
        conf = result["minutes_confidence"].iloc[0]
        assert 0.0 <= conf <= 1.0

    def test_small_sample_damped_toward_replacement(self, tmp_path):
        """Player with 2 games should be pulled toward 12-min replacement level."""
        db = tmp_path / "test.duckdb"
        rows = [
            {"player_name": "Rookie X", "game_date": f"2026-01-{d:02d}",
             "minutes": 38.0, "dk_pts": 30.0, "fd_pts": 28.0,
             "team": "OKC", "opponent": "MEM"}
            for d in range(1, 3)
        ]
        _make_db(db, rows)
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        df = _proj_df(["Rookie X"])
        result = add_minutes_confidence(df, db_path=db)
        pm = result["projected_minutes"].iloc[0]
        # With 2 games out of SMALL_SAMPLE_THRESHOLD=5, should be dampened
        # toward 12 min: result should be well below the raw 38 min average
        assert pm < 35.0

    def test_unknown_player_gets_zero(self, tmp_path):
        """A player with no game-log data gets projected_minutes=NaN or 0."""
        db = tmp_path / "test.duckdb"
        _make_db(db, [
            {"player_name": "Known Player", "game_date": "2026-01-01",
             "minutes": 30.0, "dk_pts": 40.0, "fd_pts": 35.0,
             "team": "BOS", "opponent": "MIA"}
        ])
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        df = _proj_df(["Unknown Player"])
        result = add_minutes_confidence(df, db_path=db)
        # unknown player: confidence should be 0, minutes NaN or 0
        assert result["minutes_confidence"].iloc[0] == 0.0

    def test_proj_floor_ceiling_untouched(self, tmp_path):
        """minutes model must not overwrite Proj, Floor, Ceiling."""
        db = tmp_path / "test.duckdb"
        rows = [
            {"player_name": "Joel Embiid", "game_date": f"2026-01-{d:02d}",
             "minutes": 32.0, "dk_pts": 45.0, "fd_pts": 40.0,
             "team": "PHI", "opponent": "ATL"}
            for d in range(1, 12)
        ]
        _make_db(db, rows)
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        df = _proj_df(["Joel Embiid"])
        result = add_minutes_confidence(df, db_path=db)
        assert result["Proj"].iloc[0] == pytest.approx(30.0)
        assert result["Floor"].iloc[0] == pytest.approx(20.0)
        assert result["Ceiling"].iloc[0] == pytest.approx(45.0)

    def test_higher_sample_higher_confidence(self, tmp_path):
        """More games should yield higher confidence (all else equal)."""
        db = tmp_path / "test.duckdb"
        rows_few = [
            {"player_name": "Few Games", "game_date": f"2026-01-{d:02d}",
             "minutes": 30.0, "dk_pts": 40.0, "fd_pts": 35.0,
             "team": "BOS", "opponent": "MIA"}
            for d in range(1, 4)
        ]
        rows_many = [
            {"player_name": "Many Games", "game_date": f"2026-01-{d:02d}",
             "minutes": 30.0, "dk_pts": 40.0, "fd_pts": 35.0,
             "team": "BOS", "opponent": "MIA"}
            for d in range(1, 31)
        ]
        _make_db(db, rows_few + rows_many)
        from analysis.nba.minutes_confidence_lite import add_minutes_confidence
        df = _proj_df(["Few Games", "Many Games"])
        result = add_minutes_confidence(df, db_path=db)
        few_conf = result.loc[result["Name"] == "Few Games", "minutes_confidence"].iloc[0]
        many_conf = result.loc[result["Name"] == "Many Games", "minutes_confidence"].iloc[0]
        assert many_conf > few_conf


# ---------------------------------------------------------------------------
# stat_projection_breakdown tests
# ---------------------------------------------------------------------------

class TestStatProjectionBreakdown:
    def _make_db_with_rates(self, path: Path) -> None:
        rows = [
            {"player_name": "Kevin Durant", "game_date": f"2026-01-{d:02d}",
             "minutes": 35.0, "dk_pts": 52.5, "fd_pts": 47.0,
             "team": "PHX", "opponent": "LAL"}
            for d in range(1, 21)
        ]
        _make_db(path, rows)

    def test_output_columns_present(self, tmp_path):
        db = tmp_path / "test.duckdb"
        self._make_db_with_rates(db)
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Kevin Durant"])
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        for col in ["projected_minutes", "minutes_confidence", "est_dk_pts", "est_fd_pts", "stat_confidence"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_est_dk_pts_reasonable(self, tmp_path):
        """est_dk_pts should be close to rate * minutes."""
        db = tmp_path / "test.duckdb"
        self._make_db_with_rates(db)
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Kevin Durant"])
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        est = result["est_dk_pts"].iloc[0]
        # KD averages 52.5 dk_pts in 35 min → ~1.5 dk/min.
        # projected_minutes ≈ 35, so est_dk_pts ≈ 52.5
        assert not math.isnan(est)
        assert 20.0 < est < 80.0  # loose sanity bounds

    def test_stat_confidence_in_range(self, tmp_path):
        db = tmp_path / "test.duckdb"
        self._make_db_with_rates(db)
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Kevin Durant"])
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        conf = result["stat_confidence"].iloc[0]
        assert 0.0 <= conf <= 1.0

    def test_proj_floor_ceiling_never_overwritten(self, tmp_path):
        db = tmp_path / "test.duckdb"
        self._make_db_with_rates(db)
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Kevin Durant"])
        original_proj = df["Proj"].iloc[0]
        original_floor = df["Floor"].iloc[0]
        original_ceiling = df["Ceiling"].iloc[0]
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        assert result["Proj"].iloc[0] == pytest.approx(original_proj)
        assert result["Floor"].iloc[0] == pytest.approx(original_floor)
        assert result["Ceiling"].iloc[0] == pytest.approx(original_ceiling)

    def test_unknown_player_gets_nan_estimates(self, tmp_path):
        db = tmp_path / "test.duckdb"
        self._make_db_with_rates(db)
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Nobody McFakename"])
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        assert math.isnan(result["est_dk_pts"].iloc[0])
        assert result["stat_confidence"].iloc[0] == 0.0

    def test_missing_db_does_not_crash(self, tmp_path):
        db = tmp_path / "nonexistent.duckdb"
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Kawhi Leonard"])
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        assert "est_dk_pts" in result.columns
        assert "stat_confidence" in result.columns

    def test_empty_input_returns_empty(self, tmp_path):
        db = tmp_path / "test.duckdb"
        _make_db(db, [])
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = pd.DataFrame(columns=["DFS_ID", "Name", "Proj", "Floor", "Ceiling", "Salary", "Own"])
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        assert result.empty

    def test_projected_minutes_within_guardrails(self, tmp_path):
        """Projected minutes must be in [0, 42] even for high-minute players."""
        db = tmp_path / "test.duckdb"
        rows = [
            {"player_name": "Iron Man", "game_date": f"2026-01-{d:02d}",
             "minutes": 41.0, "dk_pts": 60.0, "fd_pts": 55.0,
             "team": "BOS", "opponent": "MIL"}
            for d in range(1, 31)
        ]
        _make_db(db, rows)
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Iron Man"])
        result = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        pm = result["projected_minutes"].iloc[0]
        assert 0.0 <= pm <= 42.0

    def test_fd_site_produces_different_estimates(self, tmp_path):
        """FD and DK estimates should differ when rates differ."""
        db = tmp_path / "test.duckdb"
        rows = [
            {"player_name": "Nikola Jokic", "game_date": f"2026-01-{d:02d}",
             "minutes": 33.0, "dk_pts": 57.0, "fd_pts": 49.0,
             "team": "DEN", "opponent": "OKC"}
            for d in range(1, 21)
        ]
        _make_db(db, rows)
        from analysis.nba.stat_projection_breakdown import enrich_with_stat_breakdown
        df = _proj_df(["Nikola Jokic"])
        result_dk = enrich_with_stat_breakdown(df, site="DK", db_path=db)
        result_fd = enrich_with_stat_breakdown(df, site="FD", db_path=db)
        # DK scoring is higher than FD in NBA, so est_dk > est_fd
        assert result_dk["est_dk_pts"].iloc[0] > result_fd["est_fd_pts"].iloc[0]
