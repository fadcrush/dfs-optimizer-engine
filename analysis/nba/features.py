import pandas as pd


def add_basic_features(proj_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add baseline DFS features that work for FD + DK immediately.
    This is the starter "intelligence layer" (we'll extend it later with defense/pace/odds).
    """

    df = proj_df.copy()

    # Make numeric where possible
    df["Salary"] = pd.to_numeric(df.get("Salary", None), errors="coerce")
    df["Proj"] = pd.to_numeric(df.get("Proj", None), errors="coerce")
    df["Own"] = pd.to_numeric(df.get("Own", 0), errors="coerce").fillna(0)

    # Value score
    df["Value_per_1k"] = (df["Proj"] / (df["Salary"] / 1000)).round(3)

    # Projection rank
    df["ProjRank"] = df["Proj"].rank(ascending=False, method="min")

    # Ownership rank
    df["OwnRank"] = df["Own"].rank(ascending=False, method="min")

    # Salary tiers (helps later for ownership modeling)
    df["SalaryTier"] = pd.cut(
        df["Salary"],
        bins=[0, 3500, 5000, 6500, 8000, 10000, 20000],
        labels=["min", "punt", "value", "mid", "upper-mid", "stud"]
    )

    return df


# ---- placeholders for future intelligence layers ----
def add_defense_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Later we will inject:
    - Opponent defensive rating
    - DvP style position matchup
    - team pace
    - implied total from odds
    """
    return df


def add_odds_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Later we will inject:
    - spread
    - game total
    - implied team totals
    """
    return df
