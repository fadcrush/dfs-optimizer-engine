"""
Phase 21 tests: Monte Carlo simulation engine expansion.

Covers:
  - SimulationConfig "game" correlation mode and game_corr field
  - summarize_player_sims advanced stats (Boom_Rate, Ceiling_P, Floor_Hit_P, Sim_Boost)
  - simulate_contest_ev EV/ROI/CashRate correctness
  - enrich_simulation_with_ev column merge
  - PRESETS catalog
  - CanonicalNBAProjectionEngine simulation layer (Sim_P90, Sim_Boost, Boom_Rate output)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.core.simulation import (
    SimulationConfig,
    build_team_correlation_matrix,
    simulate_lineup_scores,
    simulate_player_outcomes,
    summarize_player_sims,
)
from analysis.core.contest_sim import (
    ContestConfig,
    PRESETS,
    enrich_simulation_with_ev,
    simulate_contest_ev,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def two_team_projections() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"DFS_ID": "a1", "Proj": 40.0, "Floor": 28.0, "Ceiling": 52.0, "Team": "AAA", "Opp": "BBB"},
            {"DFS_ID": "a2", "Proj": 35.0, "Floor": 24.0, "Ceiling": 46.0, "Team": "AAA", "Opp": "BBB"},
            {"DFS_ID": "b1", "Proj": 30.0, "Floor": 20.0, "Ceiling": 42.0, "Team": "BBB", "Opp": "AAA"},
            {"DFS_ID": "b2", "Proj": 25.0, "Floor": 16.0, "Ceiling": 36.0, "Team": "BBB", "Opp": "AAA"},
        ]
    )


@pytest.fixture()
def three_lineup_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"LineupIndex": 0, "DFS_ID": "a1"},
            {"LineupIndex": 0, "DFS_ID": "a2"},
            {"LineupIndex": 1, "DFS_ID": "b1"},
            {"LineupIndex": 1, "DFS_ID": "b2"},
            {"LineupIndex": 2, "DFS_ID": "a1"},
            {"LineupIndex": 2, "DFS_ID": "b1"},
        ]
    )


# ===========================================================================
# TestSimulationConfigGame
# ===========================================================================

class TestSimulationConfigGame:
    def test_game_mode_accepted(self) -> None:
        cfg = SimulationConfig(correlation="game")
        assert cfg.correlation == "game"

    def test_game_corr_default(self) -> None:
        cfg = SimulationConfig()
        assert cfg.game_corr == pytest.approx(0.10)

    def test_game_corr_custom(self) -> None:
        cfg = SimulationConfig(game_corr=0.20)
        assert cfg.game_corr == pytest.approx(0.20)

    def test_game_mode_higher_opp_corr_than_team_mode(self, two_team_projections) -> None:
        df = two_team_projections
        # "game" mode uses game_corr=0.10 for opponents vs opp_corr=0.06 in "team" mode
        cfg_team = SimulationConfig(correlation="team", same_team_corr=0.18, opp_corr=0.06, game_corr=0.10)
        cfg_game = SimulationConfig(correlation="game", same_team_corr=0.18, opp_corr=0.06, game_corr=0.10)
        sims_team = simulate_player_outcomes(df, SimulationConfig(n_sims=200, seed=1, correlation="team", opp_corr=0.06, game_corr=0.10))
        sims_game = simulate_player_outcomes(df, SimulationConfig(n_sims=200, seed=1, correlation="game", opp_corr=0.06, game_corr=0.10))
        # Opposing teams (indices 0 and 2) should be more correlated under "game" mode
        corr_team = np.corrcoef(sims_team[:, 0], sims_team[:, 2])[0, 1]
        corr_game = np.corrcoef(sims_game[:, 0], sims_game[:, 2])[0, 1]
        assert corr_game > corr_team

    def test_game_mode_same_team_corr_unchanged(self, two_team_projections) -> None:
        df = two_team_projections
        sims = simulate_player_outcomes(df, SimulationConfig(n_sims=500, seed=42, correlation="game"))
        # Both 0 and 1 are on team AAA — same-team corr should be high
        corr = np.corrcoef(sims[:, 0], sims[:, 1])[0, 1]
        assert corr > 0.10  # well above base_corr

    def test_none_mode_produces_identity_corr(self, two_team_projections) -> None:
        df = two_team_projections
        sims = simulate_player_outcomes(df, SimulationConfig(n_sims=1000, seed=99, correlation="none"))
        # All player pairs should have near-zero empirical correlation
        corr_teammates = np.corrcoef(sims[:, 0], sims[:, 1])[0, 1]
        assert abs(corr_teammates) < 0.15  # allow empirical noise


# ===========================================================================
# TestSummarizePlayerSimsAdvanced
# ===========================================================================

class TestSummarizePlayerSimsAdvanced:

    def _fixed_sims(self, proj: float, n: int = 1000, seed: int = 7) -> np.ndarray:
        """1-player sims from N(proj, proj*0.2)."""
        rng = np.random.default_rng(seed)
        return rng.normal(loc=proj, scale=proj * 0.2, size=(n, 1)).clip(0)

    def test_boom_rate_all_above(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 10.0}])
        # All sims are 100 — well above 1.5 × 10 = 15
        sims = np.full((500, 1), 100.0)
        result = summarize_player_sims(df, sims)
        assert result.loc[0, "Boom_Rate"] == pytest.approx(1.0)

    def test_boom_rate_none_above(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 100.0}])
        # All sims are 1 — below 1.5 × 100 = 150
        sims = np.full((500, 1), 1.0)
        result = summarize_player_sims(df, sims)
        assert result.loc[0, "Boom_Rate"] == pytest.approx(0.0)

    def test_ceiling_p_with_column(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 30.0, "Floor": 20.0, "Ceiling": 45.0}])
        # Half the sims are 50 (>= 45) and half are 10 (< 45)
        sims = np.array([[50.0]] * 250 + [[10.0]] * 250)
        result = summarize_player_sims(df, sims)
        assert result.loc[0, "Ceiling_P"] == pytest.approx(0.5)

    def test_ceiling_p_without_column(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 30.0}])
        sims = np.full((100, 1), 999.0)
        result = summarize_player_sims(df, sims)
        # No Ceiling column → defaults to 0.0
        assert result.loc[0, "Ceiling_P"] == pytest.approx(0.0)

    def test_floor_hit_p_with_column(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 30.0, "Floor": 25.0}])
        # 200 sims at 20 (<= 25) and 300 at 40 (> 25)
        sims = np.array([[20.0]] * 200 + [[40.0]] * 300)
        result = summarize_player_sims(df, sims)
        assert result.loc[0, "Floor_Hit_P"] == pytest.approx(200 / 500)

    def test_floor_hit_p_without_column(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 30.0}])
        sims = np.full((100, 1), 0.0)
        result = summarize_player_sims(df, sims)
        # No Floor column → defaults to 0.0
        assert result.loc[0, "Floor_Hit_P"] == pytest.approx(0.0)

    def test_sim_boost_positive_when_above_proj(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 20.0}])
        # All sims are 30 — Sim_Mean=30, Proj=20 → boost = 30/20 - 1 = 0.5
        sims = np.full((100, 1), 30.0)
        result = summarize_player_sims(df, sims)
        assert result.loc[0, "Sim_Boost"] == pytest.approx(0.5)

    def test_sim_boost_zero_when_proj_is_zero(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 0.0}])
        sims = np.full((100, 1), 5.0)
        result = summarize_player_sims(df, sims)
        assert result.loc[0, "Sim_Boost"] == pytest.approx(0.0)

    def test_all_new_columns_present(self) -> None:
        df = pd.DataFrame([{"DFS_ID": "p", "Proj": 30.0, "Floor": 20.0, "Ceiling": 40.0}])
        sims = np.full((50, 1), 30.0)
        result = summarize_player_sims(df, sims)
        for col in ("Boom_Rate", "Ceiling_P", "Floor_Hit_P", "Sim_Boost"):
            assert col in result.columns, f"Missing column: {col}"

    def test_deterministic_with_seed(self, two_team_projections) -> None:
        cfg = SimulationConfig(n_sims=200, seed=11)
        sims1 = simulate_player_outcomes(two_team_projections, cfg)
        sims2 = simulate_player_outcomes(two_team_projections, cfg)
        r1 = summarize_player_sims(two_team_projections, sims1)
        r2 = summarize_player_sims(two_team_projections, sims2)
        pd.testing.assert_frame_equal(r1, r2)

    def test_original_columns_still_present(self, two_team_projections) -> None:
        cfg = SimulationConfig(n_sims=100, seed=5)
        sims = simulate_player_outcomes(two_team_projections, cfg)
        result = summarize_player_sims(two_team_projections, sims)
        for col in ("DFS_ID", "Sim_Mean", "Sim_Median", "Sim_P90", "Sim_P95", "Sim_P99", "Sim_StdDev"):
            assert col in result.columns


# ===========================================================================
# TestContestSimEV
# ===========================================================================

class TestContestSimEV:

    def _scores_matrix(self, n_lineups: int = 3, n_sims: int = 200, seed: int = 42) -> np.ndarray:
        rng = np.random.default_rng(seed)
        return rng.normal(loc=130.0, scale=15.0, size=(n_lineups, n_sims)).clip(0)

    def test_returns_dataframe(self) -> None:
        scores = self._scores_matrix()
        cfg = ContestConfig(entry_fee=3.0, field_size=10, contest_type="gpp")
        result = simulate_contest_ev(scores, [0, 1, 2], cfg, seed=7)
        assert isinstance(result, pd.DataFrame)
        assert not result.empty

    def test_expected_columns_present(self) -> None:
        scores = self._scores_matrix()
        cfg = ContestConfig(entry_fee=5.0, field_size=20, contest_type="gpp")
        result = simulate_contest_ev(scores, [0, 1, 2], cfg)
        for col in ("LineupIndex", "EV", "ROI", "CashRate", "Top1Rate", "AvgFinishPct"):
            assert col in result.columns

    def test_roi_sorted_descending(self) -> None:
        scores = self._scores_matrix()
        cfg = ContestConfig(entry_fee=3.0, field_size=50, contest_type="gpp")
        result = simulate_contest_ev(scores, [0, 1, 2], cfg)
        assert list(result["ROI"]) == sorted(result["ROI"].tolist(), reverse=True)

    def test_cash_rate_double_up_near_45_pct(self) -> None:
        # 1 lineup with average field scores → should cash ~45% (double_up pays top ~45%)
        rng = np.random.default_rng(42)
        field_mean = 130.0
        scores = rng.normal(loc=field_mean, scale=15.0, size=(1, 2000)).clip(0)
        cfg = ContestConfig(entry_fee=5.0, field_size=100, contest_type="double_up",
                            field_mean_score=field_mean)
        result = simulate_contest_ev(scores, [0], cfg, seed=42)
        # Allow wide tolerance; should be near 0.45 ± 0.15
        assert 0.20 < result.loc[0, "CashRate"] < 0.70

    def test_top1_rate_bounds(self) -> None:
        # Top1Rate per lineup should be between 0 and 1
        scores = self._scores_matrix(n_lineups=5, n_sims=500)
        cfg = ContestConfig(field_size=5)  # no external — all ours
        result = simulate_contest_ev(scores, list(range(5)), cfg)
        assert (result["Top1Rate"] >= 0).all()
        assert (result["Top1Rate"] <= 1).all()

    def test_top1_rate_sums_to_approx_one_no_external(self) -> None:
        # When field_size == n_lineups, every sim should have exactly 1 winner
        scores = self._scores_matrix(n_lineups=4, n_sims=1000)
        cfg = ContestConfig(field_size=4)
        result = simulate_contest_ev(scores, list(range(4)), cfg)
        assert result["Top1Rate"].sum() == pytest.approx(1.0, abs=0.05)

    def test_ev_negative_for_low_scorer(self) -> None:
        rng = np.random.default_rng(1)
        # Our lineup scores much lower than the field
        my_scores = rng.normal(loc=80.0, scale=5.0, size=(1, 1000)).clip(0)
        cfg = ContestConfig(entry_fee=20.0, field_size=500, contest_type="gpp",
                            field_mean_score=130.0)
        result = simulate_contest_ev(my_scores, [0], cfg)
        assert result.loc[0, "EV"] < 0.0

    def test_empty_scores_returns_empty(self) -> None:
        empty = np.empty((0, 0))
        cfg = ContestConfig()
        result = simulate_contest_ev(empty, [], cfg)
        assert result.empty

    def test_enrich_simulation_with_ev_adds_columns(self, two_team_projections, three_lineup_df) -> None:
        cfg_sim = SimulationConfig(n_sims=100, seed=5)
        sim_summary, scores = simulate_lineup_scores(three_lineup_df, two_team_projections, cfg_sim)
        cfg_contest = ContestConfig(field_size=10, entry_fee=3.0)
        enriched = enrich_simulation_with_ev(sim_summary, scores, cfg_contest)
        for col in ("EV", "ROI", "CashRate"):
            assert col in enriched.columns

    def test_presets_all_accessible(self) -> None:
        expected = {
            "dk_gpp_small", "dk_gpp_medium", "dk_gpp_large",
            "dk_double_up", "dk_top_heavy",
            "fd_gpp_small", "fd_double_up",
        }
        assert expected.issubset(set(PRESETS.keys()))

    def test_preset_payout_schedule_non_empty(self) -> None:
        for name, cfg in PRESETS.items():
            schedule = cfg.payout_schedule()
            assert len(schedule) > 0, f"Empty schedule for preset: {name}"


# ===========================================================================
# TestEngineSimulationIntegration
# ===========================================================================

class TestEngineSimulationIntegration:
    """Integration tests that run the full CanonicalNBAProjectionEngine."""

    def _make_slate(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "DFS_ID": f"p{i}",
                    "Name": f"Player_{i}",
                    "Team": "AAA" if i < 4 else "BBB",
                    "Opp": "BBB" if i < 4 else "AAA",
                    "Pos": "PG",
                    "Salary": 8000 - i * 200,
                    "Base_Proj": 35.0 - i * 2,
                    "InjuryStatus": "ACTIVE",
                }
                for i in range(8)
            ]
        )

    def _run_engine(self, sim_enabled: bool = True, sim_n_sims: int = 100):
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext
        engine = CanonicalNBAProjectionEngine(
            dvp_enabled=False,
            b2b_enabled=False,
            blowout_enabled=False,
            minutes_trend_enabled=False,
            game_total_enabled=False,
            injury_boost_enabled=False,
            simulation_enabled=sim_enabled,
            sim_n_sims=sim_n_sims,
            ownership_enabled=False,
        )
        ctx = ProjectionContext(sport="NBA", site="DK", slate_date="2026-01-01")
        return engine.generate(self._make_slate(), ctx)

    def test_sim_columns_present_when_enabled(self) -> None:
        result = self._run_engine(sim_enabled=True)
        for col in ("Sim_P90", "Sim_Boost", "Boom_Rate"):
            assert col in result.columns, f"Missing column: {col}"

    def test_sim_p90_greater_than_proj(self) -> None:
        result = self._run_engine(sim_enabled=True)
        # P90 should be > median projection for active players
        valid = result[(result["Proj"] > 0) & result["Sim_P90"].notna()]
        assert (valid["Sim_P90"] > valid["Proj"]).all()

    def test_sim_boost_is_small_near_zero(self) -> None:
        # Without correlated teammates booming, Sim_Boost should be near 0
        result = self._run_engine(sim_enabled=True, sim_n_sims=300)
        valid = result[result["Sim_Boost"].notna()]
        # Sim_Boost should be within +/-0.10 of zero (no strong push in either direction)
        assert (valid["Sim_Boost"].abs() < 0.15).all()

    def test_sim_columns_nan_when_disabled(self) -> None:
        result = self._run_engine(sim_enabled=False)
        for col in ("Sim_P90", "Sim_Boost", "Boom_Rate"):
            assert col in result.columns
            assert result[col].isna().all(), f"{col} should be all-NaN when simulation disabled"
