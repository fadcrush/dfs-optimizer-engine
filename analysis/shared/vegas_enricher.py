"""
Vegas Enrichment
================
Adds team_total, spread, implied_pts, is_home to a projections DataFrame.
Falls back gracefully when the API key is absent or the request fails.

Usage
-----
    from analysis.shared.vegas_enricher import enrich_with_vegas
    df = enrich_with_vegas(df, sport="NBA")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# ------------------------------------------------------------------
# NBA team name → 3-letter abbreviation mapping
# TheOddsAPI uses full city names; our slate uses abbreviations.
# ------------------------------------------------------------------
NBA_NAME_TO_ABBREV: dict[str, str] = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "LA Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}

NFL_NAME_TO_ABBREV: dict[str, str] = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}

LEAGUE_AVG_TEAM_TOTAL: dict[str, float] = {
    "NBA": 112.0,
    "NFL": 24.0,
}


def _get_odds_client() -> Optional[object]:
    api_key = os.getenv("THE_ODDS_API_KEY", "")
    if not api_key:
        log.warning("THE_ODDS_API_KEY not set — Vegas enrichment skipped")
        return None
    try:
        from analysis.shared.api_clients import TheOddsAPIClient
        return TheOddsAPIClient(api_key=api_key)
    except Exception as exc:
        log.warning("Could not import TheOddsAPIClient: %s", exc)
        return None


def _parse_game_totals(games: list[dict], sport: str) -> dict[str, dict]:
    """
    Returns a dict keyed by team abbrev:
      { "LAL": { team_total, spread, is_home, opp_abbrev } }
    """
    name_map = NBA_NAME_TO_ABBREV if sport.upper() == "NBA" else NFL_NAME_TO_ABBREV
    out: dict[str, dict] = {}

    for game in games:
        home_name = game.get("home_team", "")
        away_name = game.get("away_team", "")
        odds = game.get("odds", {})

        total = odds.get("total")
        spread = odds.get("spread")  # home spread (e.g. -4.5 means home favored by 4.5)

        if total is None:
            continue

        spread_val = float(spread) if spread is not None else 0.0
        total_val = float(total)

        # implied = (total ± spread) / 2
        home_implied = (total_val - spread_val) / 2.0
        away_implied = (total_val + spread_val) / 2.0

        home_abbrev = name_map.get(home_name, home_name[:3].upper())
        away_abbrev = name_map.get(away_name, away_name[:3].upper())

        out[home_abbrev] = {
            "team_total": round(home_implied, 2),
            "spread": round(spread_val, 1),
            "is_home": 1,
            "opp_abbrev": away_abbrev,
        }
        out[away_abbrev] = {
            "team_total": round(away_implied, 2),
            "spread": round(-spread_val, 1),
            "is_home": 0,
            "opp_abbrev": home_abbrev,
        }

    return out


def _fetch_team_totals(sport: str) -> dict[str, dict]:
    """Fetch from TheOddsAPI. Returns {} on any failure."""
    client = _get_odds_client()
    if client is None:
        return {}

    try:
        if sport.upper() == "NBA":
            games = client.get_nba_odds()
        elif sport.upper() == "NFL":
            games = getattr(client, "get_nfl_odds", lambda: [])()
        else:
            log.info("Vegas enrichment not supported for sport: %s", sport)
            return {}

        parsed = _parse_game_totals(games, sport)
        log.info("Vegas enrichment: fetched totals for %d teams", len(parsed))
        return parsed
    except Exception as exc:
        log.warning("Vegas enrichment failed: %s", exc)
        return {}


def enrich_with_vegas(df: pd.DataFrame, sport: str = "NBA") -> pd.DataFrame:
    """
    Add Vegas-derived columns to a projections DataFrame.

    Adds
    ----
    team_total   : implied team total points (float, NaN if unavailable)
    spread       : point spread from home team's perspective
    is_home      : 1=home, 0=away
    Vegas_Boost  : projection multiplier based on team total vs league avg
    implied_pts  : same as team_total (alias for downstream compatibility)

    Parameters
    ----------
    df    : DataFrame with at least a ``Team`` column
    sport : "NBA" or "NFL"

    Returns
    -------
    Enriched DataFrame (copy).
    """
    df = df.copy()
    league_avg = LEAGUE_AVG_TEAM_TOTAL.get(sport.upper(), 112.0)

    # Initialize columns with NaN so downstream can detect unavailability
    for col in ("team_total", "spread", "is_home", "implied_pts", "Vegas_Boost"):
        if col not in df.columns:
            df[col] = float("nan")

    team_col = next((c for c in ["Team", "team", "TEAM"] if c in df.columns), None)
    if team_col is None:
        log.warning("No Team column found — Vegas enrichment columns set to NaN")
        return df

    totals = _fetch_team_totals(sport)
    if not totals:
        log.info("No Vegas data available — columns remain NaN")
        return df

    for idx, row in df.iterrows():
        team = str(row[team_col]).strip().upper()
        info = totals.get(team)
        if info is None:
            continue
        tt = info["team_total"]
        df.at[idx, "team_total"] = tt
        df.at[idx, "implied_pts"] = tt
        df.at[idx, "spread"] = info["spread"]
        df.at[idx, "is_home"] = float(info["is_home"])
        # Boost: 0.8% per point above league average
        boost = 1.0 + 0.008 * (tt - league_avg)
        df.at[idx, "Vegas_Boost"] = round(boost, 4)

    enriched = df["team_total"].notna().sum()
    total = len(df)
    log.info("Vegas enrichment applied to %d / %d players", enriched, total)
    return df
