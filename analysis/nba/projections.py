"""
DEPRECATED — this module has broken imports and is not used by the production pipeline.
The canonical projection path is:

    analysis.core.orchestrator.run_dfs_pipeline
    → analysis.core.projection_engine.CanonicalNBAProjectionEngine
    → analysis.shared.scoring.score_nba_row

Do NOT import from this module.  It will be removed in a future cleanup pass.
"""

import pandas as pd
try:
    from apis.nba.nba_api_client import get_nba_odds  # type: ignore[import]
except ImportError:
    get_nba_odds = None  # type: ignore[assignment]  # dead module — import guard
from .defense import build_defense_table, defense_adjustment_for_matchup


def build_baseline_from_slate(slate_df: pd.DataFrame) -> pd.DataFrame:
    """
    DEPRECATED — use analysis.core.orchestrator.run_dfs_pipeline() instead.
    Phase 3 gate: this function must never be called in production.
    """
    raise RuntimeError(
        "DEPRECATED: analysis.nba.projections.build_baseline_from_slate() is not "
        "part of the production pipeline.  Use "
        "analysis.core.orchestrator.run_dfs_pipeline() instead."
    )
    df = slate_df.copy()  # unreachable — kept to avoid linter removing arg

    # Make sure columns exist
    col_map = {}
    if "My Proj" in df.columns:
        col_map["My Proj"] = "Base_Proj"
    elif "SS Proj" in df.columns:
        col_map["SS Proj"] = "Base_Proj"

    if col_map:
        df.rename(columns=col_map, inplace=True)
    else:
        df["Base_Proj"] = 0.0  # placeholder if nothing exists

    needed = ["Name", "Team", "Opp", "Salary", "Base_Proj"]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    return df[needed + [c for c in df.columns if c not in needed]]


def apply_defensive_adjustments(baseline_df: pd.DataFrame) -> pd.DataFrame:
    """DEPRECATED — raises RuntimeError. Use run_dfs_pipeline()."""
    raise RuntimeError(
        "DEPRECATED: analysis.nba.projections.apply_defensive_adjustments() is not "
        "part of the production pipeline.  Use "
        "analysis.core.orchestrator.run_dfs_pipeline() instead."
    )
    odds = get_nba_odds()  # unreachable
    defense_df = build_defense_table(odds)
    df = baseline_df.copy()

    df["Defense_Mult"] = df.apply(
        lambda r: defense_adjustment_for_matchup(r.get("Team"), r.get("Opp"), defense_df),
        axis=1,
    )
    df["Proj_Final"] = df["Base_Proj"] * df["Defense_Mult"]
    return df


def generate_nba_projections_from_slate(slate_df: pd.DataFrame) -> pd.DataFrame:
    """
    DEPRECATED — raises RuntimeError. Use run_dfs_pipeline().

    Phase 3 gate: this function is the canonical entry-point guard.
    Any call here means a caller bypassed the canonical engine.
    """
    raise RuntimeError(
        "DEPRECATED: analysis.nba.projections.generate_nba_projections_from_slate() "
        "is not part of the production pipeline.  Use "
        "analysis.core.orchestrator.run_dfs_pipeline() instead."
    )
    base = build_baseline_from_slate(slate_df)  # unreachable
    adjusted = apply_defensive_adjustments(base)
    return adjusted
