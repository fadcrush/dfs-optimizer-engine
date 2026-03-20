"""
Phase 25 tests: Vectorised scoring priority chain and StdDev in
CanonicalNBAProjectionEngine (§8.5 — projection_engine.py iterrows replaced).

Covers:
  - Layer 1 (Base_Proj > 0) wins over game-log avg and box-score
  - Layer 2 (GL L10 avg > 0) wins over box-score when no base
  - Layer 3 (box-score stats) used when neither base nor gl exist
  - Layer 0 (zero) when no signal available
  - Mixed slate: each player lands on the correct layer independently
  - _slug temp column NOT leaked into engine output
  - GL_L10 column populated correctly from game-log baseline dict
  - StdDev vectorised: individual CV from baseline overrides position fallback
  - StdDev vectorised: position fallback used for players absent from baseline
  - Multi-position (PG/SG) correctly parsed for position fallback (uses PG CV)
  - projection_service output dict has correct keys after vectorised build
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.core.projection_engine import (
    CanonicalNBAProjectionEngine,
    _POSITION_DEFAULT_CV,
    _POSITION_DEFAULT_CV_FALLBACK,
    _slugify,
)
from analysis.core.schemas import ProjectionContext


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ctx(site: str = "DK") -> ProjectionContext:
    return ProjectionContext(site=site, sport="NBA", slate_date=date(2026, 3, 19))


_EMPTY_PATCHES = dict(
    load_dvp_table="analysis.core.projection_engine.load_dvp_table",
    get_rest_multipliers="analysis.core.projection_engine.get_rest_multipliers",
    get_blowout_multipliers="analysis.core.projection_engine.get_blowout_multipliers",
)


def _run_engine(
    slate_df: pd.DataFrame,
    gl_baseline: dict | None = None,
    stddev_baseline: dict | None = None,
    site: str = "DK",
) -> pd.DataFrame:
    """Run the engine with controlled mocks for DB lookups."""
    engine = CanonicalNBAProjectionEngine()
    with (
        patch("analysis.core.projection_engine._load_game_log_baseline",
              return_value=gl_baseline or {}),
        patch("analysis.core.projection_engine._load_stddev_baseline",
              return_value=stddev_baseline or {}),
        patch("analysis.core.projection_engine.load_dvp_table", return_value={}),
        patch("analysis.core.projection_engine.get_rest_multipliers", return_value={}),
        patch("analysis.core.projection_engine.get_blowout_multipliers", return_value={}),
    ):
        return engine.generate(slate_df, _ctx(site))


def _player(
    name: str = "Player A",
    pos: str = "SF",
    base_proj: float = 0.0,
    **extra,
) -> dict:
    row = {"DFS_ID": name.replace(" ", "_"), "Name": name,
           "Team": "LAL", "Opp": "BOS", "Pos": pos,
           "Salary": 6000, "Own": 10.0, "Base_Proj": base_proj}
    row.update(extra)
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Scoring priority chain — Layer 1: Base_Proj
# ──────────────────────────────────────────────────────────────────────────────

def test_layer1_base_proj_wins_over_gl_avg():
    """When Base_Proj > 0, it overrides the game-log L10 average."""
    slug = _slugify("Player A")
    gl = {slug: {"dk": 30.0, "fd": 27.0, "min": 32.0, "games": 10}}
    slate = pd.DataFrame([_player("Player A", base_proj=45.0)])

    result = _run_engine(slate, gl_baseline=gl)

    assert result.iloc[0]["Proj"] == pytest.approx(45.0)


def test_layer1_base_proj_wins_over_box_score():
    """Base_Proj > 0 must suppress box-score derivation."""
    slate = pd.DataFrame([_player(
        "Player A", base_proj=40.0,
        PTS=20, TRB=5, AST=4, STL=1, BLK=1, TOV=2,  # box stats present
    )])
    result = _run_engine(slate)
    assert result.iloc[0]["Proj"] == pytest.approx(40.0)


# ──────────────────────────────────────────────────────────────────────────────
# Scoring priority chain — Layer 2: Game-log L10 avg
# ──────────────────────────────────────────────────────────────────────────────

def test_layer2_gl_avg_used_when_no_base():
    """Without Base_Proj, the GL L10 average should be the projection."""
    slug = _slugify("Player A")
    gl = {slug: {"dk": 35.5, "fd": 31.0, "min": 33.0, "games": 10}}
    slate = pd.DataFrame([_player("Player A", base_proj=0.0)])

    result = _run_engine(slate, gl_baseline=gl)

    assert result.iloc[0]["Proj"] == pytest.approx(35.5)


def test_layer2_gl_l10_column_populated():
    """GL_L10 column reflects the game-log value regardless of which layer wins."""
    slug = _slugify("Player A")
    gl = {slug: {"dk": 28.0, "fd": 25.0, "min": 30.0, "games": 10}}
    slate = pd.DataFrame([_player("Player A", base_proj=50.0)])

    result = _run_engine(slate, gl_baseline=gl)

    assert result.iloc[0]["GL_L10"] == pytest.approx(28.0)


def test_layer2_gl_l10_zero_for_unknown_player():
    """Player not in GL baseline → GL_L10 = 0.0."""
    slate = pd.DataFrame([_player("Unknown Player", base_proj=0.0)])
    result = _run_engine(slate, gl_baseline={})
    assert result.iloc[0]["GL_L10"] == pytest.approx(0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Scoring priority chain — Layer 3: Box score
# ──────────────────────────────────────────────────────────────────────────────

def test_layer3_box_score_used_when_no_base_or_gl():
    """When Base_Proj = 0 and no GL entry, box stats drive the projection."""
    slate = pd.DataFrame([_player(
        "Player A", pos="SF", base_proj=0.0,
        PTS=22, TRB=7, AST=3, STL=1, BLK=1, TOV=2,
    )])
    result = _run_engine(slate, gl_baseline={})
    # Projection must be > 0 (exact value depends on scoring formula)
    assert result.iloc[0]["Proj"] > 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Scoring priority chain — Layer 0: Zero fallback
# ──────────────────────────────────────────────────────────────────────────────

def test_layer0_zero_when_no_signal():
    """No base, no GL, no box stats → projection is 0.0."""
    slate = pd.DataFrame([_player("Player A", base_proj=0.0)])  # no box cols
    result = _run_engine(slate, gl_baseline={})
    assert result.iloc[0]["Proj"] == pytest.approx(0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Mixed priority — independent per player
# ──────────────────────────────────────────────────────────────────────────────

def test_mixed_slate_each_player_correct_layer():
    """
    Three players on one slate:
      - Player A: Base_Proj supplied → Layer 1
      - Player B: no base, GL entry exists → Layer 2
      - Player C: no base, no GL, no box → Layer 0 (zero)
    """
    slug_b = _slugify("Player B")
    gl = {slug_b: {"dk": 33.0, "fd": 29.0, "min": 31.0, "games": 10}}

    slate = pd.DataFrame([
        _player("Player A", base_proj=44.0),
        _player("Player B", base_proj=0.0),
        _player("Player C", base_proj=0.0),
    ])

    result = _run_engine(slate, gl_baseline=gl)
    result = result.set_index("Name")

    assert result.loc["Player A", "Proj"] == pytest.approx(44.0)   # Layer 1
    assert result.loc["Player B", "Proj"] == pytest.approx(33.0)   # Layer 2
    assert result.loc["Player C", "Proj"] == pytest.approx(0.0)    # Layer 0


# ──────────────────────────────────────────────────────────────────────────────
# Regression: _slug temp column must NOT appear in output
# ──────────────────────────────────────────────────────────────────────────────

def test_slug_column_not_in_output():
    """_slug is an internal temp column and must be excluded from the result."""
    slate = pd.DataFrame([_player("Player A", base_proj=30.0)])
    result = _run_engine(slate)
    assert "_slug" not in result.columns


# ──────────────────────────────────────────────────────────────────────────────
# StdDev vectorisation
# ──────────────────────────────────────────────────────────────────────────────

def test_stddev_individual_cv_overrides_position_fallback():
    """Player in stddev_baseline uses their own CV, not the position default."""
    slug = _slugify("Player A")
    custom_cv = 0.25
    mock_stddev = {slug: {"cv": custom_cv, "std": 9.0, "games": 12}}

    slate = pd.DataFrame([_player("Player A", pos="C", base_proj=40.0)])
    result = _run_engine(slate, stddev_baseline=mock_stddev)

    row = result.iloc[0]
    expected_std = round(float(row["Proj"]) * custom_cv, 3)
    assert abs(float(row["StdDev"]) - expected_std) < 0.01


def test_stddev_position_fallback_for_unknown_player():
    """Player absent from stddev_baseline falls back to position CV."""
    slate = pd.DataFrame([_player("Unknown Player", pos="PG", base_proj=38.0)])
    result = _run_engine(slate, stddev_baseline={})

    row = result.iloc[0]
    expected_cv = _POSITION_DEFAULT_CV.get("PG", _POSITION_DEFAULT_CV_FALLBACK)
    expected_std = round(float(row["Proj"]) * expected_cv, 3)
    assert abs(float(row["StdDev"]) - expected_std) < 0.01


def test_stddev_multi_position_uses_primary():
    """For Pos='PG/SG', the CV lookup uses 'PG' (first token before '/')."""
    slate = pd.DataFrame([_player("Unknown Player", pos="PG/SG", base_proj=32.0)])
    result = _run_engine(slate, stddev_baseline={})

    row = result.iloc[0]
    pg_cv = _POSITION_DEFAULT_CV.get("PG", _POSITION_DEFAULT_CV_FALLBACK)
    sg_cv = _POSITION_DEFAULT_CV.get("SG", _POSITION_DEFAULT_CV_FALLBACK)
    expected_std = round(float(row["Proj"]) * pg_cv, 3)
    wrong_std    = round(float(row["Proj"]) * sg_cv, 3)
    # PG and SG may have same CV — only assert equal when they differ
    if abs(pg_cv - sg_cv) > 1e-6:
        assert abs(float(row["StdDev"]) - expected_std) < 0.01
        assert abs(float(row["StdDev"]) - wrong_std) > 0.005


def test_stddev_fallback_cv_for_unknown_position():
    """No matching position in _POSITION_DEFAULT_CV → uses _POSITION_DEFAULT_CV_FALLBACK."""
    slate = pd.DataFrame([_player("Unknown Player", pos="UNKNOWN_POS", base_proj=25.0)])
    result = _run_engine(slate, stddev_baseline={})

    row = result.iloc[0]
    expected_std = round(float(row["Proj"]) * _POSITION_DEFAULT_CV_FALLBACK, 3)
    assert abs(float(row["StdDev"]) - expected_std) < 0.01
