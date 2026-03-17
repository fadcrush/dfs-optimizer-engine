"""Tests for the NBA LP optimizer (analysis/nba/optimizer.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.nba.optimizer import (
    StackRule,
    optimize_portfolio,
    SITE_RULES,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _has_duplicate_players(lineup_df: pd.DataFrame) -> bool:
    """Return True if any lineup-index has a player appearing twice."""
    id_cols = [c for c in lineup_df.columns if c not in ("LineupIndex", "TotalSalary", "Proj")]
    for _, grp in lineup_df.groupby("LineupIndex"):
        players = [grp[c].values[0] for c in id_cols if not grp[c].isna().values[0]]
        if len(players) != len(set(players)):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOptimizePortfolioFD:
    """Basic correctness for FanDuel site."""

    def test_generates_requested_lineup_count(self, fd_players_df):
        result = optimize_portfolio(fd_players_df, site="FD", n_lineups=3, max_exposure=1.0)
        n = result["LineupIndex"].nunique()
        assert n == 3, f"Expected 3 lineups, got {n}"

    def test_unique_lineups(self, fd_players_df):
        result = optimize_portfolio(fd_players_df, site="FD", n_lineups=3, max_exposure=1.0)
        # Each lineup is identified by its set of player DFS_IDs
        lineup_sets = []
        for _, grp in result.groupby("LineupIndex"):
            s = frozenset(grp["DFS_ID"].tolist())
            lineup_sets.append(s)
        assert len(lineup_sets) == len(set(lineup_sets)), "Duplicate lineups generated"

    def test_salary_cap_respected(self, fd_players_df):
        cap = SITE_RULES["FD"]["salary_cap"]
        result = optimize_portfolio(fd_players_df, site="FD", n_lineups=2)
        salary_col = "TotalSalary"
        if salary_col in result.columns:
            for _, grp in result.groupby("LineupIndex"):
                assert grp[salary_col].values[0] <= cap + 1, "Salary cap exceeded"

    def test_returns_dataframe(self, fd_players_df):
        result = optimize_portfolio(fd_players_df, site="FD", n_lineups=1)
        assert isinstance(result, pd.DataFrame)
        assert not result.empty

    def test_roster_size(self, fd_players_df):
        """Each FD lineup should have exactly 9 players."""
        result = optimize_portfolio(fd_players_df, site="FD", n_lineups=1)
        id_cols = [c for c in result.columns if c not in ("LineupIndex", "TotalSalary", "Proj")]
        for _, grp in result.groupby("LineupIndex"):
            filled = sum(1 for c in id_cols if not pd.isna(grp[c].values[0]))
            assert filled == 9, f"Expected 9 players, got {filled}"


class TestOptimizePortfolioLockFade:
    """Lock and fade constraint validation."""

    def test_locked_player_appears_in_every_lineup(self, fd_players_df):
        lock_name = "PG One"
        result = optimize_portfolio(
            fd_players_df, site="FD", n_lineups=3, locks=[lock_name]
        )
        id_cols = [c for c in result.columns if c not in ("LineupIndex", "TotalSalary", "Proj")]
        for _, grp in result.groupby("LineupIndex"):
            players_in_lineup = {grp[c].values[0] for c in id_cols}
            assert lock_name in players_in_lineup, (
                f"Locked player '{lock_name}' missing from lineup"
            )

    def test_faded_player_absent_from_all_lineups(self, fd_players_df):
        fade_name = "PG Two"
        result = optimize_portfolio(
            fd_players_df, site="FD", n_lineups=3, fades=[fade_name]
        )
        id_cols = [c for c in result.columns if c not in ("LineupIndex", "TotalSalary", "Proj")]
        for _, grp in result.groupby("LineupIndex"):
            players_in_lineup = {grp[c].values[0] for c in id_cols}
            assert fade_name not in players_in_lineup, (
                f"Faded player '{fade_name}' appeared in lineup"
            )


class TestOptimizePortfolioStacking:
    """Stacking rules."""

    def test_max_from_same_team_respected(self, fd_players_df):
        rule = StackRule(max_from_same_team=2, max_from_same_game=9)
        result = optimize_portfolio(
            fd_players_df, site="FD", n_lineups=2, stack_rules=rule, max_exposure=1.0
        )
        # Build a player → team lookup
        team_lookup = dict(zip(fd_players_df["Name"], fd_players_df["Team"]))
        for _, grp in result.groupby("LineupIndex"):
            from collections import Counter
            player_names = grp["Name"].tolist()
            team_counts = Counter(team_lookup.get(p, "?") for p in player_names)
            for team, cnt in team_counts.items():
                assert cnt <= 2, f"Team {team} appears {cnt} times (max=2)"


class TestOptimizePortfolioEdgeCases:
    """Edge cases and fallbacks."""

    def test_single_lineup_by_default(self, fd_players_df):
        result = optimize_portfolio(fd_players_df, site="FD")
        assert result["LineupIndex"].nunique() >= 1

    def test_zero_lineups_returns_empty(self, fd_players_df):
        result = optimize_portfolio(fd_players_df, site="FD", n_lineups=0)
        assert result.empty or len(result) == 0

    def test_unknown_site_raises(self, fd_players_df):
        with pytest.raises((ValueError, KeyError)):
            optimize_portfolio(fd_players_df, site="XX", n_lineups=1)
