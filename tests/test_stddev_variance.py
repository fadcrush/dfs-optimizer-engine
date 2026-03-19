"""
Tests for Phase 14: per-player StdDev variance model.

Covers:
  - _load_stddev_baseline: DB querying, CV clamping, min-games filter
  - CanonicalNBAProjectionEngine: StdDev/Floor/Ceiling columns, individual vs
    position-fallback CV, graceful DB degradation
  - estimate_ownership: corrected Proj/Own leverage formula
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.core.projection_engine import (
    CanonicalNBAProjectionEngine,
    _CV_MAX,
    _CV_MIN,
    _POSITION_DEFAULT_CV,
    _POSITION_DEFAULT_CV_FALLBACK,
    _load_stddev_baseline,
    _slugify,
)
from analysis.core.schemas import ProjectionContext
from analysis.nba.ownership import estimate_ownership


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_duckdb(path: Path, rows: list[tuple]) -> None:
    """Create a minimal player_game_logs DuckDB for testing."""
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE player_game_logs (
            player_name VARCHAR,
            team        VARCHAR,
            opponent    VARCHAR,
            game_date   DATE,
            dk_pts      DOUBLE,
            fd_pts      DOUBLE,
            minutes     DOUBLE
        )
    """)
    for row in rows:
        con.execute(
            "INSERT INTO player_game_logs VALUES (?, ?, ?, ?, ?, ?, ?)", row
        )
    con.close()


def _slate_df(players=None) -> pd.DataFrame:
    if players is None:
        players = [
            {"DFS_ID": "p1", "Name": "LeBron James", "Team": "LAL", "Opp": "BOS",
             "Pos": "SF", "Salary": 9800, "Own": 25.0, "Base_Proj": 52.0},
            {"DFS_ID": "p2", "Name": "Steph Curry", "Team": "GSW", "Opp": "MEM",
             "Pos": "PG", "Salary": 9200, "Own": 30.0, "Base_Proj": 48.0},
            {"DFS_ID": "p3", "Name": "Unknown Player", "Team": "MIA", "Opp": "NYK",
             "Pos": "SG", "Salary": 7000, "Own": 8.0, "Base_Proj": 35.0},
        ]
    return pd.DataFrame(players)


def _ctx(site: str = "DK") -> ProjectionContext:
    return ProjectionContext(site=site, sport="NBA", slate_date=date(2026, 3, 19))


# ─────────────────────────────────────────────────────────────────────────────
# _load_stddev_baseline
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadStddevBaseline:
    def test_returns_dict(self, tmp_path):
        result = _load_stddev_baseline("DK", tmp_path / "no.duckdb")
        assert isinstance(result, dict)

    def test_empty_on_missing_db(self, tmp_path):
        result = _load_stddev_baseline("DK", tmp_path / "no.duckdb")
        assert result == {}

    def test_keys_are_slugs(self, tmp_path):
        db = tmp_path / "t.duckdb"
        rows = [
            ("LeBron James", "LAL", "BOS", "2026-03-01", 50.0, 44.0, 35.0),
            ("LeBron James", "LAL", "MIA", "2026-03-03", 42.0, 37.0, 34.0),
            ("LeBron James", "LAL", "GSW", "2026-03-05", 48.0, 42.0, 36.0),
            ("LeBron James", "LAL", "PHX", "2026-03-07", 55.0, 49.0, 37.0),
            ("LeBron James", "LAL", "DEN", "2026-03-09", 46.0, 41.0, 33.0),
        ]
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("DK", db, min_games=5)
        # Key must be slug of "LeBron James"
        slug = _slugify("LeBron James")
        assert slug in result

    def test_entry_has_cv_std_games(self, tmp_path):
        db = tmp_path / "t.duckdb"
        rows = [(f"Player A", "BOS", "MIA", f"2026-03-{i:02d}", 40.0 + i, 35.0, 30.0)
                for i in range(1, 11)]
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("DK", db, min_games=5)
        slug = _slugify("Player A")
        assert slug in result
        entry = result[slug]
        assert "cv" in entry
        assert "std" in entry
        assert "games" in entry

    def test_cv_clamped_min(self, tmp_path):
        """A perfectly consistent player (std ≈ 0) gets CV clamped to _CV_MIN."""
        db = tmp_path / "t.duckdb"
        # All 10 games exactly the same → std ≈ 0 → CV would be 0 → clamp to _CV_MIN
        rows = [("Consistent", "BOS", "MIA", f"2026-03-{i:02d}", 40.0, 35.0, 30.0)
                for i in range(1, 11)]
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("DK", db, min_games=5)
        slug = _slugify("Consistent")
        assert slug in result
        assert result[slug]["cv"] >= _CV_MIN

    def test_cv_clamped_max(self, tmp_path):
        """Extremely erratic player's CV is capped at _CV_MAX."""
        db = tmp_path / "t.duckdb"
        # Alternating extremes: 5 and 95 — huge variance relative to ~50 mean
        rows = [("Wild", "BOS", "MIA", f"2026-03-{i:02d}", 5.0 if i % 2 else 95.0, 4.0, 20.0)
                for i in range(1, 11)]
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("DK", db, min_games=5)
        slug = _slugify("Wild")
        assert slug in result
        assert result[slug]["cv"] <= _CV_MAX

    def test_min_games_filter(self, tmp_path):
        """Player with fewer than min_games is excluded."""
        db = tmp_path / "t.duckdb"
        # Only 3 games — below default min_games=5
        rows = [("Sparse", "BOS", "MIA", f"2026-03-{i:02d}", 40.0, 35.0, 30.0)
                for i in range(1, 4)]
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("DK", db, min_games=5)
        assert _slugify("Sparse") not in result

    def test_min_games_passes(self, tmp_path):
        """Player with exactly min_games is included."""
        db = tmp_path / "t.duckdb"
        rows = [("Exact", "BOS", "MIA", f"2026-03-{i:02d}", 40.0 + i, 35.0, 30.0)
                for i in range(1, 6)]  # 5 games = min_games
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("DK", db, min_games=5)
        assert _slugify("Exact") in result

    def test_fd_uses_fd_pts_col(self, tmp_path):
        """Site='FD' queries fd_pts not dk_pts."""
        db = tmp_path / "t.duckdb"
        # dk_pts all 0, fd_pts all 30+i — FD result should be non-trivial
        rows = [("FD Player", "BOS", "MIA", f"2026-03-{i:02d}", 0.0, 30.0 + i, 28.0)
                for i in range(1, 11)]
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("FD", db, min_games=5)
        slug = _slugify("FD Player")
        assert slug in result
        assert result[slug]["std"] > 0  # FD pts had variance; would be 0 if DK was used

    def test_zero_minutes_excluded(self, tmp_path):
        """Games with 0 minutes (DNPs) are excluded from the stddev calculation."""
        db = tmp_path / "t.duckdb"
        rows = (
            [("DNP Player", "BOS", "MIA", f"2026-03-{i:02d}", 0.0, 0.0, 0.0)
             for i in range(1, 6)]  # 5 DNP rows — minutes=0 → excluded
        )
        _make_duckdb(db, rows)
        result = _load_stddev_baseline("DK", db, min_games=5)
        assert _slugify("DNP Player") not in result  # excluded because minutes=0


# ─────────────────────────────────────────────────────────────────────────────
# _POSITION_DEFAULT_CV constants
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionDefaultCV:
    def test_all_positions_have_cv(self):
        for pos in ("PG", "SG", "SF", "PF", "C"):
            assert pos in _POSITION_DEFAULT_CV
            assert _CV_MIN <= _POSITION_DEFAULT_CV[pos] <= _CV_MAX

    def test_fallback_in_range(self):
        assert _CV_MIN <= _POSITION_DEFAULT_CV_FALLBACK <= _CV_MAX

    def test_guard_positions_higher_than_bigs(self):
        """Guards (PG/SG) should have higher CV than bigs (C) — more role volatility."""
        assert _POSITION_DEFAULT_CV["PG"] > _POSITION_DEFAULT_CV["C"]


# ─────────────────────────────────────────────────────────────────────────────
# CanonicalNBAProjectionEngine — per-player variance integration
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineVarianceModel:
    """Test that the engine outputs correct StdDev / Floor / Ceiling columns."""

    def _run_engine_no_db(self, slate_df: pd.DataFrame, site: str = "DK") -> pd.DataFrame:
        """Run the engine with all DB lookups mocked to return empty (no history)."""
        engine = CanonicalNBAProjectionEngine()
        ctx = _ctx(site)
        with (
            patch("analysis.core.projection_engine._load_game_log_baseline", return_value={}),
            patch("analysis.core.projection_engine._load_stddev_baseline", return_value={}),
            patch("analysis.core.projection_engine.load_dvp_table", return_value={}),
            patch("analysis.core.projection_engine.get_rest_multipliers", return_value={}),
            patch("analysis.core.projection_engine.get_blowout_multipliers", return_value={}),
        ):
            return engine.generate(slate_df, ctx)

    def test_stddev_column_present(self):
        result = self._run_engine_no_db(_slate_df())
        assert "StdDev" in result.columns, "StdDev column missing from output"

    def test_floor_column_present(self):
        result = self._run_engine_no_db(_slate_df())
        assert "Floor" in result.columns

    def test_ceiling_column_present(self):
        result = self._run_engine_no_db(_slate_df())
        assert "Ceiling" in result.columns

    def test_floor_never_negative(self):
        """Floor should be clipped at 0."""
        result = self._run_engine_no_db(_slate_df())
        assert (result["Floor"] >= 0).all(), "Floor has negative values"

    def test_ceiling_gt_proj(self):
        """Ceiling must be >= Proj for every player."""
        result = self._run_engine_no_db(_slate_df())
        assert (result["Ceiling"] >= result["Proj"]).all()

    def test_floor_lt_proj(self):
        """Floor must be <= Proj for every player with Proj > 0."""
        result = self._run_engine_no_db(_slate_df())
        positive = result[result["Proj"] > 0]
        assert (positive["Floor"] <= positive["Proj"]).all()

    def test_position_fallback_cv_applied(self):
        """Without DB history, position CVs are used — StdDev = Proj × position_cv."""
        result = self._run_engine_no_db(_slate_df())
        for _, row in result.iterrows():
            pos = str(row.get("Pos", "")).split("/")[0].strip().upper()
            expected_cv = _POSITION_DEFAULT_CV.get(pos, _POSITION_DEFAULT_CV_FALLBACK)
            expected_std = round(float(row["Proj"]) * expected_cv, 3)
            assert abs(float(row["StdDev"]) - expected_std) < 0.01, (
                f"StdDev mismatch for {row.get('Name')}: expected {expected_std}, "
                f"got {row['StdDev']}"
            )

    def test_individual_cv_used_when_available(self):
        """When stddev_baseline has an entry, it overrides the position CV."""
        slate = _slate_df([
            {"DFS_ID": "p1", "Name": "LeBron James", "Team": "LAL", "Opp": "BOS",
             "Pos": "SF", "Salary": 9800, "Own": 25.0, "Base_Proj": 50.0},
        ])
        # LeBron's individual CV is 0.20 (different from SF default 0.21)
        slug = _slugify("LeBron James")
        mock_stddev = {slug: {"cv": 0.20, "std": 10.0, "games": 18}}

        engine = CanonicalNBAProjectionEngine()
        ctx = _ctx()
        with (
            patch("analysis.core.projection_engine._load_game_log_baseline", return_value={}),
            patch("analysis.core.projection_engine._load_stddev_baseline", return_value=mock_stddev),
            patch("analysis.core.projection_engine.load_dvp_table", return_value={}),
            patch("analysis.core.projection_engine.get_rest_multipliers", return_value={}),
            patch("analysis.core.projection_engine.get_blowout_multipliers", return_value={}),
        ):
            result = engine.generate(slate, ctx)

        row = result.iloc[0]
        expected_std = round(50.0 * 0.20, 3)  # Proj × individual CV
        assert abs(float(row["StdDev"]) - expected_std) < 0.01

    def test_floor_formula(self):
        """Floor = (Proj - StdDev).clip(0)."""
        result = self._run_engine_no_db(_slate_df())
        for _, row in result.iterrows():
            expected_floor = max(0.0, float(row["Proj"]) - float(row["StdDev"]))
            assert abs(float(row["Floor"]) - expected_floor) < 0.01, (
                f"Floor formula wrong for {row.get('Name')}: "
                f"Proj={row['Proj']}, StdDev={row['StdDev']}, "
                f"Floor={row['Floor']}, expected={expected_floor}"
            )

    def test_ceiling_formula(self):
        """Ceiling = Proj + 1.5 × StdDev."""
        result = self._run_engine_no_db(_slate_df())
        for _, row in result.iterrows():
            expected_ceil = float(row["Proj"]) + 1.5 * float(row["StdDev"])
            assert abs(float(row["Ceiling"]) - expected_ceil) < 0.05, (
                f"Ceiling formula wrong for {row.get('Name')}: "
                f"expected {expected_ceil}, got {row['Ceiling']}"
            )

    def test_high_cv_widens_range(self):
        """A player with high historical variance should have a wider Floor-Ceiling spread."""
        # Two players with identical projection; one has high CV, one has low
        slate = _slate_df([
            {"DFS_ID": "p_high", "Name": "High Var", "Team": "BOS", "Opp": "MIA",
             "Pos": "PG", "Salary": 8000, "Own": 10.0, "Base_Proj": 40.0},
            {"DFS_ID": "p_low", "Name": "Low Var", "Team": "MIA", "Opp": "BOS",
             "Pos": "PG", "Salary": 8000, "Own": 10.0, "Base_Proj": 40.0},
        ])
        slug_high = _slugify("High Var")
        slug_low = _slugify("Low Var")
        mock_stddev = {
            slug_high: {"cv": 0.35, "std": 14.0, "games": 15},
            slug_low:  {"cv": 0.12, "std": 4.8,  "games": 15},
        }

        engine = CanonicalNBAProjectionEngine()
        ctx = _ctx()
        with (
            patch("analysis.core.projection_engine._load_game_log_baseline", return_value={}),
            patch("analysis.core.projection_engine._load_stddev_baseline", return_value=mock_stddev),
            patch("analysis.core.projection_engine.load_dvp_table", return_value={}),
            patch("analysis.core.projection_engine.get_rest_multipliers", return_value={}),
            patch("analysis.core.projection_engine.get_blowout_multipliers", return_value={}),
        ):
            result = engine.generate(slate, ctx)

        high_row = result[result["Name"] == "High Var"].iloc[0]
        low_row  = result[result["Name"] == "Low Var"].iloc[0]

        high_range = float(high_row["Ceiling"]) - float(high_row["Floor"])
        low_range  = float(low_row["Ceiling"])  - float(low_row["Floor"])
        assert high_range > low_range, (
            f"High-CV player should have wider range. "
            f"High range={high_range:.2f}, Low range={low_range:.2f}"
        )

    def test_zero_proj_floor_is_zero(self):
        """A player with Proj=0 should have Floor=0 (not negative)."""
        slate = _slate_df([
            {"DFS_ID": "p1", "Name": "Zero Proj", "Team": "BOS", "Opp": "MIA",
             "Pos": "SF", "Salary": 4000, "Own": 1.0, "Base_Proj": 0.0},
        ])
        result = self._run_engine_no_db(slate)
        assert result.iloc[0]["Floor"] == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# estimate_ownership — new Proj/Own leverage formula
# ─────────────────────────────────────────────────────────────────────────────

class TestEstimateOwnershipLeverage:
    def _basic_df(self):
        return pd.DataFrame({
            "Name": ["A", "B", "C"],
            "Salary": [9000, 7000, 5000],
            "Proj_Final": [45.0, 35.0, 20.0],
        })

    def test_leverage_score_column_present(self):
        result = estimate_ownership(self._basic_df())
        assert "Leverage_Score" in result.columns

    def test_own_est_column_present(self):
        result = estimate_ownership(self._basic_df())
        assert "Own_Est" in result.columns

    def test_leverage_is_proj_over_own(self):
        """Leverage_Score ≈ Proj_Final / Own_Est for each row."""
        df = self._basic_df()
        result = estimate_ownership(df)
        for _, row in result.iterrows():
            own = max(row["Own_Est"], 0.5)  # same floor as impl
            expected = round(row["Proj_Final"] / own, 3)
            assert abs(row["Leverage_Score"] - expected) < 0.01, (
                f"Leverage wrong for {row['Name']}: got {row['Leverage_Score']}, "
                f"expected {expected}"
            )

    def test_high_proj_low_own_beats_chalk(self):
        """Same projection but lower salary → lower modelled ownership → higher leverage."""
        # estimate_ownership computes Own_Est from salary+projection, so drive ownership
        # through salary: same Proj but different Salary → different Own_Est.
        df = pd.DataFrame({
            "Name": ["Contrarian", "Chalk"],
            "Salary": [5000, 9800],     # low salary = less chalk
            "Proj_Final": [40.0, 40.0], # identical projection
        })
        result = estimate_ownership(df)
        cont  = result[result["Name"] == "Contrarian"]["Leverage_Score"].values[0]
        chalk = result[result["Name"] == "Chalk"]["Leverage_Score"].values[0]
        assert cont > chalk, (
            f"Low-salary (contrarian) player should have higher leverage. "
            f"Contrarian={cont}, Chalk={chalk}"
        )

    def test_zero_ownership_doesnt_divide_by_zero(self):
        """Own_Est = 0 should be floored to 0.5 so no ZeroDivisionError."""
        df = pd.DataFrame({
            "Name": ["Ghost"],
            "Salary": [5000],
            "Proj_Final": [25.0],
            "Own_Est": [0.0],
        })
        result = estimate_ownership(df)
        assert result["Leverage_Score"].notna().all()
        assert result["Leverage_Score"].iloc[0] > 0

    def test_internal_rank_cols_dropped(self):
        """proj_rank, sal_rank, own_score should not leak into the output."""
        result = estimate_ownership(self._basic_df())
        for col in ("proj_rank", "sal_rank", "own_score"):
            assert col not in result.columns, f"Internal column '{col}' leaked into output"

    def test_proj_fallback_cols(self):
        """estimate_ownership accepts Base_Proj when Proj_Final is absent."""
        df = pd.DataFrame({
            "Name": ["X"],
            "Salary": [7000],
            "Base_Proj": [38.0],
        })
        result = estimate_ownership(df)
        assert result["Proj_Final"].iloc[0] == pytest.approx(38.0)

    def test_own_est_range(self):
        """Own_Est should be in a plausible ownership range (0–60%)."""
        result = estimate_ownership(self._basic_df())
        assert (result["Own_Est"] >= 0).all()
        assert (result["Own_Est"] <= 60.0).all()

    def test_returns_dataframe(self):
        result = estimate_ownership(self._basic_df())
        assert isinstance(result, pd.DataFrame)

    def test_row_count_unchanged(self):
        df = self._basic_df()
        result = estimate_ownership(df)
        assert len(result) == len(df)
