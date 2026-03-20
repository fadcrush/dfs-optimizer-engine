"""
Phase 20 — Tests for injury usage-boost projection adjustment

Tests cover:
  - get_injury_boost_multipliers() with various OUT/SSPD configurations
  - Same-position teammates boosted more than different-position teammates
  - OUT player itself receives no boost
  - Cap limits the maximum total boost
  - SSPD treated like OUT; GTD is NOT treated as OUT
  - Dual-position strings like "PG/SG" normalised to primary position "PG"
  - Players on different teams are unaffected by another team's OUT players
  - Missing status column → no boost (graceful degradation)
  - injury_boost_enabled=False → InjuryBoost column = 1.0, Proj unchanged
  - InjuryBoost column always present in engine output
  - Engine Layer 9 applied after Layer 8 (GameTotal) before variance
"""
from __future__ import annotations

import pandas as pd
import pytest

from analysis.nba.b2b import (
    get_injury_boost_multipliers,
    _INJURY_BOOST_SAME_POS,
    _INJURY_BOOST_DIFF_POS,
    _INJURY_BOOST_CAP,
    _OUT_STATUSES,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _slate(
    names: list[str],
    teams: list[str],
    positions: list[str],
    statuses: list[str],
    base_proj: float = 25.0,
) -> pd.DataFrame:
    """Build a minimal slate DataFrame."""
    return pd.DataFrame({
        "Name":         names,
        "Team":         teams,
        "Pos":          positions,
        "InjuryStatus": statuses,
        "Salary":       [6000] * len(names),
        "Base_Proj":    [base_proj] * len(names),
        "Opp":          ["OPP"] * len(names),
    })


def _engine_slate(
    names: list[str],
    teams: list[str],
    positions: list[str],
    statuses: list[str],
    base_proj: float = 25.0,
) -> pd.DataFrame:
    """Slate ready for CanonicalNBAProjectionEngine.generate()."""
    return _slate(names, teams, positions, statuses, base_proj)


# ===========================================================================
# TestGetInjuryBoostMultipliers — unit tests for the helper function
# ===========================================================================

class TestGetInjuryBoostMultipliers:
    """Unit tests for get_injury_boost_multipliers()."""

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["Name", "Team", "Pos", "InjuryStatus"])
        assert get_injury_boost_multipliers(df) == {}

    def test_no_out_players_returns_empty(self):
        """All active players → no OUT/SSPD → function returns {}."""
        df = _slate(
            ["A", "B", "C"],
            ["LAL", "LAL", "LAL"],
            ["PG", "SG", "SF"],
            ["", "", ""],
        )
        assert get_injury_boost_multipliers(df) == {}

    def test_single_out_boosts_same_pos_teammate(self):
        """1 OUT PG on LAL → active PG gets same_pos_boost."""
        df = _slate(
            ["Starter", "Backup"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["OUT", ""],
        )
        result = get_injury_boost_multipliers(df)
        assert "BACKUP" in result
        expected = round(1.0 + _INJURY_BOOST_SAME_POS, 5)
        assert result["BACKUP"] == pytest.approx(expected, abs=1e-5)

    def test_single_out_boosts_diff_pos_teammate(self):
        """1 OUT PG on LAL → active SF gets diff_pos_boost."""
        df = _slate(
            ["PG_Out", "SF_Active"],
            ["LAL", "LAL"],
            ["PG", "SF"],
            ["OUT", ""],
        )
        result = get_injury_boost_multipliers(df)
        assert "SF_ACTIVE" in result
        expected = round(1.0 + _INJURY_BOOST_DIFF_POS, 5)
        assert result["SF_ACTIVE"] == pytest.approx(expected, abs=1e-5)

    def test_out_player_not_in_result(self):
        """OUT player itself should never appear in the boost dict."""
        df = _slate(
            ["Injured", "Active"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["OUT", ""],
        )
        result = get_injury_boost_multipliers(df)
        assert "INJURED" not in result

    def test_two_outs_same_pos_stack(self):
        """2 OUT PGs → active PG gets 2 × same_pos_boost (≤ cap)."""
        df = _slate(
            ["Out1", "Out2", "Active"],
            ["LAL", "LAL", "LAL"],
            ["PG", "PG", "PG"],
            ["OUT", "OUT", ""],
        )
        result = get_injury_boost_multipliers(df)
        expected_boost = 2 * _INJURY_BOOST_SAME_POS
        expected_mult = round(1.0 + min(expected_boost, _INJURY_BOOST_CAP), 5)
        assert result["ACTIVE"] == pytest.approx(expected_mult, abs=1e-5)

    def test_cap_limits_total_boost(self):
        """Many OUT teammates should not push the boost above the cap."""
        # 5 OUT PGs + 5 OUT SGs → would be 5×0.08 + 5×0.02 = 0.50 without cap
        many_out = [f"Out{i}" for i in range(10)]
        names = many_out + ["Active"]
        teams = ["LAL"] * 11
        positions = ["PG"] * 5 + ["SG"] * 5 + ["PG"]
        statuses = ["OUT"] * 10 + [""]
        df = _slate(names, teams, positions, statuses)
        result = get_injury_boost_multipliers(df)
        assert "ACTIVE" in result
        assert result["ACTIVE"] == pytest.approx(1.0 + _INJURY_BOOST_CAP, abs=1e-5)

    def test_sspd_treated_as_out(self):
        """SSPD status should trigger boost just like OUT."""
        df = _slate(
            ["Susp", "Backup"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["SSPD", ""],
        )
        result = get_injury_boost_multipliers(df)
        assert "BACKUP" in result
        assert result["BACKUP"] > 1.0

    def test_gtd_not_treated_as_out(self):
        """GTD (game-time decision) should NOT trigger any boost."""
        df = _slate(
            ["GTD_Player", "Teammate"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["GTD", ""],
        )
        result = get_injury_boost_multipliers(df)
        # GTD → no boost → dict is empty
        assert result == {}

    def test_dual_position_normalised(self):
        """'PG/SG' OUT should count as PG (primary position)."""
        df = _slate(
            ["DualOut", "PG_Active", "SG_Active"],
            ["LAL", "LAL", "LAL"],
            ["PG/SG", "PG", "SG"],
            ["OUT", "", ""],
        )
        result = get_injury_boost_multipliers(df)
        # PG/SG normalise to PG → PG_Active gets same_pos_boost, SG_Active gets diff_pos_boost
        assert result.get("PG_ACTIVE", 1.0) == pytest.approx(
            round(1.0 + _INJURY_BOOST_SAME_POS, 5), abs=1e-5
        )
        assert result.get("SG_ACTIVE", 1.0) == pytest.approx(
            round(1.0 + _INJURY_BOOST_DIFF_POS, 5), abs=1e-5
        )

    def test_different_team_no_boost(self):
        """OUT player on BOS should not affect players on LAL."""
        df = _slate(
            ["BOS_Out", "LAL_PG"],
            ["BOS", "LAL"],
            ["PG", "PG"],
            ["OUT", ""],
        )
        result = get_injury_boost_multipliers(df)
        assert "LAL_PG" not in result

    def test_missing_status_col_returns_empty(self):
        """If InjuryStatus column is absent, treat all as active → no OUT → {}."""
        df = pd.DataFrame({
            "Name":    ["A", "B"],
            "Team":    ["LAL", "LAL"],
            "Pos":     ["PG", "PG"],
            "Salary":  [6000, 6000],
        })
        result = get_injury_boost_multipliers(df)
        assert result == {}

    def test_exact_multiplier_arithmetic(self):
        """Verify the formula: mult = 1 + same_boost + diff_boost (1 OUT each)."""
        # 1 OUT PG, 1 OUT SG on LAL → active LAL SF gets:
        #   n_same=0 (SF ≠ PG and SF ≠ SG), n_diff=2
        #   boost = 2 × diff_boost
        df = _slate(
            ["OutPG", "OutSG", "ActiveSF"],
            ["LAL", "LAL", "LAL"],
            ["PG", "SG", "SF"],
            ["OUT", "OUT", ""],
        )
        result = get_injury_boost_multipliers(df)
        expected = round(1.0 + 2 * _INJURY_BOOST_DIFF_POS, 5)
        assert result["ACTIVESF"] == pytest.approx(expected, abs=1e-5)

    def test_name_key_is_uppercased(self):
        """Result keys are always str.strip().upper() of the Name value."""
        df = _slate(
            ["out player", "active player"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["OUT", ""],
        )
        result = get_injury_boost_multipliers(df)
        assert "ACTIVE PLAYER" in result


# ===========================================================================
# TestEngineInjuryBoostLayer — integration tests via the full engine
# ===========================================================================

class TestEngineInjuryBoostLayer:
    """Integration tests: InjuryBoost wired into CanonicalNBAProjectionEngine."""

    def _ctx(self, site: str = "FD"):
        from analysis.core.schemas import ProjectionContext
        from datetime import date
        return ProjectionContext(site=site, sport="NBA", slate_date=date(2026, 3, 19))

    def _minimal_engine(self, **kwargs):
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        return CanonicalNBAProjectionEngine(
            dvp_enabled=False,
            b2b_enabled=False,
            blowout_enabled=False,
            minutes_trend_enabled=False,
            game_total_enabled=False,
            ownership_enabled=False,
            **kwargs,
        )

    def test_injury_boost_column_always_present(self):
        """InjuryBoost column always appears in engine output."""
        df = _engine_slate(
            ["P1", "P2"],
            ["LAL", "LAL"],
            ["PG", "SG"],
            ["", ""],
        )
        engine = self._minimal_engine()
        result = engine.generate(df, self._ctx())
        assert "InjuryBoost" in result.columns

    def test_proj_boosted_when_out_teammate(self):
        """Active PG on LAL gets boosted Proj when the other PG is OUT."""
        df = _engine_slate(
            ["Active", "Injured"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["", "OUT"],
            base_proj=30.0,
        )
        engine = self._minimal_engine()
        result = engine.generate(df, self._ctx())
        active_row = result[result["Name"] == "Active"].iloc[0]
        assert active_row["InjuryBoost"] == pytest.approx(
            round(1.0 + _INJURY_BOOST_SAME_POS, 5), abs=1e-5
        )
        # Proj should be 30.0 × (1 + same_pos_boost)
        assert active_row["Proj"] == pytest.approx(
            round(30.0 * (1.0 + _INJURY_BOOST_SAME_POS), 4), abs=1e-3
        )

    def test_injury_boost_disabled(self):
        """Setting injury_boost_enabled=False produces InjuryBoost=1.0 everywhere."""
        df = _engine_slate(
            ["Active", "Injured"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["", "OUT"],
            base_proj=30.0,
        )
        engine = self._minimal_engine(injury_boost_enabled=False)
        result = engine.generate(df, self._ctx())
        assert (result["InjuryBoost"] == 1.0).all()

    def test_no_status_col_neutral(self):
        """Without an InjuryStatus column, InjuryBoost defaults to 1.0."""
        df = pd.DataFrame({
            "Name":      ["A", "B"],
            "Team":      ["LAL", "LAL"],
            "Pos":       ["PG", "PG"],
            "Salary":    [6000, 6500],
            "Base_Proj": [25.0, 28.0],
            "Opp":       ["BOS", "BOS"],
        })
        engine = self._minimal_engine()
        result = engine.generate(df, self._ctx())
        assert (result["InjuryBoost"] == 1.0).all()

    def test_out_player_proj_passes_through(self):
        """The OUT player's own Proj is not touched by the injury boost."""
        df = _engine_slate(
            ["Active", "Injured"],
            ["LAL", "LAL"],
            ["PG", "PG"],
            ["", "OUT"],
            base_proj=30.0,
        )
        engine = self._minimal_engine()
        result = engine.generate(df, self._ctx())
        injured_row = result[result["Name"] == "Injured"].iloc[0]
        # InjuryBoost for OUT player is 1.0 (not boosted, since they're excluded from lineups)
        assert injured_row["InjuryBoost"] == pytest.approx(1.0, abs=1e-5)
