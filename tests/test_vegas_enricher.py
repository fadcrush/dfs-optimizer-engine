"""Tests for analysis/shared/vegas_enricher.py."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from analysis.shared.vegas_enricher import enrich_with_vegas


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: minimal slate DataFrame (NBA style)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def nba_slate():
    return pd.DataFrame(
        {
            "Name": ["Player A", "Player B", "Player C"],
            "Team": ["BOS", "MIA", "LAL"],
            "Opp":  ["MIA", "BOS", "GSW"],
            "Salary": [8000, 7500, 7000],
            "Proj": [45.0, 38.0, 35.0],
        }
    )


# Mocked team-totals returned by a successful API fetch.
# _fetch_team_totals returns (dict, int) — tests must mirror that tuple shape.
_MOCK_TOTALS_DICT = {
    "BOS": {"team_total": 115.5, "spread": -5.5, "is_home": 1, "opp_abbrev": "MIA"},
    "MIA": {"team_total": 106.5, "spread": 5.5,  "is_home": 0, "opp_abbrev": "BOS"},
}
_MOCK_TOTALS = (_MOCK_TOTALS_DICT, 1)

_NO_TOTALS = ({}, 0)


class TestEnrichWithVegas:
    def test_returns_dataframe(self, nba_slate):
        """Function always returns a DataFrame even on API failure."""
        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=_NO_TOTALS):
            result = enrich_with_vegas(nba_slate.copy(), sport="NBA")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(nba_slate)

    def test_fallback_columns_added_on_failure(self, nba_slate):
        """On empty totals, NaN columns are still added so downstream won't crash."""
        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=_NO_TOTALS):
            result = enrich_with_vegas(nba_slate.copy(), sport="NBA")
        for col in ("team_total", "spread"):
            assert col in result.columns, f"Column '{col}' missing after failure"

    def test_original_columns_preserved(self, nba_slate):
        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=_NO_TOTALS):
            result = enrich_with_vegas(nba_slate.copy(), sport="NBA")
        for col in nba_slate.columns:
            assert col in result.columns

    def test_enrichment_with_mock_data(self, nba_slate):
        """With valid totals, BOS and MIA should be populated."""
        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=_MOCK_TOTALS):
            result = enrich_with_vegas(nba_slate.copy(), sport="NBA")

        assert "team_total" in result.columns
        bos_row = result[result["Team"] == "BOS"]
        assert not bos_row.empty
        assert bos_row["team_total"].values[0] == pytest.approx(115.5)

    def test_enrichment_sets_vegas_boost(self, nba_slate):
        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=_MOCK_TOTALS):
            result = enrich_with_vegas(nba_slate.copy(), sport="NBA")
        bos = result[result["Team"] == "BOS"]
        # Above league avg (112) → boost > 1
        assert bos["Vegas_Boost"].values[0] > 1.0

    def test_row_count_unchanged(self, nba_slate):
        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=_NO_TOTALS):
            result = enrich_with_vegas(nba_slate.copy(), sport="NBA")
        assert len(result) == len(nba_slate)

    def test_no_api_key_returns_df(self, nba_slate):
        """Missing API key skips fetch; function still returns a DataFrame."""
        import os
        original = os.environ.pop("THE_ODDS_API_KEY", None)
        try:
            result = enrich_with_vegas(nba_slate.copy(), sport="NBA")
            assert isinstance(result, pd.DataFrame)
        finally:
            if original is not None:
                os.environ["THE_ODDS_API_KEY"] = original

    def test_missing_team_column_is_handled(self):
        """DataFrame without a Team column should return without error."""
        df = pd.DataFrame({"Name": ["A", "B"], "Proj": [30.0, 25.0]})
        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=_MOCK_TOTALS):
            result = enrich_with_vegas(df.copy(), sport="NBA")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
