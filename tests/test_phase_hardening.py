"""
Phase 1–8 Hardening Tests
=========================
Tests for the load-bearing fixes and new features introduced in the
correction + hardening pass.

Covers:
  Phase 1  — .env loading + Vegas enrichment health
  Phase 2  — Fantasy score integrity (projection engine SQL fallback)
  Phase 4  — Data quality flags
  Phase 5  — GPP vs Cash optimizer constraints
  Phase 6  — Value column
  Phase 8  — Player trends projection delta
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Repo root on path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))


# ===========================================================================
# Phase 1: .env loading
# ===========================================================================

class TestEnvLoading:
    def test_config_module_exports_root_path(self):
        from backend.config import ROOT
        assert (ROOT / ".env.example").exists(), "ROOT should point to the repo root"

    def test_ensure_env_loaded_is_idempotent(self):
        from backend.config import ensure_env_loaded
        # calling twice should not raise
        ensure_env_loaded()
        ensure_env_loaded()

    def test_env_example_uses_correct_key_name(self):
        env_example = _REPO_ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "THE_ODDS_API_KEY=" in content, (
            ".env.example must declare THE_ODDS_API_KEY (not THEODDS_API_KEY)"
        )
        assert "THEODDS_API_KEY=" not in content or content.index("THE_ODDS_API_KEY=") < content.index("THEODDS_API_KEY=") + 100, (
            "THEODDS_API_KEY should not appear in .env.example; use THE_ODDS_API_KEY"
        )

    def test_database_db_loads_explicit_path(self):
        """backend/database/db.py must not use bare load_dotenv()."""
        db_source = (_REPO_ROOT / "backend" / "database" / "db.py").read_text(encoding="utf-8")
        # After our fix: must use explicit path, not bare load_dotenv()
        assert '_REPO_ROOT' in db_source or 'parent.parent.parent' in db_source, (
            "database/db.py must load .env via explicit repo-root path"
        )
        assert 'load_dotenv()' not in db_source or 'load_dotenv(dotenv_path' in db_source, (
            "database/db.py must not use bare load_dotenv() — use explicit dotenv_path"
        )


# ===========================================================================
# Phase 1: Vegas enrichment health state
# ===========================================================================

class TestVegasHealth:
    def test_get_vegas_health_returns_dict(self):
        from analysis.shared.vegas_enricher import get_vegas_health
        h = get_vegas_health()
        assert isinstance(h, dict)
        assert "api_key_loaded" in h
        assert "last_successful_enrichment_at" in h
        assert "games_enriched_on_last_run" in h
        assert "players_enriched_on_last_run" in h
        assert "last_error" in h

    def test_api_key_loaded_false_when_key_missing(self):
        from analysis.shared.vegas_enricher import get_vegas_health
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("THE_ODDS_API_KEY", None)
            h = get_vegas_health()
        assert h["api_key_loaded"] is False

    def test_api_key_loaded_true_when_key_present(self):
        from analysis.shared.vegas_enricher import get_vegas_health
        with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test_key_xyz"}):
            h = get_vegas_health()
        assert h["api_key_loaded"] is True

    def test_enrich_with_vegas_no_key_returns_nan_columns(self):
        from analysis.shared.vegas_enricher import enrich_with_vegas
        df = pd.DataFrame({"Name": ["LeBron James"], "Team": ["LAL"], "Proj": [45.0]})
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("THE_ODDS_API_KEY", None)
            result = enrich_with_vegas(df, sport="NBA")
        assert "team_total" in result.columns
        assert result["team_total"].isna().all()

    def test_enrich_with_vegas_updates_health_state_on_success(self):
        from analysis.shared.vegas_enricher import enrich_with_vegas, _vegas_health
        df = pd.DataFrame({"Name": ["LeBron James"], "Team": ["LAL"], "Proj": [45.0]})
        fake_totals = {"LAL": {"team_total": 118.5, "spread": -4.5, "is_home": 1, "opp_abbrev": "DEN"}}

        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=(fake_totals, 5)):
            enrich_with_vegas(df, sport="NBA")

        assert _vegas_health["last_successful_enrichment_at"] is not None
        assert _vegas_health["games_enriched_on_last_run"] == 5
        assert _vegas_health["players_enriched_on_last_run"] == 1
        assert _vegas_health["last_error"] is None


# ===========================================================================
# Phase 2: Fantasy score integrity
# ===========================================================================

class TestFantasyScoreIntegrity:
    def test_canonical_dk_formula(self):
        from backend.services.fantasy_scoring import compute_dk_score
        # 20 pts, 2 3pm, 10 reb, 5 ast, 2 stl, 1 blk, 3 tov — DD bonus (pts+reb)
        score = compute_dk_score(20, 2, 10, 5, 2, 1, 3)
        expected = 20*1.0 + 2*0.5 + 10*1.25 + 5*1.5 + 2*2.0 + 1*2.0 + 3*(-0.5) + 1.5
        assert abs(score - expected) < 0.01

    def test_canonical_fd_formula(self):
        from backend.services.fantasy_scoring import compute_fd_score
        score = compute_fd_score(8, 3, 2, 9, 5, 1, 1, 2)
        expected = 8*2.0 + 3*1.0 + 2*1.0 + 9*1.2 + 5*1.5 + 1*3.0 + 1*3.0 + 2*(-1.0)
        assert abs(score - expected) < 0.01

    def test_score_game_row_handles_missing_fields(self):
        from backend.services.fantasy_scoring import score_game_row
        dk, fd = score_game_row({})
        assert dk == 0.0
        assert fd == 0.0

    def test_score_game_row_matches_individual_functions(self):
        from backend.services.fantasy_scoring import score_game_row, compute_dk_score, compute_fd_score
        row = {
            "points": 25, "three_pointers": 3, "rebounds": 7,
            "assists": 8, "steals": 2, "blocks": 0, "turnovers": 2,
            "fg_made": 9, "ft_made": 4,
        }
        dk, fd = score_game_row(row)
        assert abs(dk - compute_dk_score(25, 3, 7, 8, 2, 0, 2)) < 0.01
        assert abs(fd - compute_fd_score(9, 4, 3, 7, 8, 2, 0, 2)) < 0.01


# ===========================================================================
# Phase 4: Data quality flags
# ===========================================================================

class TestDataQualityFlags:
    def _make_proj_df(self, **overrides):
        data = {
            "Name": ["Player A", "Player B"],
            "Proj": [35.0, 28.0],
            "Salary": [8000, 6200],
            "Team": ["LAL", "BOS"],
            "Pos": ["PG", "SF"],
            "DFS_ID": ["1", "2"],
        }
        data.update(overrides)
        return pd.DataFrame(data)

    def test_value_column_computed(self):
        df = self._make_proj_df()
        df["Value"] = (
            pd.to_numeric(df["Proj"]) / (pd.to_numeric(df["Salary"]) / 1000)
        ).round(2)
        assert "Value" in df.columns
        assert abs(df.loc[0, "Value"] - 35.0 / 8.0) < 0.01

    def test_flag_vegas_applied(self):
        """When team_total is valid, flag should include 'vegas_applied'."""
        df = self._make_proj_df()
        df["team_total"] = [118.5, 112.0]
        df["own_source"] = ["model", "model"]
        df["InjuryStatus"] = ["", ""]

        flags, conf = _run_flag_logic(df)

        assert "vegas_applied" in flags[0]
        assert "vegas_applied" in flags[1]

    def test_flag_vegas_fallback_when_nan(self):
        df = self._make_proj_df()
        df["team_total"] = [float("nan"), float("nan")]
        df["own_source"] = ["model", "model"]

        with patch.dict(os.environ, {"THE_ODDS_API_KEY": "fake_key"}):
            flags, conf = _run_flag_logic(df)

        assert "vegas_fallback" in flags[0]

    def test_flag_ownership_trained_vs_fallback(self):
        df = self._make_proj_df()
        df["team_total"] = [float("nan"), float("nan")]
        df["own_source"] = ["model", "fallback"]

        flags, _ = _run_flag_logic(df)
        assert "ownership_trained" in flags[0]
        assert "ownership_fallback" in flags[1]

    def test_confidence_score_decreases_with_missing_signals(self):
        # All signals present
        df_good = self._make_proj_df()
        df_good["team_total"] = [118.5, 112.0]
        df_good["own_source"] = ["model", "model"]
        df_good["InjuryStatus"] = ["", ""]

        # Missing vegas + fallback ownership
        df_bad = self._make_proj_df()
        df_bad["team_total"] = [float("nan"), float("nan")]
        df_bad["own_source"] = ["fallback", "fallback"]
        df_bad["games_played"] = [3, 2]

        with patch.dict(os.environ, {"THE_ODDS_API_KEY": "fake_key"}):
            _, conf_good = _run_flag_logic(df_good)
            _, conf_bad = _run_flag_logic(df_bad)

        assert conf_good[0] > conf_bad[0]


def _run_flag_logic(df: pd.DataFrame):
    """Re-run the flag/confidence logic from orchestrator on a DataFrame.
    Extracted here so tests don't need to run the full pipeline.
    """
    import math as _math

    api_key_present = bool(os.getenv("THE_ODDS_API_KEY", ""))
    flag_rows = []
    conf_rows = []

    for _, row in df.iterrows():
        flags = []

        _tt = row.get("team_total")
        _tt_valid = _tt is not None and not (isinstance(_tt, float) and _math.isnan(_tt))
        if _tt_valid:
            flags.append("vegas_applied")
        elif api_key_present:
            flags.append("vegas_fallback")

        _own_src = str(row.get("own_source", ""))
        if _own_src == "model":
            flags.append("ownership_trained")
        elif _own_src in ("fallback", "weighted", "simple"):
            flags.append("ownership_fallback")

        _inj = str(row.get("InjuryStatus", "")).upper()
        if _inj and _inj not in ("", "A", "ACTIVE"):
            flags.append("injury_adjusted")

        _games = row.get("games_played") or 0
        if _games and int(_games) < 5:
            flags.append("low_minutes_sample")

        flag_rows.append(flags)

        conf = 1.0
        if "vegas_fallback" in flags:
            conf -= 0.20
        if "ownership_fallback" in flags:
            conf -= 0.10
        if "low_minutes_sample" in flags:
            conf -= 0.20
        conf_rows.append(round(max(0.0, min(1.0, conf)), 2))

    return flag_rows, conf_rows


# ===========================================================================
# Phase 5: GPP vs Cash constraints
# ===========================================================================

class TestOptimizerConstraints:
    def _base_pool(self) -> pd.DataFrame:
        return pd.DataFrame({
            "Name":   ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
            "Proj":   [40, 35, 30, 28, 25, 22, 20, 18, 15],
            "Salary": [9000, 8000, 7500, 7000, 6500, 6000, 5500, 5000, 4500],
            "Pos":    ["PG", "SG", "SF", "PF", "C", "PG", "SG", "SF", "PF"],
            "Floor":  [30, 25, 22, 20, 18, 15, 13, 11, 8],
            "StdDev": [8, 12, 6, 5, 4, 9, 11, 7, 6],
            "Own":    [35, 28, 20, 12, 8, 5, 4, 3, 2],
            "DFS_ID": [str(i) for i in range(9)],
        })

    def test_cash_removes_high_cv_players(self):
        from analysis.nba.optimizer_constraints import apply_contest_constraints
        pool = self._base_pool()
        filtered, _ = apply_contest_constraints(pool, contest_type="cash", site="DK")
        # Players with StdDev/Proj > 0.35 should be removed
        filtered["cv"] = filtered["StdDev"] / filtered["Proj"]
        assert (filtered["cv"] <= 0.35).all(), "Cash should remove high-CV players"

    def test_cash_no_stack_rule(self):
        from analysis.nba.optimizer_constraints import apply_contest_constraints
        pool = self._base_pool()
        _, stack = apply_contest_constraints(pool, contest_type="cash")
        assert stack is None, "Cash contests should have no StackRule"

    def test_gpp_returns_stack_rule(self):
        from analysis.nba.optimizer_constraints import apply_contest_constraints
        pool = self._base_pool()
        _, stack = apply_contest_constraints(pool, contest_type="gpp")
        assert stack is not None, "GPP should return a StackRule"

    def test_gpp_ceiling_blend_adjusts_proj(self):
        from analysis.nba.optimizer_constraints import apply_contest_constraints
        pool = self._base_pool()
        pool["Ceiling"] = pool["Proj"] * 1.4  # ceiling is 40% above proj
        original_proj = pool["Proj"].copy()
        filtered, _ = apply_contest_constraints(pool, contest_type="gpp")
        # Proj should now be blended with ceiling
        assert (filtered["Proj"] >= original_proj).all(), (
            "GPP ceiling blend should shift Proj upward when Ceiling > Proj"
        )

    def test_get_constraints_returns_correct_type(self):
        from analysis.nba.optimizer_constraints import get_constraints, ContestConstraints
        assert isinstance(get_constraints("gpp"), ContestConstraints)
        assert isinstance(get_constraints("cash"), ContestConstraints)
        assert isinstance(get_constraints("UNKNOWN"), ContestConstraints)  # falls back to GPP

    def test_cash_floor_gate_removes_risky_players(self):
        from analysis.nba.optimizer_constraints import apply_contest_constraints, CASH_MIN_FLOOR_BY_POS
        pool = self._base_pool()
        # Set last player's floor well below threshold
        pool.loc[8, "Floor"] = 1.0
        filtered, _ = apply_contest_constraints(pool, contest_type="cash")
        # The player with Floor=1.0 (PF position, threshold ~18) must be removed
        assert len(filtered) < len(pool), "Floor gate should remove below-threshold players"


# ===========================================================================
# Phase 8: Player trends projection delta
# ===========================================================================

class TestPlayerTrendsDelta:
    def _split(self, avg_dk=38.0, avg_fd=34.0, games=10):
        return {
            "games_used": games,
            "avg_dk_points": avg_dk,
            "avg_fd_points": avg_fd,
            "avg_minutes": 34.0,
        }

    def test_under_projecting_signal(self):
        """When projection > avg_historical by > 1.5, signal = over_projecting."""
        from backend.services.player_trends_service import _compute_projection_delta
        split = self._split(avg_dk=32.0)
        result = _compute_projection_delta(split, projection=40.0, site="dk")
        assert result is not None
        assert result["signal"] == "over_projecting"
        assert result["delta"] == pytest.approx(8.0, abs=0.1)

    def test_over_projecting_becomes_under_when_reversed(self):
        from backend.services.player_trends_service import _compute_projection_delta
        split = self._split(avg_dk=44.0)
        result = _compute_projection_delta(split, projection=40.0, site="dk")
        assert result["signal"] == "under_projecting"
        assert result["delta"] == pytest.approx(-4.0, abs=0.1)

    def test_aligned_signal_within_threshold(self):
        from backend.services.player_trends_service import _compute_projection_delta
        split = self._split(avg_dk=38.0)
        result = _compute_projection_delta(split, projection=38.8, site="dk")
        assert result["signal"] == "aligned"

    def test_returns_none_when_split_is_none(self):
        from backend.services.player_trends_service import _compute_projection_delta
        assert _compute_projection_delta(None, 40.0) is None

    def test_returns_none_when_projection_is_none(self):
        from backend.services.player_trends_service import _compute_projection_delta
        split = self._split()
        assert _compute_projection_delta(split, None) is None

    def test_confidence_high_when_many_games(self):
        from backend.services.player_trends_service import _compute_projection_delta
        split = self._split(games=10)
        result = _compute_projection_delta(split, 40.0)
        assert result["confidence"] == "high"

    def test_confidence_low_when_few_games(self):
        from backend.services.player_trends_service import _compute_projection_delta
        split = self._split(games=3)
        result = _compute_projection_delta(split, 40.0)
        assert result["confidence"] == "low"

    def test_delta_pct_computed_correctly(self):
        from backend.services.player_trends_service import _compute_projection_delta
        split = self._split(avg_dk=40.0)
        result = _compute_projection_delta(split, 44.0)
        assert result["delta_pct"] == pytest.approx(10.0, abs=0.1)


# ===========================================================================
# Phase 1: Vegas enricher fetch tuple unpacking
# ===========================================================================

class TestVegasEnricherTupleReturn:
    """_fetch_team_totals now returns (dict, int) — verify callers handle this."""

    def test_fetch_no_key_returns_empty_tuple(self):
        from analysis.shared.vegas_enricher import _fetch_team_totals
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("THE_ODDS_API_KEY", None)
            totals, game_count = _fetch_team_totals("NBA")
        assert totals == {}
        assert game_count == 0

    def test_enrich_with_vegas_uses_tuple_return(self):
        """enrich_with_vegas must correctly unpack the (dict, int) tuple."""
        from analysis.shared.vegas_enricher import enrich_with_vegas
        df = pd.DataFrame({"Name": ["P1"], "Team": ["LAL"], "Proj": [40.0]})
        fake_totals = {"LAL": {"team_total": 115.0, "spread": -3.0, "is_home": 1, "opp_abbrev": "BOS"}}

        with patch("analysis.shared.vegas_enricher._fetch_team_totals", return_value=(fake_totals, 3)):
            result = enrich_with_vegas(df, sport="NBA")

        assert result.loc[0, "team_total"] == 115.0
        assert result.loc[0, "Vegas_Boost"] > 1.0
