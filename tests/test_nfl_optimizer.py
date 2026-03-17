"""Tests for the NFL random-sampling optimizer (analysis/nfl/optimizer.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.nfl.optimizer import NFLOptimizer


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: minimal DK NFL Classic player pool
# ─────────────────────────────────────────────────────────────────────────────

def _make_nfl_player(name, pos, salary, proj, team="KC", opp="LV", dfs_id=None):
    return {
        "Name": name,
        "Position": pos,
        "Salary": salary,
        "Projection": proj,
        "Team": team,
        "Opp": opp,
        "DFS_ID": dfs_id or name.replace(" ", "_"),
    }


NFL_POOL = [
    # QB
    _make_nfl_player("QB One",  "QB", 7200, 28.0, "KC", "LV"),
    _make_nfl_player("QB Two",  "QB", 6500, 24.0, "LV", "KC"),
    # RB
    _make_nfl_player("RB One",  "RB", 6800, 22.0, "KC", "LV"),
    _make_nfl_player("RB Two",  "RB", 5900, 18.0, "KC", "LV"),
    _make_nfl_player("RB Three","RB", 5400, 15.0, "LV", "KC"),
    _make_nfl_player("RB Four", "RB", 4800, 13.0, "LV", "KC"),
    # WR
    _make_nfl_player("WR One",  "WR", 7800, 30.0, "KC", "LV"),
    _make_nfl_player("WR Two",  "WR", 7000, 26.0, "KC", "LV"),
    _make_nfl_player("WR Three","WR", 6200, 22.0, "LV", "KC"),
    _make_nfl_player("WR Four", "WR", 5500, 18.0, "LV", "KC"),
    _make_nfl_player("WR Five", "WR", 4800, 14.0, "KC", "LV"),
    # TE
    _make_nfl_player("TE One",  "TE", 6000, 20.0, "KC", "LV"),
    _make_nfl_player("TE Two",  "TE", 5200, 16.0, "LV", "KC"),
    # DST
    _make_nfl_player("KC DST",  "DST",3600, 10.0, "KC", "LV", dfs_id="KC_DST"),
    _make_nfl_player("LV DST",  "DST",3200,  8.0, "LV", "KC", dfs_id="LV_DST"),
    # Extra FLEX eligibles
    _make_nfl_player("RB Five", "RB", 4200, 11.0, "DAL", "NYG"),
    _make_nfl_player("WR Six",  "WR", 4500, 12.0, "DAL", "NYG"),
]


@pytest.fixture()
def nfl_df():
    return pd.DataFrame(NFL_POOL)


@pytest.fixture()
def classic_optimizer():
    return NFLOptimizer(site="DK", contest_type="Classic")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNFLOptimizerBasics:
    def test_generate_returns_list(self, classic_optimizer, nfl_df):
        lineups = classic_optimizer.generate(nfl_df, n_lineups=3)
        assert isinstance(lineups, list)
        assert len(lineups) >= 1

    def test_generates_requested_count(self, classic_optimizer, nfl_df):
        lineups = classic_optimizer.generate(nfl_df, n_lineups=5)
        assert len(lineups) == 5

    def test_each_lineup_has_slots(self, classic_optimizer, nfl_df):
        lineups = classic_optimizer.generate(nfl_df, n_lineups=2)
        for lu in lineups:
            assert hasattr(lu, "slots"), "NFLLineup missing .slots attribute"
            assert len(lu.slots) > 0

    def test_salary_within_cap(self, classic_optimizer, nfl_df):
        """DK Classic salary cap is 50 000."""
        lineups = classic_optimizer.generate(nfl_df, n_lineups=5)
        for lu in lineups:
            assert lu.total_salary <= 50_000 + 1, (
                f"Lineup salary {lu.total_salary} exceeds cap"
            )

    def test_unique_lineups(self, classic_optimizer, nfl_df):
        lineups = classic_optimizer.generate(nfl_df, n_lineups=3)
        keys = [frozenset(s.player_id for s in lu.slots) for lu in lineups]
        assert len(keys) == len(set(keys)), "Duplicate NFL lineups generated"


class TestNFLOptimizerLockFade:
    def test_locked_player_in_every_lineup(self, classic_optimizer, nfl_df):
        lock_name = "QB One"
        lineups = classic_optimizer.generate(nfl_df, n_lineups=3, locks=[lock_name])
        for lu in lineups:
            names = {s.name.split(" (CPT)")[0] for s in lu.slots}
            assert lock_name in names, f"Locked '{lock_name}' missing"

    def test_faded_player_absent(self, classic_optimizer, nfl_df):
        fade_name = "QB Two"
        lineups = classic_optimizer.generate(nfl_df, n_lineups=3, fades=[fade_name])
        for lu in lineups:
            names = {s.name.split(" (CPT)")[0] for s in lu.slots}
            assert fade_name not in names, f"Faded '{fade_name}' appeared"


class TestNFLOptimizerTeamCap:
    def test_max_from_team_respected(self, classic_optimizer, nfl_df):
        lineups = classic_optimizer.generate(
            nfl_df, n_lineups=5, max_from_team=3
        )
        for lu in lineups:
            from collections import Counter
            team_counts = Counter(s.team for s in lu.slots if s.team)
            for team, cnt in team_counts.items():
                assert cnt <= 3, f"Team {team} has {cnt} players (max=3)"
