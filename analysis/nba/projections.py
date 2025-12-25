"""Custom NBA projection engine combining:
- baseline per-minute production
- expected minutes
- defensive matchup adjustment
"""

import pandas as pd
from apis.nba.nba_api_client import get_nba_odds
from .defense import build_defense_table, defense_adjustment_for_matchup


def build_baseline_from_slate(slate_df: pd.DataFrame) -> pd.DataFrame:
    """
    Start from your existing projection slate CSV (like SS/My Proj etc.)
    and standardize columns to:
    - Name, Team, Opp, Salary, Base_Proj
    """
    df = slate_df.copy()

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
    """Apply game-total-based defensive multipliers to Base_Proj."""
    odds = get_nba_odds()
    defense_df = build_defense_table(odds)
    df = baseline_df.copy()

    multipliers = []
    for _, row in df.iterrows():
        team = row.get("Team")
        opp = row.get("Opp")
        mult = defense_adjustment_for_matchup(team, opp, defense_df)
        multipliers.append(mult)

    df["Defense_Mult"] = multipliers
    df["Proj_Final"] = df["Base_Proj"] * df["Defense_Mult"]
    return df


def generate_nba_projections_from_slate(slate_df: pd.DataFrame) -> pd.DataFrame:
    """
    High-level entry:

    1) Start from your slate/projection CSV.
    2) Build a normalized baseline.
    3) Apply defensive/pace adjustments.
    """
    base = build_baseline_from_slate(slate_df)
    adjusted = apply_defensive_adjustments(base)
    return adjusted
