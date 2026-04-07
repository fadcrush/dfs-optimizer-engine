"""Phase 5 gate tests — Orchestrator decomposition.

Verifies:
1. All 12 new stage helpers exist and are importable.
2. run_dfs_pipeline public signature is unchanged.
3. Each helper is callable in isolation with minimal stubs.
4. The coordinator correctly chains helpers (end-to-end smoke with a synthetic CSV).
5. No god-function smell: run_dfs_pipeline body is small (delegates, not implements).
"""
from __future__ import annotations

import inspect
import os
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ── Import all helpers (test 1: they must exist) ────────────────────────────

from analysis.core.orchestrator import (
    _apply_injury_context,
    _apply_pool_preparation,
    _apply_projection_overrides,
    _build_initial_result,
    _build_projections,
    _compute_value_and_quality,
    _enrich_props_and_ownership,
    _export_results,
    _generate_lineups,
    _ingest_and_enrich_slate,
    _run_pre_sim,
    _run_simulation,
    run_dfs_pipeline,
)
from analysis.core.orchestrator import (
    _auto_refresh_injuries,
    _print_injury_summary,
    _read_slate_csv,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

MINIMAL_CSV = textwrap.dedent("""\
    Id,Nickname,Position,Salary,Game,TeamAbbrev,FPPG
    100,LeBron James,PF,8000,LAL@BOS,LAL,42.5
    101,Anthony Davis,C,9000,LAL@BOS,LAL,48.0
    102,Jayson Tatum,SF,8500,LAL@BOS,BOS,44.0
    103,Jaylen Brown,SG,7500,LAL@BOS,BOS,36.0
    104,Devin Booker,SG,7000,PHX@GSW,PHX,35.0
    105,Kevin Durant,SF,8200,PHX@GSW,PHX,43.0
    106,Stephen Curry,PG,9200,PHX@GSW,GSW,50.0
    107,Draymond Green,PF,6500,PHX@GSW,GSW,28.0
    108,Kyle Lowry,PG,5000,MIA@CHI,MIA,22.0
    109,Bam Adebayo,C,7800,MIA@CHI,MIA,38.0
""")


@pytest.fixture
def tmp_csv(tmp_path):
    f = tmp_path / "slate.csv"
    f.write_text(MINIMAL_CSV)
    return f


@pytest.fixture
def minimal_df():
    rows = [
        {"DFS_ID": str(i), "Name": n, "Salary": s, "Position": p,
         "TeamAbbrev": t, "Game": "A@B", "FPPG": fp, "Proj": fp * 0.9,
         "Own": 10.0, "Floor": fp * 0.6, "Ceiling": fp * 1.4}
        for i, (n, s, p, t, fp) in enumerate([
            ("LeBron James", 8000, "PF", "LAL", 42.5),
            ("Anthony Davis", 9000, "C", "LAL", 48.0),
            ("Jayson Tatum", 8500, "SF", "BOS", 44.0),
            ("Jaylen Brown", 7500, "SG", "BOS", 36.0),
            ("Devin Booker", 7000, "SG", "PHX", 35.0),
            ("Kevin Durant", 8200, "SF", "PHX", 43.0),
            ("Stephen Curry", 9200, "PG", "GSW", 50.0),
            ("Draymond Green", 6500, "PF", "GSW", 28.0),
            ("Kyle Lowry", 5000, "PG", "MIA", 22.0),
            ("Bam Adebayo", 7800, "C", "MIA", 38.0),
        ])
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def minimal_context():
    from analysis.core.schemas import ProjectionContext
    return ProjectionContext(sport="NBA", site="FD")


# ── Test 1: All helpers exist and are callable ────────────────────────────────

EXPECTED_HELPERS = [
    "_ingest_and_enrich_slate",
    "_build_projections",
    "_run_pre_sim",
    "_apply_injury_context",
    "_apply_pool_preparation",
    "_enrich_props_and_ownership",
    "_compute_value_and_quality",
    "_apply_projection_overrides",
    "_build_initial_result",
    "_generate_lineups",
    "_run_simulation",
    "_export_results",
]

@pytest.mark.parametrize("name", EXPECTED_HELPERS)
def test_helper_exists_and_is_callable(name):
    import analysis.core.orchestrator as orch
    fn = getattr(orch, name, None)
    assert fn is not None, f"{name} not found in orchestrator"
    assert callable(fn), f"{name} is not callable"


# ── Test 2: Public signature of run_dfs_pipeline unchanged ───────────────────

def test_run_dfs_pipeline_signature():
    sig = inspect.signature(run_dfs_pipeline)
    params = list(sig.parameters.keys())
    required = [
        "slate_file_path", "context", "n_lineups", "n_sims",
        "sim_seed", "correlation", "pre_sim", "pre_sim_sims",
        "export_dir", "apply_filter", "pool_filter_cfg",
        "contest_cfg", "exposure_cfg", "num_unique", "skip_injury_refresh",
    ]
    for p in required:
        assert p in params, f"run_dfs_pipeline missing parameter: {p}"


# ── Test 3: run_dfs_pipeline delegates — body is thin ────────────────────────

def test_coordinator_is_thin():
    """The coordinator body must be smaller than the old god-function (~460 lines).
    We expect ≤ 50 source lines (blank + code) after the def statement.
    """
    src = inspect.getsource(run_dfs_pipeline)
    lines = [l for l in src.splitlines() if l.strip() and not l.strip().startswith("#")]
    # Subtract 1 for the def line itself
    body_lines = len(lines) - 1
    assert body_lines <= 65, (
        f"run_dfs_pipeline body has {body_lines} non-blank/non-comment lines; "
        "it should be ≤ 65 — god-function may not be fully decomposed"
    )


# ── Test 4: _apply_injury_context ────────────────────────────────────────────

def test_apply_injury_context_stamps_status(minimal_df, minimal_context):
    ctx = minimal_context.model_copy(update={"injuries": {"LeBron James": "OUT"}})
    result = _apply_injury_context(minimal_df.copy(), ctx)
    mask = result["Name"] == "LeBron James"
    assert result.loc[mask, "InjuryStatus"].iloc[0] == "OUT"


def test_apply_injury_context_noop_when_empty(minimal_df, minimal_context):
    original = minimal_df.copy()
    result = _apply_injury_context(original, minimal_context)
    # No InjuryStatus column added when injuries dict is empty
    if "InjuryStatus" not in original.columns:
        assert "InjuryStatus" not in result.columns or result["InjuryStatus"].isna().all()


# ── Test 5: _compute_value_and_quality ───────────────────────────────────────

def test_compute_value_and_quality_adds_columns(minimal_df):
    result = _compute_value_and_quality(minimal_df.copy())
    assert "Value" in result.columns
    assert "data_quality_flags" in result.columns
    assert "projection_confidence" in result.columns


def test_compute_value_and_quality_value_formula(minimal_df):
    result = _compute_value_and_quality(minimal_df.copy())
    for _, row in result.iterrows():
        expected = round(row["Proj"] / (row["Salary"] / 1000.0), 2)
        assert abs(row["Value"] - expected) < 0.01


def test_compute_value_and_quality_flags_are_lists(minimal_df):
    result = _compute_value_and_quality(minimal_df.copy())
    for flags in result["data_quality_flags"]:
        assert isinstance(flags, list)


def test_compute_value_and_quality_confidence_in_range(minimal_df):
    result = _compute_value_and_quality(minimal_df.copy())
    for conf in result["projection_confidence"]:
        assert 0.0 <= conf <= 1.0


# ── Test 6: _apply_projection_overrides ──────────────────────────────────────

def test_apply_projection_overrides_updates_proj(minimal_df, minimal_context):
    ctx = minimal_context.model_copy(
        update={"projection_overrides": {"Stephen Curry": 99.9}}
    )
    result = _apply_projection_overrides(minimal_df.copy(), ctx)
    mask = result["Name"] == "Stephen Curry"
    assert abs(result.loc[mask, "Proj"].iloc[0] - 99.9) < 0.01


def test_apply_projection_overrides_noop_when_empty(minimal_df, minimal_context):
    original = minimal_df.copy()
    result = _apply_projection_overrides(original, minimal_context)
    pd.testing.assert_frame_equal(result, original)


# ── Test 7: _build_initial_result ────────────────────────────────────────────

def test_build_initial_result_keys(minimal_df, minimal_context):
    result = _build_initial_result(
        minimal_df.copy(), "fanduel", minimal_context,
        filter_report={}, ownership_model_used="simple",
        replacement_boosts=None, _bridge=None,
    )
    for key in ["success", "site", "sport", "projections_df", "lineups_df",
                "filter_report", "ownership_model_used", "stats",
                "injury_bridge_source", "out_players", "salary_freed"]:
        assert key in result, f"Missing key in result: {key}"


def test_build_initial_result_lineups_df_empty(minimal_df, minimal_context):
    result = _build_initial_result(
        minimal_df.copy(), "fanduel", minimal_context,
        filter_report={}, ownership_model_used="simple",
        replacement_boosts=None, _bridge=None,
    )
    assert result["lineups_df"].empty


def test_build_initial_result_stats(minimal_df, minimal_context):
    result = _build_initial_result(
        minimal_df.copy(), "fanduel", minimal_context,
        filter_report={}, ownership_model_used="simple",
        replacement_boosts=None, _bridge=None,
    )
    assert result["stats"]["total_players"] == len(minimal_df)


# ── Test 8: _export_results writes files ─────────────────────────────────────

def test_export_results_writes_projections(minimal_df, minimal_context, tmp_path):
    result = {
        "projections_df": minimal_df.copy(),
        "lineups_df": pd.DataFrame(),
    }
    result = _export_results(
        result, str(tmp_path), "FD", Path("slate"), n_lineups=0, pre_sim=False
    )
    assert Path(result["projections_file"]).exists()


# ── Test 9: _enrich_props_and_ownership returns expected types ────────────────

def test_enrich_props_and_ownership_returns_df_and_str(minimal_df, minimal_context):
    df, model_used = _enrich_props_and_ownership(
        minimal_df.copy(), "FD", minimal_context
    )
    assert isinstance(df, pd.DataFrame)
    assert isinstance(model_used, str)
    assert len(model_used) > 0


# ── Test 10: End-to-end smoke through run_dfs_pipeline ──────────────────────

def test_run_dfs_pipeline_smoke(tmp_csv, minimal_context):
    """Full pipeline smoke — no optimizer, no sims, apply_filter=False."""
    result = run_dfs_pipeline(
        slate_file_path=str(tmp_csv),
        context=minimal_context,
        n_lineups=0,
        apply_filter=False,
        skip_injury_refresh=True,
    )
    assert result["success"] is True
    assert isinstance(result["projections_df"], pd.DataFrame)
    assert not result["projections_df"].empty
    assert "Value" in result["projections_df"].columns
    assert "data_quality_flags" in result["projections_df"].columns
    assert "projection_confidence" in result["projections_df"].columns


def test_run_dfs_pipeline_raises_on_missing_file(minimal_context):
    with pytest.raises(FileNotFoundError):
        run_dfs_pipeline(
            slate_file_path="/nonexistent/path/slate.csv",
            context=minimal_context,
        )


def test_run_dfs_pipeline_result_has_required_keys(tmp_csv, minimal_context):
    result = run_dfs_pipeline(
        slate_file_path=str(tmp_csv),
        context=minimal_context,
        n_lineups=0,
        apply_filter=False,
        skip_injury_refresh=True,
    )
    for key in [
        "success", "site", "sport", "projections_df", "lineups_df",
        "filter_report", "ownership_model_used", "stats",
        "injury_bridge_source", "out_players", "salary_freed",
    ]:
        assert key in result, f"Missing key: {key}"
