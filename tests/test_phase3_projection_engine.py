"""
Phase 3 Gate Tests — Canonical Projection Engine
=================================================

Verifies that the three Phase 3 contracts hold:

  1. Deprecated entry points (analysis.nba.projections, analysis.nba.projection_pipeline)
     raise RuntimeError when called — they must never silently produce projections.

  2. ``CanonicalNBAProjectionEngine.generate()`` is the sole producer of the
     canonical column set: Proj, Floor, Ceiling, StdDev, Own, Value.

  3. The ``analysis.nba.projection_engine.NBAProjectionEngine`` alias routes to
     ``CanonicalNBAProjectionEngine`` (shim contract still holds).

  4. ``generate()`` always returns non-negative Proj and Floor values.

  5. The orchestrator's ``run_dfs_pipeline()`` calls ``engine.generate()`` exactly once,
     not any deprecated projection function.

All tests are pure-unit — no live DB, no HTTP, no file I/O.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.core.projection_engine import CanonicalNBAProjectionEngine
from analysis.core.schemas import ProjectionContext


# ── Helpers ────────────────────────────────────────────────────────────────

def _minimal_slate(n: int = 10, site: str = "DK") -> pd.DataFrame:
    """Return a minimal slate DataFrame accepted by CanonicalNBAProjectionEngine."""
    positions = ["PG", "SG", "SF", "PF", "C", "PG", "SG", "SF", "PF", "C"]
    rows = []
    for i in range(n):
        rows.append({
            "DFS_ID": f"player_{i}",
            "Raw_DFS_ID": f"player_{i}",
            "Name": f"Player {i}",
            "Pos": positions[i % len(positions)],
            "Team": "LAL" if i % 2 == 0 else "GSW",
            "Opp": "GSW" if i % 2 == 0 else "LAL",
            "Salary": 5000 + i * 500,
            "Site_FPPG": 20.0 + i * 1.5,
            "InjuryStatus": "",
        })
    return pd.DataFrame(rows)


def _minimal_context(site: str = "DK") -> ProjectionContext:
    return ProjectionContext(sport="NBA", site=site)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Deprecated entry points raise RuntimeError
# ═══════════════════════════════════════════════════════════════════════════

class TestDeprecatedEntryPointsRaise:
    """Gate: legacy projection functions must never silently produce projections."""

    def test_generate_nba_projections_from_slate_raises(self):
        """generate_nba_projections_from_slate() must raise RuntimeError."""
        from analysis.nba.projections import generate_nba_projections_from_slate
        df = pd.DataFrame({"Name": ["A"], "Salary": [5000]})
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            generate_nba_projections_from_slate(df)

    def test_build_baseline_from_slate_raises(self):
        """build_baseline_from_slate() must raise RuntimeError."""
        from analysis.nba.projections import build_baseline_from_slate
        df = pd.DataFrame({"Name": ["A"], "Salary": [5000]})
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            build_baseline_from_slate(df)

    def test_apply_defensive_adjustments_raises(self):
        """apply_defensive_adjustments() must raise RuntimeError."""
        from analysis.nba.projections import apply_defensive_adjustments
        df = pd.DataFrame({"Name": ["A"], "Base_Proj": [30.0]})
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            apply_defensive_adjustments(df)

    def test_projection_pipeline_init_raises(self):
        """ProjectionPipeline() must raise RuntimeError on instantiation."""
        from analysis.nba.projection_pipeline import ProjectionPipeline
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            ProjectionPipeline()

    def test_projection_pipeline_with_output_dir_raises(self):
        """ProjectionPipeline(output_dir=...) also raises RuntimeError."""
        from analysis.nba.projection_pipeline import ProjectionPipeline
        with pytest.raises(RuntimeError, match="DEPRECATED"):
            ProjectionPipeline(output_dir=Path("/tmp"))


# ═══════════════════════════════════════════════════════════════════════════
# 2. NBAProjectionEngine alias routes to CanonicalNBAProjectionEngine
# ═══════════════════════════════════════════════════════════════════════════

class TestNBAProjectionEngineShim:
    """Gate: the nba shim module alias must point to the canonical engine."""

    def test_nba_projection_engine_is_canonical(self):
        """analysis.nba.projection_engine.NBAProjectionEngine is the canonical class."""
        from analysis.nba.projection_engine import NBAProjectionEngine
        assert NBAProjectionEngine is CanonicalNBAProjectionEngine

    def test_nba_projection_engine_instantiates(self):
        """NBAProjectionEngine() instantiates without error (it is canonical)."""
        from analysis.nba.projection_engine import NBAProjectionEngine
        engine = NBAProjectionEngine()
        assert isinstance(engine, CanonicalNBAProjectionEngine)


# ═══════════════════════════════════════════════════════════════════════════
# 3. CanonicalNBAProjectionEngine.generate() produces canonical column set
# ═══════════════════════════════════════════════════════════════════════════

class TestCanonicalEngineOutputContract:
    """Gate: engine.generate() is the sole producer of the canonical columns."""

    # Columns that MUST appear in every generate() output
    REQUIRED_COLUMNS = {"Proj", "Floor", "Ceiling", "StdDev", "Own", "Value",
                        "DFS_ID", "Name", "Salary"}

    def _run_engine(self, site: str = "DK") -> pd.DataFrame:
        engine = CanonicalNBAProjectionEngine(
            simulation_enabled=False,   # skip heavy Monte Carlo in tests
            ownership_enabled=False,    # skip ownership model DB access
            dvp_enabled=False,          # skip DvP DB access
            b2b_enabled=False,          # skip B2B DB access
            blowout_enabled=False,      # skip blowout DB access
            minutes_trend_enabled=False,
            game_total_enabled=False,
            injury_boost_enabled=False,
        )
        slate_df = _minimal_slate(site=site)
        ctx = _minimal_context(site=site)
        return engine.generate(slate_df, ctx)

    def test_required_columns_present_dk(self):
        """generate() for DK site produces all required columns."""
        result = self._run_engine(site="DK")
        missing = self.REQUIRED_COLUMNS - set(result.columns)
        assert not missing, f"Missing columns: {missing}"

    def test_required_columns_present_fd(self):
        """generate() for FD site produces all required columns."""
        result = self._run_engine(site="FD")
        missing = self.REQUIRED_COLUMNS - set(result.columns)
        assert not missing, f"Missing columns: {missing}"

    def test_proj_non_negative(self):
        """All Proj values must be >= 0 after generate()."""
        result = self._run_engine()
        assert (result["Proj"] >= 0).all(), "Negative projections detected"

    def test_floor_non_negative(self):
        """Floor values must always be >= 0."""
        result = self._run_engine()
        assert (result["Floor"] >= 0).all(), "Negative floor values detected"

    def test_ceiling_gte_proj(self):
        """Ceiling must be >= Proj for every player."""
        result = self._run_engine()
        assert (result["Ceiling"] >= result["Proj"]).all(), \
            "Ceiling < Proj for some players"

    def test_floor_lte_proj(self):
        """Floor must be <= Proj for every player."""
        result = self._run_engine()
        assert (result["Floor"] <= result["Proj"]).all(), \
            "Floor > Proj for some players"

    def test_stddev_non_negative(self):
        """StdDev must be >= 0."""
        result = self._run_engine()
        assert (result["StdDev"] >= 0).all(), "Negative StdDev values detected"

    def test_value_column_computed(self):
        """Value = Proj / (Salary / 1000); must be non-negative."""
        result = self._run_engine()
        assert "Value" in result.columns
        assert (result["Value"] >= 0).all()

    def test_returns_dataframe(self):
        """generate() must return a pandas DataFrame (not dict, not list)."""
        result = self._run_engine()
        assert isinstance(result, pd.DataFrame)

    def test_output_row_count_matches_input(self):
        """generate() must return the same number of rows as the input slate."""
        slate_df = _minimal_slate(n=8)
        engine = CanonicalNBAProjectionEngine(
            simulation_enabled=False, ownership_enabled=False,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, game_total_enabled=False,
            injury_boost_enabled=False,
        )
        result = engine.generate(slate_df, _minimal_context())
        assert len(result) == len(slate_df)

    def test_sport_validation(self):
        """generate() raises ValueError for non-NBA sport."""
        engine = CanonicalNBAProjectionEngine(simulation_enabled=False)
        slate_df = _minimal_slate()
        ctx = ProjectionContext(sport="NFL", site="DK")
        with pytest.raises(ValueError, match="NBA"):
            engine.generate(slate_df, ctx)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Orchestrator calls engine.generate() – not any deprecated path
# ═══════════════════════════════════════════════════════════════════════════

class TestOrchestratorCallsCanonicalEngine:
    """Gate: run_dfs_pipeline() must route through CanonicalNBAProjectionEngine.generate()."""

    def test_orchestrator_calls_engine_generate(self, tmp_path):
        """run_dfs_pipeline calls engine.generate(), not the deprecated functions."""
        # Write a minimal slate CSV
        slate_path = tmp_path / "slate.csv"
        slate_df = _minimal_slate()
        slate_df.to_csv(slate_path, index=False)

        ctx = _minimal_context()

        generate_calls = []

        def _fake_generate(self_engine, df, context):
            generate_calls.append({"rows": len(df)})
            # Return a minimal projections_df that downstream steps accept
            out = df.copy()
            out["Proj"] = 25.0
            out["Floor"] = 15.0
            out["Ceiling"] = 40.0
            out["StdDev"] = 5.0
            out["Own"] = 10.0
            out["Value"] = 5.0
            out["InjuryStatus"] = ""
            out["GL_L10"] = 0.0
            out["DvP"] = 1.0
            out["Rest"] = 1.0
            out["Blowout"] = 1.0
            out["MinutesTrend"] = 0.0
            out["GameTotal"] = 1.0
            out["InjuryBoost"] = 1.0
            out["Leverage"] = 2.5
            out["Own_Est"] = 10.0
            out["own_source"] = "mock"
            out["Sim_P90"] = float("nan")
            out["Sim_Boost"] = float("nan")
            out["Boom_Rate"] = float("nan")
            out["Raw_DFS_ID"] = out["DFS_ID"]
            return out

        with patch.object(CanonicalNBAProjectionEngine, "generate", _fake_generate):
            from analysis.core.orchestrator import run_dfs_pipeline
            result = run_dfs_pipeline(
                str(slate_path),
                context=ctx,
                n_lineups=0,
                apply_filter=False,
                skip_injury_refresh=True,
            )

        assert generate_calls, "engine.generate() was never called by orchestrator"
        assert result["success"] is True

    def test_deprecated_projection_function_not_called_in_orchestrator(self, tmp_path):
        """The orchestrator module must not import or call generate_nba_projections_from_slate."""
        import importlib
        import analysis.core.orchestrator as orch_module
        # The orchestrator source must not reference the deprecated function name
        import inspect
        source = inspect.getsource(orch_module)
        assert "generate_nba_projections_from_slate" not in source
        assert "from analysis.nba.projections" not in source
        # Also verify it does NOT import ProjectionPipeline
        assert "ProjectionPipeline" not in source


# ═══════════════════════════════════════════════════════════════════════════
# 5. Site-FPPG fallback works without game-log DB
# ═══════════════════════════════════════════════════════════════════════════

class TestEngineGracefulDegradation:
    """Gate: engine produces valid projections even when all data sources are absent."""

    def test_engine_uses_site_fppg_when_no_gl_db(self, tmp_path):
        """Without game-log DB, Site_FPPG is used as the projection baseline."""
        engine = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "nonexistent.duckdb",  # forces empty baseline
            simulation_enabled=False,
            ownership_enabled=False,
            dvp_enabled=False,
            b2b_enabled=False,
            blowout_enabled=False,
            minutes_trend_enabled=False,
            game_total_enabled=False,
            injury_boost_enabled=False,
        )
        slate_df = _minimal_slate()
        ctx = _minimal_context()
        result = engine.generate(slate_df, ctx)

        # All players should still have a non-zero projection (driven by Site_FPPG)
        assert (result["Proj"] > 0).all(), \
            "Some players have Proj=0 even with Site_FPPG fallback"

    def test_engine_base_proj_overrides_fppg(self, tmp_path):
        """User-supplied Base_Proj takes precedence over GL_L10 and Site_FPPG."""
        slate_df = _minimal_slate()
        # Give the first player a very high Base_Proj
        slate_df["Base_Proj"] = 0.0
        slate_df.loc[0, "Base_Proj"] = 99.9

        engine = CanonicalNBAProjectionEngine(
            gl_db_path=tmp_path / "nonexistent.duckdb",
            simulation_enabled=False,
            ownership_enabled=False,
            dvp_enabled=False, b2b_enabled=False, blowout_enabled=False,
            minutes_trend_enabled=False, game_total_enabled=False,
            injury_boost_enabled=False,
        )
        result = engine.generate(slate_df, _minimal_context())

        # Player 0 should have Proj ~= 99.9 (Base_Proj wins)
        assert result.loc[result["Name"] == "Player 0", "Proj"].iloc[0] == pytest.approx(99.9, abs=0.1)
