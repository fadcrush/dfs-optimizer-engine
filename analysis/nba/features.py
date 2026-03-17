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


def add_defense_features(
    df: pd.DataFrame,
    site: str = "DK",
    lookback_days: int = 30,
) -> pd.DataFrame:
    """Apply Defense-vs-Player (DvP) multipliers from game-log data.

    Looks up each player's opponent (``Opp`` column) in the DvP table built
    from ``dfs_edge.duckdb`` and multiplies ``Proj`` accordingly.  Adds a
    ``DvP`` column showing the applied multiplier.  If no DB is available or
    no ``Opp`` column exists the dataframe is returned unchanged.

    Args:
        df:            Projections DataFrame with at least ``Proj`` and ``Opp``.
        site:          ``"DK"`` or ``"FD"`` — which fantasy scoring to use.
        lookback_days: Calendar days of game-log history to include.
    """
    from analysis.nba.dvp import load_dvp_table

    if "Proj" not in df.columns:
        return df

    opp_col = next((c for c in ["Opp", "opp", "Opponent"] if c in df.columns), None)
    if opp_col is None:
        return df

    dvp_table = load_dvp_table(site=site, lookback_days=lookback_days)
    if not dvp_table:
        df["DvP"] = 1.0
        return df

    multipliers = df[opp_col].map(lambda t: dvp_table.get(str(t).strip(), 1.0))
    df = df.copy()
    df["DvP"] = multipliers.round(4)
    df["Proj"] = (df["Proj"] * multipliers).round(4)
    return df


def add_odds_features(
    df: pd.DataFrame,
    game_totals: dict[str, float] | None = None,
    max_adj: float = 0.05,
) -> pd.DataFrame:
    """Apply Vegas game-total pace adjustments to projections.

    Uses the ``Team`` column to look up each player's game total, then applies
    a small multiplier: high totals (loose implied scoring) nudge projections
    up, low totals nudge them down.  The adjustment is capped at ±``max_adj``
    (default ±5 %) to avoid over-fitting a single number.

    Args:
        df:           Projections DataFrame with ``Proj`` and ``Team`` columns.
        game_totals:  Dict mapping team abbreviation → game O/U total
                      e.g. ``{"BOS": 224.5, "MIA": 224.5, ...}``.
                      If ``None`` or empty the dataframe is returned unchanged.
        max_adj:      Maximum fractional adjustment (0.05 → ±5 %).
    """
    if "Proj" not in df.columns or not game_totals:
        return df

    team_col = next((c for c in ["Team", "team"] if c in df.columns), None)
    if team_col is None:
        return df

    all_totals = list(game_totals.values())
    if not all_totals:
        return df

    import statistics
    median_total = statistics.median(all_totals)

    def _pace_mult(team: str) -> float:
        total = game_totals.get(str(team).strip())
        if total is None or median_total == 0:
            return 1.0
        raw = total / median_total
        return max(1.0 - max_adj, min(1.0 + max_adj, raw))

    df = df.copy()
    mults = df[team_col].map(_pace_mult)
    df["GameTotal"] = df[team_col].map(lambda t: game_totals.get(str(t).strip(), None))
    df["PaceMult"] = mults.round(4)
    df["Proj"] = (df["Proj"] * mults).round(4)
    return df
