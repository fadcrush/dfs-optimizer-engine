"""Defensive matchup adjustments for NBA projections."""

import pandas as pd


def build_defense_table(odds_data: list) -> pd.DataFrame:
    """
    Very simple placeholder:
    - Use game totals & spreads as proxies for defensive environment.
    - Later we combine with real defensive rating from APIs.
    """
    rows = []
    for game in odds_data:
        home = game.get("home_team")
        away = game.get("away_team")
        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") == "totals":
                    outcome = market["outcomes"][0]
                    total = outcome.get("point")
                    rows.append({"home": home, "away": away, "total": total})
                    break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Simple: higher total = weaker combined defenses / faster pace
    # We'll later merge this with team names in your projections.
    return df


def defense_adjustment_for_matchup(team: str, opp: str, defense_df: pd.DataFrame) -> float:
    """
    Returns a multiplier (e.g., 0.9 for tough defense, 1.1 for soft defense).
    For now: crude rule based on game total percentiles.
    """
    if defense_df.empty:
        return 1.0

    mask = ((defense_df["home"] == team) & (defense_df["away"] == opp)) | \
           ((defense_df["home"] == opp) & (defense_df["away"] == team))

    subset = defense_df[mask]
    if subset.empty:
        return 1.0

    total = subset["total"].iloc[0]
    q_low = defense_df["total"].quantile(0.25)
    q_high = defense_df["total"].quantile(0.75)

    if total >= q_high:
        return 1.1  # fast/soft spot
    if total <= q_low:
        return 0.9  # slow/tough spot
    return 1.0
