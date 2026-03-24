"""
Tests for analysis.nba.ownership_weighted — weighted component ownership model.
"""
from __future__ import annotations

import pandas as pd
import pytest

from analysis.nba.ownership_weighted import (
    OWNERSHIP_CAP,
    estimate_ownership_weighted,
)


def _slate(
    projs: list[float],
    salaries: list[float] | None = None,
    injury_statuses: list[str] | None = None,
    site: str = "DK",
) -> pd.DataFrame:
    n = len(projs)
    salaries = salaries or [6000] * n
    injury_statuses = injury_statuses or [""] * n
    return pd.DataFrame(
        {
            "Name": [f"Player{i}" for i in range(n)],
            "Proj": projs,
            "Salary": salaries,
            "InjuryStatus": injury_statuses,
        }
    )


class TestOwnershipWeightedBasics:
    def test_own_est_column_present(self) -> None:
        df = _slate([40.0, 30.0, 20.0])
        result = estimate_ownership_weighted(df)
        assert "Own_Est" in result.columns

    def test_own_source_is_weighted(self) -> None:
        df = _slate([40.0, 30.0, 20.0])
        result = estimate_ownership_weighted(df)
        assert (result["own_source"] == "weighted").all()

    def test_ownership_bucket_present(self) -> None:
        df = _slate([40.0, 30.0, 20.0])
        result = estimate_ownership_weighted(df)
        assert "ownership_bucket" in result.columns
        valid_buckets = {"chalk", "high", "medium", "low"}
        assert set(result["ownership_bucket"].unique()).issubset(valid_buckets)

    def test_original_df_not_mutated(self) -> None:
        df = _slate([50.0, 30.0])
        original_cols = set(df.columns)
        estimate_ownership_weighted(df)
        assert set(df.columns) == original_cols

    def test_empty_slate_returns_empty(self) -> None:
        df = pd.DataFrame(columns=["Name", "Proj", "Salary", "InjuryStatus"])
        result = estimate_ownership_weighted(df)
        assert len(result) == 0
        assert "Own_Est" in result.columns

    def test_single_player_no_crash(self) -> None:
        df = _slate([35.0])
        result = estimate_ownership_weighted(df)
        assert len(result) == 1
        assert result.loc[0, "Own_Est"] >= 0.0


class TestOwnershipWeightedRanking:
    def test_higher_proj_gets_higher_ownership(self) -> None:
        # Use 10 players so budget normalization doesn't cap everybody at 40%
        projs   = [60.0, 50.0, 40.0, 30.0, 25.0, 20.0, 15.0, 12.0, 8.0, 5.0]
        salaries = [9500, 8500, 7500, 7000, 6500, 6000, 5500, 5000, 4500, 4000]
        df = _slate(projs, salaries)
        result = estimate_ownership_weighted(df)
        # Stud (idx 0) should be more owned than bench player (idx 9)
        assert result.loc[0, "Own_Est"] > result.loc[9, "Own_Est"]

    def test_out_player_gets_zero_ownership(self) -> None:
        df = _slate(
            projs=[40.0, 40.0],
            injury_statuses=["O", ""],
        )
        result = estimate_ownership_weighted(df)
        assert result.loc[0, "Own_Est"] == 0.0

    def test_questionable_player_below_healthy(self) -> None:
        # Identical proj/salary — status is the only difference; use enough
        # players so neither hits the 40% cap before comparison.
        n_others = 10
        projs   = [40.0, 40.0] + [20.0] * n_others
        salaries = [8000, 8000] + [5000] * n_others
        injury_statuses = ["Q", ""] + [""] * n_others
        df = _slate(projs, salaries, injury_statuses)
        result = estimate_ownership_weighted(df)
        assert result.loc[0, "Own_Est"] < result.loc[1, "Own_Est"]

    def test_zero_proj_player_gets_zero_ownership(self) -> None:
        df = _slate([0.0, 40.0])
        result = estimate_ownership_weighted(df)
        assert result.loc[0, "Own_Est"] == pytest.approx(0.0, abs=0.01)


class TestOwnershipWeightedConstraints:
    def test_no_player_exceeds_ownership_cap(self) -> None:
        df = _slate([99.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        result = estimate_ownership_weighted(df)
        assert result["Own_Est"].max() <= OWNERSHIP_CAP + 1e-6

    def test_all_ownership_non_negative(self) -> None:
        df = _slate([30.0, 25.0, 20.0, 15.0, 10.0])
        result = estimate_ownership_weighted(df)
        assert (result["Own_Est"] >= 0.0).all()

    def test_replacement_boost_lifts_ownership(self) -> None:
        # Need 50+ filler players at similar projection to dilute the budget
        # so neither target player reaches the 40% ownership cap.
        n_others = 50
        projs   = [25.0, 25.0] + [25.0] * n_others
        salaries = [5000, 5000] + [5000] * n_others
        df = _slate(projs, salaries)
        df["is_replacement_boost"] = [1, 0] + [0] * n_others
        result = estimate_ownership_weighted(df)
        assert result.loc[0, "Own_Est"] > result.loc[1, "Own_Est"]

    def test_fd_site_normalization(self) -> None:
        dk = estimate_ownership_weighted(_slate([40.0, 30.0, 20.0]), site="DK")
        fd = estimate_ownership_weighted(_slate([40.0, 30.0, 20.0]), site="FD")
        # FD has 9 roster slots vs DK's 8 — FD total ownership is higher
        assert fd["Own_Est"].sum() >= dk["Own_Est"].sum()


class TestOwnershipWeightedConfidenceScores:
    """Verify that stat_confidence / minutes_confidence wire into Component 4."""

    def _slate_with_fillers(
        self,
        spotlight_confs: list[float],
        conf_col: str,
        n_fillers: int = 50,
        filler_conf: float = 0.5,
    ) -> pd.DataFrame:
        n_spot = len(spotlight_confs)
        projs    = [30.0] * n_spot + [20.0] * n_fillers
        salaries = [7000] * n_spot + [5500] * n_fillers
        df = _slate(projs, salaries)
        df[conf_col] = spotlight_confs + [filler_conf] * n_fillers
        return df

    def test_stat_confidence_higher_raises_ownership(self) -> None:
        """Player with high stat_confidence ranks higher than identical player with low."""
        df = self._slate_with_fillers([0.95, 0.05], conf_col="stat_confidence")
        result = estimate_ownership_weighted(df)
        assert result.loc[0, "Own_Est"] > result.loc[1, "Own_Est"]

    def test_minutes_confidence_fallback_raises_ownership(self) -> None:
        """minutes_confidence is used when stat_confidence is absent."""
        df = self._slate_with_fillers([0.95, 0.05], conf_col="minutes_confidence")
        result = estimate_ownership_weighted(df)
        assert result.loc[0, "Own_Est"] > result.loc[1, "Own_Est"]

    def test_zero_stat_confidence_uses_neutral_default(self) -> None:
        """stat_confidence == 0 (no data) should equal the neutral default (0.75)."""
        n_fill = 20
        projs    = [30.0] * 4 + [20.0] * n_fill
        salaries = [7000] * 4 + [5500] * n_fill
        df = _slate(projs, salaries)
        # Players 0,1: no data → fallback to 0.75; Players 2,3: exactly 0.75
        df["stat_confidence"] = [0.0, 0.0, 0.75, 0.75] + [0.5] * n_fill
        result = estimate_ownership_weighted(df)
        # Ownership should be equal because effective conf_score is 0.75 in both cases
        assert abs(result.loc[0, "Own_Est"] - result.loc[2, "Own_Est"]) < 0.01
        assert abs(result.loc[1, "Own_Est"] - result.loc[3, "Own_Est"]) < 0.01

    def test_stat_confidence_takes_priority_over_minutes_confidence(self) -> None:
        """When both columns present, stat_confidence drives the blend; pure minutes_confidence alone is not used."""
        n_fill = 50
        projs    = [30.0] * 2 + [20.0] * n_fill
        salaries = [7000] * 2 + [5500] * n_fill
        df = _slate(projs, salaries)
        # Player 0: high stat_conf + low min_conf → blend ≈ 0.5
        # Player 1: low stat_conf + high min_conf → blend ≈ 0.5
        df["stat_confidence"]    = [0.95, 0.05] + [0.5] * n_fill
        df["minutes_confidence"] = [0.05, 0.95] + [0.5] * n_fill
        result = estimate_ownership_weighted(df)
        # With symmetric blending, both should be approximately equal
        assert abs(result.loc[0, "Own_Est"] - result.loc[1, "Own_Est"]) < 1.0
