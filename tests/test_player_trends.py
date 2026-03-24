"""
Tests for Player Trends — Last 10 Analytics
=============================================
Covers:
  * DK fantasy scoring — base stats, double-double bonus, triple-double bonus
  * FD fantasy scoring — correct per-stat multipliers
  * compute_dd_td() logic — all boundary cases
  * score_game_row() — unified entry point
  * _compute_split() aggregation — last5, last10, season slices
  * get_player_trends() — full trend profile via in-memory DuckDB
  * search_players() — fuzzy search logic
  * get_slate_trends() — slate-wide aggregation
  * Missing / null column handling
  * Game log ordering (newest first)
  * API response shapes (router integration via FastAPI TestClient)
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure backend package is importable
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Fantasy scoring unit tests
# ---------------------------------------------------------------------------

from services.fantasy_scoring import (
    compute_dd_td,
    compute_dk_score,
    compute_fd_score,
    score_game_row,
    _f,
)


class TestSafeFloat:
    def test_none_returns_zero(self):
        assert _f(None) == 0.0

    def test_nan_returns_zero(self):
        assert _f(float("nan")) == 0.0

    def test_inf_returns_zero(self):
        assert _f(float("inf")) == 0.0

    def test_string_number(self):
        assert _f("3.5") == 3.5

    def test_int(self):
        assert _f(10) == 10.0


class TestComputeDdTd:
    def test_no_bonus_below_10(self):
        dd, td = compute_dd_td(9, 9, 9, 0, 0)
        assert not dd
        assert not td

    def test_double_double_pts_reb(self):
        dd, td = compute_dd_td(20, 10, 5, 0, 0)
        assert dd
        assert not td

    def test_double_double_pts_ast(self):
        dd, td = compute_dd_td(10, 5, 10, 0, 0)
        assert dd
        assert not td

    def test_triple_double(self):
        dd, td = compute_dd_td(10, 10, 10, 0, 0)
        assert not dd   # TD supersedes DD
        assert td

    def test_triple_double_with_blocks_steals(self):
        # reb=10, blk=10, stl=10 → 3 qualifying categories → TD (not DD)
        dd, td = compute_dd_td(5, 10, 5, 10, 10)
        assert not dd
        assert td

    def test_exactly_one_category_no_bonus(self):
        dd, td = compute_dd_td(30, 5, 4, 0, 0)
        assert not dd
        assert not td

    def test_all_five_categories_triple_double(self):
        dd, td = compute_dd_td(20, 15, 12, 10, 10)
        assert not dd
        assert td


class TestComputeDkScore:
    def test_basic_stats(self):
        # 20pts + 8reb + 5ast + 0stl + 0blk + 1tov + 0 3pm = 20 + 10 + 7.5 - 0.5 = 37
        score = compute_dk_score(pts=20, three_pm=0, reb=8, ast=5, stl=0, blk=0, tov=1)
        assert score == pytest.approx(37.0, abs=0.01)

    def test_three_pm_bonus(self):
        # 3pts (from 1 three) + 0.5 bonus = 3.5 for the three alone
        score = compute_dk_score(pts=3, three_pm=1, reb=0, ast=0, stl=0, blk=0, tov=0)
        assert score == pytest.approx(3.5, abs=0.01)

    def test_double_double_bonus(self):
        # 20pts + 10reb = DD, no TD; base = 20 + 12.5 = 32.5; +1.5 DD = 34
        score = compute_dk_score(pts=20, three_pm=0, reb=10, ast=5, stl=0, blk=0, tov=0)
        # base: 20*1 + 10*1.25 + 5*1.5 = 20 + 12.5 + 7.5 = 40 + 1.5 DD = 41.5
        assert score == pytest.approx(41.5, abs=0.01)

    def test_triple_double_bonus(self):
        # 10pts + 10reb + 10ast = TD bonus (+3.0), not DD (+1.5)
        score = compute_dk_score(pts=10, three_pm=0, reb=10, ast=10, stl=0, blk=0, tov=0)
        # base: 10 + 12.5 + 15 = 37.5 + 3.0 = 40.5
        assert score == pytest.approx(40.5, abs=0.01)

    def test_triple_double_supersedes_double_double(self):
        dd_score = compute_dk_score(pts=10, three_pm=0, reb=10, ast=5, stl=0, blk=0, tov=0)
        td_score  = compute_dk_score(pts=10, three_pm=0, reb=10, ast=10, stl=0, blk=0, tov=0)
        # TD bonus (3.0) > DD bonus (1.5)
        assert td_score > dd_score

    def test_turnover_penalty(self):
        score = compute_dk_score(pts=0, three_pm=0, reb=0, ast=0, stl=0, blk=0, tov=4)
        assert score == pytest.approx(-2.0, abs=0.001)

    def test_steal_block_values(self):
        score = compute_dk_score(pts=0, three_pm=0, reb=0, ast=0, stl=1, blk=1, tov=0)
        assert score == pytest.approx(4.0, abs=0.001)

    def test_none_inputs_treated_as_zero(self):
        score = compute_dk_score(
            pts=None, three_pm=None, reb=None,  # type: ignore[arg-type]
            ast=None, stl=None, blk=None, tov=None,  # type: ignore[arg-type]
        )
        assert score == 0.0


class TestComputeFdScore:
    def test_basic_stats(self):
        # 5 FGM, 2 FTM, 1 3PM, 5 REB, 3 AST, 1 STL, 0 BLK, 1 TOV
        # = 10 + 2 + 1 + 6 + 4.5 + 3 + 0 - 1 = 25.5
        score = compute_fd_score(fgm=5, ftm=2, three_pm=1, reb=5, ast=3, stl=1, blk=0, tov=1)
        assert score == pytest.approx(25.5, abs=0.01)

    def test_three_pm_earns_extra_bonus(self):
        # 1 FGM (3PM) = +2 (FGM) + +1 (3PM bonus) = 3
        score = compute_fd_score(fgm=1, ftm=0, three_pm=1, reb=0, ast=0, stl=0, blk=0, tov=0)
        assert score == pytest.approx(3.0, abs=0.001)

    def test_block_steal_values(self):
        # 1 BLK + 1 STL = 3 + 3 = 6
        score = compute_fd_score(fgm=0, ftm=0, three_pm=0, reb=0, ast=0, stl=1, blk=1, tov=0)
        assert score == pytest.approx(6.0, abs=0.001)

    def test_turnover_penalty(self):
        score = compute_fd_score(fgm=0, ftm=0, three_pm=0, reb=0, ast=0, stl=0, blk=0, tov=3)
        assert score == pytest.approx(-3.0, abs=0.001)

    def test_free_throws(self):
        score = compute_fd_score(fgm=0, ftm=4, three_pm=0, reb=0, ast=0, stl=0, blk=0, tov=0)
        assert score == pytest.approx(4.0, abs=0.001)

    def test_none_inputs_treated_as_zero(self):
        score = compute_fd_score(
            fgm=None, ftm=None, three_pm=None,  # type: ignore[arg-type]
            reb=None, ast=None, stl=None, blk=None, tov=None,  # type: ignore[arg-type]
        )
        assert score == 0.0


class TestScoreGameRow:
    def test_combined_row(self):
        row = {
            "points": 30,
            "three_pointers": 3,
            "rebounds": 8,
            "assists": 6,
            "steals": 1,
            "blocks": 0,
            "turnovers": 2,
            "fg_made": 11,
            "ft_made": 4,
        }
        dk, fd = score_game_row(row)
        # DK: 30 + 3*0.5 + 8*1.25 + 6*1.5 + 1*2 - 2*0.5 = 30+1.5+10+9+2-1 = 51.5
        assert dk == pytest.approx(51.5, abs=0.01)
        # FD: 11*2 + 4*1 + 3*1 + 8*1.2 + 6*1.5 + 1*3 + 0 - 2*1 = 22+4+3+9.6+9+3-2 = 48.6
        assert fd == pytest.approx(48.6, abs=0.01)

    def test_empty_row_returns_zeros(self):
        dk, fd = score_game_row({})
        assert dk == 0.0
        assert fd == 0.0

    def test_null_values_in_row(self):
        row = {"points": None, "rebounds": None, "fg_made": None}
        dk, fd = score_game_row(row)
        assert dk == 0.0
        assert fd == 0.0

    def test_double_double_in_row(self):
        row = {
            "points": 25, "three_pointers": 2, "rebounds": 10,
            "assists": 5, "steals": 1, "blocks": 0, "turnovers": 1,
            "fg_made": 9, "ft_made": 5,
        }
        dk, _ = score_game_row(row)
        # base: 25 + 2*0.5 + 10*1.25 + 5*1.5 + 2 - 0.5 = 25+1+12.5+7.5+2-0.5 = 47.5 + 1.5 DD
        assert dk == pytest.approx(49.0, abs=0.01)


# ---------------------------------------------------------------------------
# Service-layer tests (in-memory DuckDB)
# ---------------------------------------------------------------------------

import duckdb  # guaranteed available (analysis layer dep)
from services.player_trends_service import (
    _compute_split,
    get_player_trends,
    get_slate_trends,
    search_players,
)

_GL_DDL = """
CREATE TABLE IF NOT EXISTS player_game_logs (
    game_id        VARCHAR PRIMARY KEY,
    player_id      VARCHAR,
    player_name    VARCHAR,
    team           VARCHAR,
    opponent       VARCHAR,
    game_date      DATE,
    season         VARCHAR,
    wl             VARCHAR,
    is_home        BOOLEAN,
    minutes        DOUBLE DEFAULT 0,
    points         DOUBLE,
    rebounds       DOUBLE,
    assists        DOUBLE,
    steals         DOUBLE,
    blocks         DOUBLE,
    turnovers      DOUBLE,
    pf             DOUBLE,
    three_pointers DOUBLE,
    fg_attempted   DOUBLE,
    fg_made        DOUBLE,
    fg_pct         DOUBLE,
    ft_attempted   DOUBLE,
    ft_made        DOUBLE,
    dk_pts         DOUBLE,
    fd_pts         DOUBLE,
    ingested_at    TIMESTAMPTZ DEFAULT now()
)
"""


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "dfs_edge.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(_GL_DDL)
    con.close()
    return db_path


def _insert_row(
    db_path: Path,
    **kwargs,
) -> None:
    """Insert one row into player_game_logs with sensible defaults."""
    defaults = dict(
        game_id="g_default",
        player_id="p1",
        player_name="Test Player",
        team="LAL",
        opponent="BOS",
        game_date=date(2026, 1, 1),
        season="2025-26",
        wl="W",
        is_home=True,
        minutes=32.0,
        points=20.0,
        rebounds=5.0,
        assists=4.0,
        steals=1.0,
        blocks=0.0,
        turnovers=2.0,
        pf=2.0,
        three_pointers=2.0,
        fg_made=8.0,
        ft_made=2.0,
        fg_pct=0.50,
        dk_pts=37.5,
        fd_pts=32.0,
    )
    defaults.update(kwargs)
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        INSERT INTO player_game_logs
            (game_id, player_id, player_name, team, opponent, game_date, season,
             wl, is_home, minutes, points, rebounds, assists, steals, blocks,
             turnovers, pf, three_pointers, fg_made, ft_made, fg_pct, dk_pts, fd_pts)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT DO NOTHING
        """,
        [
            defaults["game_id"], defaults["player_id"], defaults["player_name"],
            defaults["team"], defaults["opponent"], defaults["game_date"],
            defaults["season"], defaults["wl"], defaults["is_home"],
            defaults["minutes"], defaults["points"], defaults["rebounds"],
            defaults["assists"], defaults["steals"], defaults["blocks"],
            defaults["turnovers"], defaults["pf"], defaults["three_pointers"],
            defaults["fg_made"], defaults["ft_made"], defaults["fg_pct"],
            defaults["dk_pts"], defaults["fd_pts"],
        ],
    )
    con.close()


class TestComputeSplit:
    def test_empty_returns_none(self):
        assert _compute_split([]) is None

    def test_single_game(self):
        game = {
            "minutes": 32, "points": 20, "rebounds": 5, "assists": 4,
            "steals": 1, "blocks": 0, "turnovers": 2, "three_pointers": 2,
            "fg_made": 8, "ft_made": 2, "dk_points": 37.5, "fd_points": 32.0,
        }
        result = _compute_split([game])
        assert result is not None
        assert result["games_used"] == 1
        assert result["avg_points"] == 20.0
        assert result["avg_dk_points"] == 37.5
        assert result["avg_fd_points"] == 32.0
        # stddev is None with < 2 games
        assert result["stddev_dk_points"] is None

    def test_averages_correct(self):
        games = [
            {"minutes": 30, "points": 20, "rebounds": 5, "assists": 3,
             "steals": 1, "blocks": 0, "turnovers": 1, "three_pointers": 1,
             "fg_made": 7, "ft_made": 5, "dk_points": 35.0, "fd_points": 26.0},
            {"minutes": 34, "points": 30, "rebounds": 9, "assists": 7,
             "steals": 2, "blocks": 1, "turnovers": 3, "three_pointers": 3,
             "fg_made": 11, "ft_made": 6, "dk_points": 55.0, "fd_points": 48.0},
        ]
        result = _compute_split(games)
        assert result is not None
        assert result["games_used"] == 2
        assert result["avg_minutes"] == pytest.approx(32.0, abs=0.01)
        assert result["avg_points"] == pytest.approx(25.0, abs=0.01)
        assert result["avg_dk_points"] == pytest.approx(45.0, abs=0.01)
        # stddev should be populated with 2 games
        assert result["stddev_dk_points"] is not None

    def test_per_minute_rates(self):
        games = [
            {"minutes": 20, "points": 20, "rebounds": 0, "assists": 0,
             "steals": 0, "blocks": 0, "turnovers": 0, "three_pointers": 0,
             "fg_made": 8, "ft_made": 4, "dk_points": 20.0, "fd_points": 20.0},
        ]
        result = _compute_split(games)
        assert result is not None
        assert result["points_per_min"] == pytest.approx(1.0, abs=0.001)
        assert result["dk_points_per_min"] == pytest.approx(1.0, abs=0.001)

    def test_none_dk_fd_handled(self):
        """Games with None dk/fd points don't crash split computation."""
        games = [
            {"minutes": 30, "points": 20, "rebounds": 5, "assists": 3,
             "steals": 1, "blocks": 0, "turnovers": 1, "three_pointers": 1,
             "fg_made": 7, "ft_made": 5, "dk_points": None, "fd_points": None},
        ]
        result = _compute_split(games)
        assert result is not None
        assert result["avg_dk_points"] is None
        assert result["avg_fd_points"] is None


class TestGetPlayerTrends:
    def test_returns_none_when_db_missing(self, tmp_path):
        nonexistent = tmp_path / "missing.duckdb"
        with patch("services.player_trends_service._EDGE_DB", nonexistent):
            assert get_player_trends("LeBron James") is None

    def test_returns_none_when_player_not_found(self, tmp_path):
        db = _make_db(tmp_path)
        with patch("services.player_trends_service._EDGE_DB", db):
            result = get_player_trends("Nobody Here")
        assert result is None

    def test_returns_none_when_only_dnp_rows(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="DNP Player", minutes=0)
        with patch("services.player_trends_service._EDGE_DB", db):
            result = get_player_trends("DNP Player")
        assert result is None

    def test_basic_response_shape(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="LeBron James", minutes=35,
                    points=28, rebounds=8, assists=7)
        with patch("services.player_trends_service._EDGE_DB", db):
            result = get_player_trends("LeBron James")

        assert result is not None
        assert result["player_name"] == "LeBron James"
        assert result["team"] == "LAL"
        assert "last5" in result
        assert "last10" in result
        assert "season" in result
        assert "recent_games" in result
        assert result["total_games_available"] == 1

    def test_last5_and_last10_differ_with_enough_games(self, tmp_path):
        db = _make_db(tmp_path)
        base = date(2026, 3, 1)
        for i in range(12):
            _insert_row(
                db,
                game_id=f"g_{i}",
                player_name="Star Player",
                game_date=base + timedelta(days=i),
                minutes=30 + i,
                points=20 + i,
            )
        with patch("services.player_trends_service._EDGE_DB", db):
            result = get_player_trends("Star Player")

        assert result is not None
        assert result["last5"]["games_used"] == 5
        assert result["last10"]["games_used"] == 10
        # Last 5 should include more recent (higher) points
        assert result["last5"]["avg_points"] > result["last10"]["avg_points"]

    def test_games_ordered_newest_first(self, tmp_path):
        db = _make_db(tmp_path)
        base = date(2026, 1, 1)
        dates = [base, base + timedelta(days=1), base + timedelta(days=2)]
        for i, d in enumerate(dates):
            _insert_row(db, game_id=f"g_{i}", player_name="Time Player",
                        game_date=d, points=float(i * 10))
        with patch("services.player_trends_service._EDGE_DB", db):
            result = get_player_trends("Time Player")

        assert result is not None
        # Most recent game (day+2, pts=20) should come first
        dated = [g["game_date"] for g in result["recent_games"]]
        assert dated == sorted(dated, reverse=True)

    def test_season_split_uses_current_season(self, tmp_path):
        db = _make_db(tmp_path)
        # Insert games across two seasons
        _insert_row(db, game_id="old_g1", player_name="Vet", season="2024-25",
                    game_date=date(2025, 3, 1), minutes=28, points=15)
        _insert_row(db, game_id="new_g1", player_name="Vet", season="2025-26",
                    game_date=date(2026, 3, 1), minutes=32, points=22)
        with patch("services.player_trends_service._EDGE_DB", db):
            result = get_player_trends("Vet")

        assert result is not None
        # Season split should only include '2025-26' games
        assert result["current_season"] == "2025-26"
        assert result["season"]["games_used"] == 1
        assert result["season"]["avg_points"] == pytest.approx(22.0)

    def test_dk_fd_scores_recomputed_from_raw_stats(self, tmp_path):
        """Service recomputes DK/FD scores; stored dk_pts/fd_pts are ignored."""
        db = _make_db(tmp_path)
        # Insert a row with 0 stored dk/fd but real stats
        _insert_row(
            db, game_id="g1", player_name="Scorer",
            points=20, three_pointers=2, rebounds=6, assists=4,
            steals=1, blocks=0, turnovers=1,
            fg_made=8, ft_made=4,
            dk_pts=0, fd_pts=0,  # intentionally wrong stored values
        )
        with patch("services.player_trends_service._EDGE_DB", db):
            result = get_player_trends("Scorer")

        game = result["recent_games"][0]
        # DK: 20 + 2*0.5 + 6*1.25 + 4*1.5 + 1*2 - 1*0.5 = 20+1+7.5+6+2-0.5 = 36
        assert game["dk_points"] == pytest.approx(36.0, abs=0.1)
        # FD: 8*2 + 4*1 + 2*1 + 6*1.2 + 4*1.5 + 1*3 - 1*1 = 16+4+2+7.2+6+3-1 = 37.2
        assert game["fd_points"] == pytest.approx(37.2, abs=0.1)

    def test_case_insensitive_name_lookup(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="Stephen Curry", minutes=35)
        with patch("services.player_trends_service._EDGE_DB", db):
            # Lookup with different capitalisation
            result_lower = get_player_trends("stephen curry")
            result_mixed = get_player_trends("Stephen Curry")

        assert result_lower is not None
        assert result_mixed is not None


class TestSearchPlayers:
    def test_returns_empty_when_query_too_short(self, tmp_path):
        db = _make_db(tmp_path)
        with patch("services.player_trends_service._EDGE_DB", db):
            results = search_players("a")
        assert results == []

    def test_returns_empty_when_db_missing(self, tmp_path):
        with patch("services.player_trends_service._EDGE_DB", tmp_path / "nope.duckdb"):
            results = search_players("LeBron")
        assert results == []

    def test_finds_player_by_partial_name(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="LeBron James", team="LAL")
        with patch("services.player_trends_service._EDGE_DB", db):
            results = search_players("LeBron")
        assert len(results) == 1
        assert results[0]["player_name"] == "LeBron James"
        assert results[0]["team"] == "LAL"

    def test_case_insensitive_search(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="LeBron James")
        with patch("services.player_trends_service._EDGE_DB", db):
            results = search_players("lebron")
        assert len(results) == 1

    def test_returns_distinct_players(self, tmp_path):
        db = _make_db(tmp_path)
        # Same player, two games
        _insert_row(db, game_id="g1", player_name="Giannis Antetokounmpo", game_date=date(2026, 1, 1))
        _insert_row(db, game_id="g2", player_name="Giannis Antetokounmpo", game_date=date(2026, 1, 2))
        with patch("services.player_trends_service._EDGE_DB", db):
            results = search_players("Giannis")
        assert len(results) == 1

    def test_no_match_returns_empty(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="LeBron James")
        with patch("services.player_trends_service._EDGE_DB", db):
            results = search_players("Curry")
        assert results == []


class TestGetSlateTrends:
    def test_returns_empty_when_db_missing(self, tmp_path):
        with patch("services.player_trends_service._EDGE_DB", tmp_path / "nope.duckdb"):
            assert get_slate_trends() == []

    def test_excludes_players_with_fewer_than_3_games(self, tmp_path):
        db = _make_db(tmp_path)
        for i in range(2):
            _insert_row(db, game_id=f"g_{i}", player_name="Short Career",
                        game_date=date(2026, 1, i + 1))
        with patch("services.player_trends_service._EDGE_DB", db):
            results = get_slate_trends()
        names = [r["player_name"] for r in results]
        assert "Short Career" not in names

    def test_includes_players_with_3_plus_games(self, tmp_path):
        db = _make_db(tmp_path)
        for i in range(5):
            _insert_row(db, game_id=f"g_{i}", player_name="Active Player",
                        game_date=date(2026, 1, i + 1))
        with patch("services.player_trends_service._EDGE_DB", db):
            results = get_slate_trends()
        names = [r["player_name"] for r in results]
        assert "Active Player" in names

    def test_response_has_expected_keys(self, tmp_path):
        db = _make_db(tmp_path)
        for i in range(4):
            _insert_row(db, game_id=f"g_{i}", player_name="Steady Eddie",
                        game_date=date(2026, 1, i + 1), points=20)
        with patch("services.player_trends_service._EDGE_DB", db):
            results = get_slate_trends()

        assert len(results) >= 1
        row = results[0]
        for key in ("player_name", "team", "games_l10", "avg_min", "avg_pts", "avg_reb", "avg_ast"):
            assert key in row


# ---------------------------------------------------------------------------
# API route tests (FastAPI TestClient)
# ---------------------------------------------------------------------------

try:
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routers.player_trends import router as player_trends_router

    # Minimal app with auth bypassed
    _test_app = FastAPI()
    _test_app.include_router(player_trends_router)

    # Override auth dependency to return a stub user
    from services.auth import get_current_user

    def _mock_user():
        stub = MagicMock()
        stub.id = "test-user"
        stub.tier = "pro"
        return stub

    _test_app.dependency_overrides[get_current_user] = _mock_user
    _client = TestClient(_test_app)
    _TESTCLIENT_AVAILABLE = True
except Exception:
    _TESTCLIENT_AVAILABLE = False


@pytest.mark.skipif(not _TESTCLIENT_AVAILABLE, reason="TestClient not configured")
class TestPlayerTrendsRoutes:
    def test_search_requires_min_2_chars(self):
        res = _client.get("/api/player-trends/search?q=A")
        assert res.status_code == 422

    def test_search_returns_player_list(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="Devin Booker", team="PHX")
        with patch("services.player_trends_service._EDGE_DB", db):
            res = _client.get("/api/player-trends/search?q=Devin")
        assert res.status_code == 200
        body = res.json()
        assert "players" in body
        assert "count" in body

    def test_player_trends_404_when_no_data(self, tmp_path):
        db = _make_db(tmp_path)
        with patch("services.player_trends_service._EDGE_DB", db):
            res = _client.get("/api/player-trends/player?name=Nobody+Here")
        assert res.status_code == 404

    def test_player_trends_200_with_valid_data(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_row(db, game_id="g1", player_name="Joel Embiid", team="PHI",
                    points=32, rebounds=12, assists=4)
        with patch("services.player_trends_service._EDGE_DB", db):
            res = _client.get("/api/player-trends/player?name=Joel+Embiid")
        assert res.status_code == 200
        body = res.json()
        assert body["player_name"] == "Joel Embiid"
        assert "last5" in body
        assert "last10" in body
        assert "season" in body
        assert "recent_games" in body
        assert isinstance(body["recent_games"], list)

    def test_slate_endpoint_returns_player_list(self, tmp_path):
        db = _make_db(tmp_path)
        for i in range(4):
            _insert_row(db, game_id=f"g_{i}", player_name="Slate Star",
                        game_date=date(2026, 1, i + 1), points=25)
        with patch("services.player_trends_service._EDGE_DB", db):
            res = _client.get("/api/player-trends/slate")
        assert res.status_code == 200
        body = res.json()
        assert "players" in body
        assert "count" in body

    def test_player_endpoint_validates_games_range(self):
        res = _client.get("/api/player-trends/player?name=Test&games=100")
        assert res.status_code == 422  # above max of 25
