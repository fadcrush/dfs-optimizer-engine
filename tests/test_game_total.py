"""
Phase 19 — Tests for game over/under (game-total) projection adjustment

Tests cover:
  - get_game_total_multipliers() with various O/U values
  - High-total games boost projections
  - Low-total games reduce projections
  - League-average total is neutral (1.0)
  - Adjustment is capped at ±cap (default ±5%)
  - Teams missing from vegas dict get neutral 1.0 in the engine
  - game_total_enabled=False → GameTotal column = 1.0, Proj unchanged
  - GameTotal column always present in engine output
  - "total" vs "game_total" key alias both accepted
  - Engine Layer 8 applied after Layer 7 (MinutesTrend) before variance
  - Multiple teams in same game get same multiplier
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from analysis.nba.b2b import (
    get_game_total_multipliers,
    _LEAGUE_AVG_TOTAL,
    _TOTAL_SENSITIVITY,
    _TOTAL_CAP,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _vegas(totals: dict[str, float], use_alias: bool = False) -> dict[str, dict]:
    """Build a minimal vegas_totals dict keyed by team abbreviation."""
    key = "game_total" if use_alias else "total"
    return {team: {key: t, "spread": 0.0} for team, t in totals.items()}


def _engine_slate(n: int = 8, teams: list[str] | None = None) -> pd.DataFrame:
    """Minimal slate suitable for engine generate() calls."""
    _POSITIONS = ["PG", "SG", "SF", "PF", "C", "PG", "SG", "SF",
                  "PF", "C", "PG", "SG", "SF", "PF", "C", "PG", "SG", "SF"]
    if teams is None:
        teams = ["LAL"] * n
    return pd.DataFrame({
        "Name":      [f"Player{i}" for i in range(n)],
        "Salary":    [6000 + i * 200 for i in range(n)],
        "Base_Proj": [30.0 + i for i in range(n)],
        "Team":      teams[:n],
        "Opp":       (["BOS"] * n)[:n],
        "Pos":       _POSITIONS[:n],
    })


# ===========================================================================
# TestGetGameTotalMultipliers — unit tests for the helper function
# ===========================================================================

class TestGetGameTotalMultipliers:
    """Unit tests for get_game_total_multipliers()."""

    def test_empty_input_returns_empty(self):
        assert get_game_total_multipliers({}) == {}

    def test_league_average_total_is_neutral(self):
        """At league average O/U the multiplier should be exactly 1.0."""
        result = get_game_total_multipliers(_vegas({"LAL": _LEAGUE_AVG_TOTAL}))
        assert "LAL" in result
        assert result["LAL"] == pytest.approx(1.0, abs=1e-5)

    def test_high_total_boosts(self):
        """O/U above league average → multiplier > 1.0."""
        result = get_game_total_multipliers(_vegas({"LAL": _LEAGUE_AVG_TOTAL + 10}))
        assert result["LAL"] > 1.0

    def test_low_total_reduces(self):
        """O/U below league average → multiplier < 1.0."""
        result = get_game_total_multipliers(_vegas({"BOS": _LEAGUE_AVG_TOTAL - 10}))
        assert result["BOS"] < 1.0

    def test_10_point_above_avg_gives_correct_delta(self):
        """(avg + 10) * sensitivity = 10 * 0.0025 = 0.025 → multiplier = 1.025."""
        total = _LEAGUE_AVG_TOTAL + 10
        result = get_game_total_multipliers(_vegas({"GSW": total}))
        expected = 1.0 + 10 * _TOTAL_SENSITIVITY
        assert result["GSW"] == pytest.approx(expected, abs=1e-4)

    def test_10_point_below_avg_gives_correct_delta(self):
        """(avg - 10) * sensitivity = -0.025 → multiplier = 0.975."""
        total = _LEAGUE_AVG_TOTAL - 10
        result = get_game_total_multipliers(_vegas({"MIA": total}))
        expected = 1.0 - 10 * _TOTAL_SENSITIVITY
        assert result["MIA"] == pytest.approx(expected, abs=1e-4)

    def test_cap_limits_positive_adjustment(self):
        """Extreme high O/U should be capped at 1.0 + cap."""
        total = _LEAGUE_AVG_TOTAL + 1000   # extreme
        result = get_game_total_multipliers(_vegas({"LAL": total}))
        assert result["LAL"] == pytest.approx(1.0 + _TOTAL_CAP, abs=1e-5)

    def test_cap_limits_negative_adjustment(self):
        """Extreme low O/U should be capped at 1.0 - cap."""
        total = 0.0   # extreme low
        result = get_game_total_multipliers(_vegas({"LAL": total}))
        assert result["LAL"] == pytest.approx(1.0 - _TOTAL_CAP, abs=1e-5)

    def test_game_total_key_alias_accepted(self):
        """'game_total' key should work the same as 'total'."""
        normal = get_game_total_multipliers(_vegas({"LAL": 230.0}))
        alias  = get_game_total_multipliers(_vegas({"LAL": 230.0}, use_alias=True))
        assert "LAL" in normal
        assert "LAL" in alias
        assert normal["LAL"] == pytest.approx(alias["LAL"], abs=1e-5)

    def test_team_key_uppercased(self):
        """Teams with lowercase keys should be returned uppercased."""
        input_vegas = {"lal": {"total": 225.0, "spread": 0.0}}
        result = get_game_total_multipliers(input_vegas)
        assert "LAL" in result
        assert "lal" not in result

    def test_team_missing_total_excluded(self):
        """Team entries without a 'total' or 'game_total' key are excluded."""
        input_vegas = {"LAL": {"spread": 3.5}}   # no total key
        result = get_game_total_multipliers(input_vegas)
        assert "LAL" not in result

    def test_multiple_teams_independent(self):
        """Each team gets its own multiplier based on its game total."""
        result = get_game_total_multipliers(
            _vegas({"LAL": 235.0, "BOS": 235.0, "GSW": 210.0})
        )
        assert result["LAL"] == result["BOS"]   # same O/U
        assert result["GSW"] < result["LAL"]     # lower O/U → smaller multiplier

    def test_custom_sensitivity(self):
        """Custom sensitivity parameter changes the slope."""
        total = _LEAGUE_AVG_TOTAL + 10
        result = get_game_total_multipliers(_vegas({"LAL": total}), sensitivity=0.005)
        expected = 1.0 + 10 * 0.005   # 0.05 → at cap
        assert result["LAL"] == pytest.approx(min(1.0 + expected - 1.0, 1.0 + _TOTAL_CAP), abs=1e-4)


# ===========================================================================
# TestEngineGameTotalLayer — integration tests through generate()
# ===========================================================================

class TestEngineGameTotalLayer:
    """Integration tests for Layer 8 in CanonicalNBAProjectionEngine.generate()."""

    def test_game_total_column_always_present(self, tmp_path):
        """GameTotal column must exist in output even with no Vegas data."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, ownership_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        out = engine.generate(_engine_slate(8), ctx)
        assert "GameTotal" in out.columns

    def test_game_total_disabled_sets_one(self, tmp_path):
        """game_total_enabled=False → all GameTotal values are 1.0."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, ownership_enabled=False,
            game_total_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", locks=[], fades=[])
        out = engine.generate(_engine_slate(8), ctx)
        assert (out["GameTotal"] == 1.0).all()

    def test_high_total_boosts_proj_via_context(self, tmp_path):
        """Passing a high-total game in context.vegas should boost Proj."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        # LAL has high O/U, so their players should have higher projections
        vegas = {"LAL": {"total": 240.0, "spread": 0.0},
                 "BOS": {"total": 240.0, "spread": 0.0}}

        engine_on = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, ownership_enabled=False,
            game_total_enabled=True,
        )
        engine_off = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, ownership_enabled=False,
            game_total_enabled=False,
        )

        ctx_on  = ProjectionContext(site="DK", sport="NBA", vegas=vegas, locks=[], fades=[])
        ctx_off = ProjectionContext(site="DK", sport="NBA",               locks=[], fades=[])

        slate = _engine_slate(8)
        out_on  = engine_on.generate(slate.copy(), ctx_on)
        out_off = engine_off.generate(slate.copy(), ctx_off)

        # All LAL players should have higher proj when game_total_enabled
        assert (out_on["Proj"] > out_off["Proj"]).all()

    def test_low_total_reduces_proj_via_context(self, tmp_path):
        """Passing a low-total game in context.vegas should reduce Proj."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        vegas = {"LAL": {"total": 200.0, "spread": 0.0},
                 "BOS": {"total": 200.0, "spread": 0.0}}

        engine_on = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, ownership_enabled=False,
            game_total_enabled=True,
        )
        engine_off = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, ownership_enabled=False,
            game_total_enabled=False,
        )

        ctx_on  = ProjectionContext(site="DK", sport="NBA", vegas=vegas, locks=[], fades=[])
        ctx_off = ProjectionContext(site="DK", sport="NBA",               locks=[], fades=[])

        slate = _engine_slate(8)
        out_on  = engine_on.generate(slate.copy(), ctx_on)
        out_off = engine_off.generate(slate.copy(), ctx_off)

        # All players should have lower proj with low O/U
        assert (out_on["Proj"] < out_off["Proj"]).all()

    def test_team_not_in_vegas_gets_neutral(self, tmp_path):
        """Teams absent from the vegas dict receive GameTotal = 1.0."""
        from analysis.core.projection_engine import CanonicalNBAProjectionEngine
        from analysis.core.schemas import ProjectionContext

        # Only BOS is in the vegas dict; LAL is not
        vegas = {"BOS": {"total": 235.0, "spread": 0.0}}

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "dfs_edge.duckdb",
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, ownership_enabled=False,
        )
        ctx = ProjectionContext(site="DK", sport="NBA", vegas=vegas, locks=[], fades=[])
        slate = _engine_slate(8, teams=["LAL"] * 8)
        out = engine.generate(slate, ctx)

        # LAL players have no total → GameTotal should be 1.0
        assert (out["GameTotal"] == 1.0).all()
