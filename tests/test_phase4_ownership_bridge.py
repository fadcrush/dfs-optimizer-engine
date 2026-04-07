"""
Phase 4 Gate Tests — Ownership Bridge
=======================================

Verifies that the four Phase 4 contracts hold:

  1. ``apply_ownership_bridge`` returns a DataFrame with ``Own``,
     ``Own_Est``, and ``own_source`` columns present.

  2. The ``OwnershipBridgeResult.source`` matches the intended mode:
     - mode="simple"   → source="simple"
     - mode="weighted" → source="weighted"
     - mode="ml"       → source="ml" (or graceful fallback)
     - all models fail → source="none"

  3. ``Own`` values are within [0, 100] (valid ownership %).

  4. Non-NBA sports pass through unchanged with source="none".

  5. Orchestrator no longer contains the inline closure ownership
     block — the bridge import is present and the closure functions
     are gone.

  6. After the bridge, ``compute_leverage_scores`` succeeds
     (Own column is present and numeric).

  7. ``_normalise_own_column`` correctly aliases Own ↔ Own_Est
     when only one of them exists.

These are *pure-unit* tests — no live DB, no HTTP layer.
"""
from __future__ import annotations

import importlib
import inspect
import re
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from analysis.core.ownership_bridge import (
    OwnershipBridgeResult,
    apply_ownership_bridge,
    _normalise_own_column,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _base_df(n: int = 5) -> pd.DataFrame:
    """Minimal projections DataFrame sufficient for all ownership models."""
    return pd.DataFrame(
        {
            "Name": [f"Player {i}" for i in range(n)],
            "DFS_ID": [f"P{i}" for i in range(n)],
            "Proj": [30.0 + i * 2 for i in range(n)],
            "Salary": [7000 + i * 300 for i in range(n)],
            "InjuryStatus": [""] * n,
        }
    )


def _mock_weighted_fn(df: pd.DataFrame, site: str = "DK") -> pd.DataFrame:
    """Simulate a successful weighted model call."""
    out = df.copy()
    out["Own_Est"] = 15.0
    out["own_source"] = "weighted"
    out["ownership_bucket"] = "medium"
    return out


def _mock_simple_fn(df: pd.DataFrame) -> pd.DataFrame:
    """Simulate a successful simple model call."""
    out = df.copy()
    out["Own_Est"] = 10.0
    # simple model does NOT set "own_source" — bridge must handle this
    return out


# ── Contract 1: output columns ─────────────────────────────────────────────

def test_bridge_output_has_own_column_simple():
    """Bridge must add Own column even when simple model only sets Own_Est."""
    df = _base_df()
    with patch("analysis.nba.ownership.estimate_ownership", side_effect=_mock_simple_fn):
        enriched, result = apply_ownership_bridge(df, mode="simple", sport="NBA")
    assert "Own" in enriched.columns
    assert "Own_Est" in enriched.columns
    assert "own_source" in enriched.columns


def test_bridge_output_has_own_column_weighted():
    """Bridge must add Own column when weighted model only sets Own_Est."""
    df = _base_df()
    with patch(
        "analysis.nba.ownership_weighted.estimate_ownership_weighted",
        side_effect=_mock_weighted_fn,
    ):
        enriched, result = apply_ownership_bridge(df, mode="weighted", sport="NBA")
    assert "Own" in enriched.columns
    assert "Own_Est" in enriched.columns
    assert "own_source" in enriched.columns


# ── Contract 2: source string ──────────────────────────────────────────────

def test_source_simple_mode():
    df = _base_df()
    with patch("analysis.nba.ownership.estimate_ownership", side_effect=_mock_simple_fn):
        _, result = apply_ownership_bridge(df, mode="simple", sport="NBA")
    assert result.source == "simple"
    assert result.model_available is True


def test_source_weighted_mode():
    df = _base_df()
    with patch(
        "analysis.nba.ownership_weighted.estimate_ownership_weighted",
        side_effect=_mock_weighted_fn,
    ):
        _, result = apply_ownership_bridge(df, mode="weighted", sport="NBA")
    assert result.source == "weighted"
    assert result.model_available is True


def test_source_all_models_fail_returns_none():
    """When all three models raise, source must be 'none'."""
    df = _base_df()
    with (
        patch("analysis.nba.ownership_v2.predict_ownership", side_effect=RuntimeError("no model")),
        patch(
            "analysis.nba.ownership_weighted.estimate_ownership_weighted",
            side_effect=RuntimeError("no model"),
        ),
        patch("analysis.nba.ownership.estimate_ownership", side_effect=RuntimeError("no model")),
    ):
        enriched, result = apply_ownership_bridge(df, mode="auto", sport="NBA")
    assert result.source == "none"
    assert result.model_available is False
    assert "Own" in enriched.columns  # graceful — zeroed columns present


def test_source_none_when_non_nba():
    """Non-NBA sports must get source='none' with no model invoked."""
    df = _base_df()
    enriched, result = apply_ownership_bridge(df, mode="auto", sport="NFL")
    assert result.source == "none"
    assert result.model_available is False


# ── Contract 3: Own values in [0, 100] ─────────────────────────────────────

def test_own_values_in_valid_range_weighted():
    df = _base_df()
    with patch(
        "analysis.nba.ownership_weighted.estimate_ownership_weighted",
        side_effect=_mock_weighted_fn,
    ):
        enriched, _ = apply_ownership_bridge(df, mode="weighted", sport="NBA")
    assert enriched["Own"].between(0, 100).all(), "Own column has out-of-range values"


def test_own_values_in_valid_range_simple():
    df = _base_df()
    with patch("analysis.nba.ownership.estimate_ownership", side_effect=_mock_simple_fn):
        enriched, _ = apply_ownership_bridge(df, mode="simple", sport="NBA")
    assert enriched["Own"].between(0, 100).all()


def test_own_values_zeroed_on_all_fail():
    """Graceful degradation: Own column = 0.0 when no model succeeds."""
    df = _base_df()
    with (
        patch("analysis.nba.ownership_v2.predict_ownership", side_effect=RuntimeError),
        patch(
            "analysis.nba.ownership_weighted.estimate_ownership_weighted",
            side_effect=RuntimeError,
        ),
        patch("analysis.nba.ownership.estimate_ownership", side_effect=RuntimeError),
    ):
        enriched, _ = apply_ownership_bridge(df, mode="auto", sport="NBA")
    assert (enriched["Own"] == 0.0).all()


# ── Contract 4: non-NBA pass-through ───────────────────────────────────────

def test_non_nba_df_unchanged_except_own_columns():
    """NFL df must come back with Own/Own_Est columns (defaults to 0) but unchanged otherwise."""
    df = pd.DataFrame({"Name": ["A", "B"], "Proj": [20.0, 30.0], "Salary": [6000, 8000]})
    enriched, result = apply_ownership_bridge(df, mode="auto", sport="NFL")
    assert result.source == "none"
    # Original rows preserved
    assert list(enriched["Name"]) == ["A", "B"]
    assert "Own" in enriched.columns


# ── Contract 5: orchestrator no longer contains inline closures ────────────

def test_orchestrator_uses_bridge_import():
    """ownership_bridge must be imported at the top of orchestrator."""
    import analysis.core.orchestrator as orch_mod
    src = inspect.getsource(orch_mod)
    assert "from .ownership_bridge import" in src, \
        "orchestrator.py must import from .ownership_bridge"


def test_orchestrator_no_inline_closure_functions():
    """The inline _apply_ml_ownership / _apply_weighted_ownership / _apply_simple_ownership
    closure definitions must NOT exist in orchestrator.py (they are now inside the bridge)."""
    import analysis.core.orchestrator as orch_mod
    src = inspect.getsource(orch_mod)
    for fn_name in ("_apply_ml_ownership", "_apply_weighted_ownership", "_apply_simple_ownership"):
        assert fn_name not in src, \
            f"orchestrator.py still contains inline closure '{fn_name}'; move to ownership_bridge"


# ── Contract 6: compute_leverage_scores works after bridge ─────────────────

def test_leverage_scores_succeed_after_bridge():
    """compute_leverage_scores must run without error on bridge output."""
    from analysis.core.exposure_optimizer import ExposureConfig, compute_leverage_scores
    df = _base_df()
    with patch(
        "analysis.nba.ownership_weighted.estimate_ownership_weighted",
        side_effect=_mock_weighted_fn,
    ):
        enriched, _ = apply_ownership_bridge(df, mode="weighted", sport="NBA")
    cfg = ExposureConfig(global_max=0.60)
    result = compute_leverage_scores(enriched, cfg)
    assert "Leverage" in result.columns
    assert "FieldOwn" in result.columns


def test_leverage_scores_succeed_after_bridge_all_fail():
    """compute_leverage_scores must also succeed when bridge returns zeroed Own."""
    from analysis.core.exposure_optimizer import ExposureConfig, compute_leverage_scores
    df = _base_df()
    with (
        patch("analysis.nba.ownership_v2.predict_ownership", side_effect=RuntimeError),
        patch(
            "analysis.nba.ownership_weighted.estimate_ownership_weighted",
            side_effect=RuntimeError,
        ),
        patch("analysis.nba.ownership.estimate_ownership", side_effect=RuntimeError),
    ):
        enriched, _ = apply_ownership_bridge(df, mode="auto", sport="NBA")
    cfg = ExposureConfig(global_max=0.60)
    result = compute_leverage_scores(enriched, cfg)
    assert "Leverage" in result.columns


# ── Contract 7: _normalise_own_column ──────────────────────────────────────

def test_normalise_creates_own_from_own_est():
    df = pd.DataFrame({"Own_Est": [10.0, 20.0]})
    out = _normalise_own_column(df)
    assert list(out["Own"]) == [10.0, 20.0]


def test_normalise_creates_own_est_from_own():
    df = pd.DataFrame({"Own": [15.0, 25.0]})
    out = _normalise_own_column(df)
    assert list(out["Own_Est"]) == [15.0, 25.0]


def test_normalise_creates_zeros_when_neither_exists():
    df = pd.DataFrame({"Name": ["A"]})
    out = _normalise_own_column(df)
    assert "Own" in out.columns
    assert "Own_Est" in out.columns
    assert out["Own"].iloc[0] == 0.0


def test_normalise_does_not_override_existing_own():
    """When both columns already exist, they must not be mutated."""
    df = pd.DataFrame({"Own": [50.0], "Own_Est": [48.0]})
    out = _normalise_own_column(df)
    assert out["Own"].iloc[0] == 50.0
    assert out["Own_Est"].iloc[0] == 48.0


# ── Auto cascade order ──────────────────────────────────────────────────────

def test_auto_tries_ml_first_then_weighted():
    """In auto mode, if ML fails, the bridge must fall through to weighted."""
    df = _base_df()
    with (
        patch("analysis.nba.ownership_v2.predict_ownership", side_effect=RuntimeError("no model")),
        patch(
            "analysis.nba.ownership_weighted.estimate_ownership_weighted",
            side_effect=_mock_weighted_fn,
        ),
    ):
        _, result = apply_ownership_bridge(df, mode="auto", sport="NBA")
    assert result.source == "weighted"


def test_auto_falls_to_simple_when_ml_and_weighted_fail():
    df = _base_df()
    with (
        patch("analysis.nba.ownership_v2.predict_ownership", side_effect=RuntimeError),
        patch(
            "analysis.nba.ownership_weighted.estimate_ownership_weighted",
            side_effect=RuntimeError,
        ),
        patch("analysis.nba.ownership.estimate_ownership", side_effect=_mock_simple_fn),
    ):
        _, result = apply_ownership_bridge(df, mode="auto", sport="NBA")
    assert result.source == "simple"
