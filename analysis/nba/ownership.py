"""Simple NBA ownership + leverage model."""

import pandas as pd


def estimate_ownership(proj_df: pd.DataFrame) -> pd.DataFrame:
    """
    Given a projections DataFrame with at least:

        - Proj_Final  (or Base_Proj)
        - Salary      (numeric or string)

    Returns:
        A copy of the DataFrame with new columns:

        - Own_Est        (estimated ownership percentage, 0–60%)
        - Leverage_Score (higher = stronger leverage)
    """

    df = proj_df.copy()

    # Ensure we have a final projection
    if "Proj_Final" not in df.columns:
        if "Base_Proj" in df.columns:
            df["Proj_Final"] = df["Base_Proj"]
        elif "My Proj" in df.columns:
            df["Proj_Final"] = df["My Proj"]
        else:
            df["Proj_Final"] = 0.0

    df["Salary"] = pd.to_numeric(df.get("Salary", 0), errors="coerce").fillna(0)
    df["Proj_Final"] = pd.to_numeric(df["Proj_Final"], errors="coerce").fillna(0.0)

    # Rank 0–1 by projection and salary
    df["proj_rank"] = df["Proj_Final"].rank(pct=True)
    df["sal_rank"] = df["Salary"].rank(pct=True)

    # Weighted score for ownership: high-proj, high-salary -> chalk
    df["own_score"] = 0.6 * df["proj_rank"] + 0.4 * df["sal_rank"]
    max_score = df["own_score"].max()
    if max_score <= 0:
        df["Own_Est"] = 0.0
    else:
        df["Own_Est"] = (df["own_score"] / max_score) * 60.0  # cap ~60%

    # LEVERAGE: projection edge per unit of ownership.
    # Formula: Proj_Final / Own_Est  (higher = more projection per ownership %)
    #
    # A player with 40 pts proj at 5% own  → leverage 8.0  (high edge)
    # A player with 40 pts proj at 80% own → leverage 0.5  (chalk, low edge)
    # A player with 10 pts proj at 3% own  → leverage 3.3  (low proj contrarian)
    #
    # Protects against division-by-zero: Own_Est is floored at 0.5 %.
    safe_own = df["Own_Est"].clip(lower=0.5)
    df["Leverage_Score"] = (df["Proj_Final"] / safe_own).round(3)

    # Drop internal rank columns — they are implementation details, not output.
    df.drop(columns=["proj_rank", "sal_rank", "own_score"], inplace=True, errors="ignore")

    return df
