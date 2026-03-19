"""
Tests for analysis/nba/b2b.py — B2B rest-days and blowout risk adjustments.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from analysis.nba.b2b import (
    _days_of_rest,
    compute_rest_days,
    get_blowout_multipliers,
    get_rest_multipliers,
)


# ─────────────────────────────────────────────────────────────────────────────
# _days_of_rest
# ─────────────────────────────────────────────────────────────────────────────

class TestDaysOfRest:
    def test_b2b_is_zero(self):
        """Played yesterday → 0 days rest (B2B)."""
        last = date(2026, 3, 10)
        slate = date(2026, 3, 11)
        assert _days_of_rest(last, slate) == 0

    def test_one_day_rest(self):
        last = date(2026, 3, 9)
        slate = date(2026, 3, 11)
        assert _days_of_rest(last, slate) == 1

    def test_two_day_rest(self):
        last = date(2026, 3, 8)
        slate = date(2026, 3, 11)
        assert _days_of_rest(last, slate) == 2

    def test_three_day_rest(self):
        last = date(2026, 3, 7)
        slate = date(2026, 3, 11)
        assert _days_of_rest(last, slate) == 3

    def test_same_day_clamps_to_zero(self):
        """Same-day edge: treated as 0 (clamped, not negative)."""
        d = date(2026, 3, 11)
        assert _days_of_rest(d, d) == 0


# ─────────────────────────────────────────────────────────────────────────────
# compute_rest_days — with mocked DB
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeRestDays:
    def test_returns_dict(self, tmp_path):
        """Returns a dict even when DB doesn't exist."""
        result = compute_rest_days(
            slate_date=date(2026, 3, 11),
            db_path=tmp_path / "nonexistent.duckdb",
        )
        assert isinstance(result, dict)

    def test_empty_on_missing_db(self, tmp_path):
        """Missing DB → empty dict (graceful degradation)."""
        result = compute_rest_days(
            slate_date=date(2026, 3, 11),
            db_path=tmp_path / "nonexistent.duckdb",
        )
        assert result == {}

    def test_b2b_team_detected(self, tmp_path):
        """A team whose last game was yesterday maps to 0 days rest."""
        import duckdb

        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
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
        # BOS played yesterday (2026-03-10), slate is 2026-03-11
        con.execute("""
            INSERT INTO player_game_logs VALUES
                ('Player A', 'BOS', 'MIA', '2026-03-10', 40.0, 35.0, 32.0),
                ('Player B', 'MIA', 'BOS', '2026-03-08', 38.0, 33.0, 28.0)
        """)
        con.close()

        result = compute_rest_days(
            slate_date=date(2026, 3, 11),
            db_path=db_path,
        )

        assert result["BOS"] == 0   # B2B
        assert result["MIA"] == 2   # Two days rest

    def test_excludes_same_day_games(self, tmp_path):
        """Games on the slate date itself must not count as 'last game'."""
        import duckdb

        db_path = tmp_path / "test2.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE player_game_logs (
                player_name VARCHAR, team VARCHAR, opponent VARCHAR,
                game_date DATE, dk_pts DOUBLE, fd_pts DOUBLE, minutes DOUBLE
            )
        """)
        # Insert a game ON the slate date and one two days before
        con.execute("""
            INSERT INTO player_game_logs VALUES
                ('X', 'LAL', 'GSW', '2026-03-11', 30.0, 25.0, 25.0),
                ('Y', 'LAL', 'GSW', '2026-03-09', 30.0, 25.0, 25.0)
        """)
        con.close()

        result = compute_rest_days(
            slate_date=date(2026, 3, 11),
            db_path=db_path,
        )
        # The 2026-03-09 row is the last game before the slate (03-11 row excluded)
        # days_of_rest(2026-03-09, 2026-03-11) = (11-9) - 1 = 1
        assert result.get("LAL") == 1


# ─────────────────────────────────────────────────────────────────────────────
# get_rest_multipliers
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRestMultipliers:
    def test_empty_on_missing_db(self, tmp_path):
        result = get_rest_multipliers(
            slate_date=date(2026, 3, 11),
            db_path=tmp_path / "nonexistent.duckdb",
        )
        assert result == {}

    def test_b2b_multiplier(self, tmp_path):
        """B2B team (0 days rest) gets 0.96 multiplier."""
        import duckdb

        db_path = tmp_path / "b2b.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE player_game_logs (
                player_name VARCHAR, team VARCHAR, opponent VARCHAR,
                game_date DATE, dk_pts DOUBLE, fd_pts DOUBLE, minutes DOUBLE
            )
        """)
        con.execute("INSERT INTO player_game_logs VALUES ('A', 'BOS', 'MIA', '2026-03-10', 40, 35, 32)")
        con.close()

        mults = get_rest_multipliers(slate_date=date(2026, 3, 11), db_path=db_path)
        assert mults["BOS"] == pytest.approx(0.960)

    def test_short_rest_multiplier(self, tmp_path):
        """1-day rest team gets 0.985 multiplier."""
        import duckdb

        db_path = tmp_path / "short.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE player_game_logs (
                player_name VARCHAR, team VARCHAR, opponent VARCHAR,
                game_date DATE, dk_pts DOUBLE, fd_pts DOUBLE, minutes DOUBLE
            )
        """)
        con.execute("INSERT INTO player_game_logs VALUES ('A', 'LAL', 'GSW', '2026-03-09', 30, 25, 28)")
        con.close()

        mults = get_rest_multipliers(slate_date=date(2026, 3, 11), db_path=db_path)
        assert mults["LAL"] == pytest.approx(0.985)

    def test_normal_rest_multiplier(self, tmp_path):
        """2-day rest team gets 1.0 multiplier (neutral)."""
        import duckdb

        db_path = tmp_path / "normal.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE player_game_logs (
                player_name VARCHAR, team VARCHAR, opponent VARCHAR,
                game_date DATE, dk_pts DOUBLE, fd_pts DOUBLE, minutes DOUBLE
            )
        """)
        con.execute("INSERT INTO player_game_logs VALUES ('A', 'MIA', 'BOS', '2026-03-08', 38, 33, 30)")
        con.close()

        mults = get_rest_multipliers(slate_date=date(2026, 3, 11), db_path=db_path)
        assert mults["MIA"] == pytest.approx(1.000)

    def test_extended_rest_multiplier(self, tmp_path):
        """3+ days rest team gets 1.01 multiplier (freshness bonus)."""
        import duckdb

        db_path = tmp_path / "long.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE player_game_logs (
                player_name VARCHAR, team VARCHAR, opponent VARCHAR,
                game_date DATE, dk_pts DOUBLE, fd_pts DOUBLE, minutes DOUBLE
            )
        """)
        # Last game was 4 days ago → 3 days rest
        con.execute("INSERT INTO player_game_logs VALUES ('A', 'DEN', 'OKC', '2026-03-07', 35, 30, 26)")
        con.close()

        mults = get_rest_multipliers(slate_date=date(2026, 3, 11), db_path=db_path)
        assert mults["DEN"] == pytest.approx(1.010)

    def test_result_is_dict(self, tmp_path):
        result = get_rest_multipliers(
            slate_date=date(2026, 3, 11),
            db_path=tmp_path / "none.duckdb",
        )
        assert isinstance(result, dict)


# ─────────────────────────────────────────────────────────────────────────────
# get_blowout_multipliers
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBlowoutMultipliers:
    def test_empty_input_returns_empty(self):
        assert get_blowout_multipliers({}) == {}

    def test_neutral_spread_no_penalty(self):
        totals = {"BOS": {"spread": 3.0, "team_total": 116.0, "is_home": 1}}
        mults = get_blowout_multipliers(totals)
        assert mults["BOS"] == pytest.approx(1.000)

    def test_moderate_underdog_penalized(self):
        """spread > 10 but ≤ 15 → 0.97"""
        totals = {"ORL": {"spread": 12.0, "team_total": 105.0, "is_home": 0}}
        mults = get_blowout_multipliers(totals)
        assert mults["ORL"] == pytest.approx(0.970)

    def test_heavy_underdog_penalized(self):
        """spread > 15 → 0.93"""
        totals = {"CHA": {"spread": 16.5, "team_total": 102.0, "is_home": 0}}
        mults = get_blowout_multipliers(totals)
        assert mults["CHA"] == pytest.approx(0.930)

    def test_boundary_exactly_15_not_heavy(self):
        """spread == 15.0 → 0.97 (not 0.93, threshold is strictly > 15)"""
        totals = {"DET": {"spread": 15.0, "team_total": 103.0, "is_home": 0}}
        mults = get_blowout_multipliers(totals)
        assert mults["DET"] == pytest.approx(0.970)

    def test_boundary_exactly_10_neutral(self):
        """spread == 10.0 → 1.0 (threshold is strictly > 10)"""
        totals = {"WAS": {"spread": 10.0, "team_total": 106.0, "is_home": 0}}
        mults = get_blowout_multipliers(totals)
        assert mults["WAS"] == pytest.approx(1.000)

    def test_heavy_favourite_no_penalty(self):
        """Favourites (negative spread) don't get penalised."""
        totals = {"BOS": {"spread": -16.5, "team_total": 120.0, "is_home": 1}}
        mults = get_blowout_multipliers(totals)
        assert mults["BOS"] == pytest.approx(1.000)

    def test_multiple_teams(self):
        """Mix of underdog/neutral/favourite all computed in one call."""
        totals = {
            "BOS": {"spread": -16.5, "team_total": 120.0},  # favourite → 1.0
            "CHA": {"spread": 16.5,  "team_total": 102.0},  # heavy dog → 0.93
            "MIA": {"spread": 4.0,   "team_total": 113.0},  # neutral  → 1.0
            "ORL": {"spread": 12.5,  "team_total": 105.0},  # moderate → 0.97
        }
        mults = get_blowout_multipliers(totals)
        assert mults["BOS"] == pytest.approx(1.000)
        assert mults["CHA"] == pytest.approx(0.930)
        assert mults["MIA"] == pytest.approx(1.000)
        assert mults["ORL"] == pytest.approx(0.970)

    def test_missing_spread_key_defaults_to_neutral(self):
        """If a team has no 'spread' key the multiplier defaults to 1.0."""
        totals = {"NYK": {"team_total": 112.0}}  # no spread key
        mults = get_blowout_multipliers(totals)
        assert mults["NYK"] == pytest.approx(1.000)

    def test_team_keys_uppercased(self):
        """Team keys in the output are uppercased regardless of input case."""
        totals = {"bos": {"spread": 12.0, "team_total": 106.0}}
        mults = get_blowout_multipliers(totals)
        assert "BOS" in mults
