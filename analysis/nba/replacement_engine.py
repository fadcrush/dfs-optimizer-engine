"""
NBA Replacement Engine
=======================
Data-driven boost computation for teammates of injured/OUT players.

Algorithm
---------
For each OUT player P on team T:
  1. Find all game_dates where P has 0 minutes (absent games).
  2. Find all game_dates where P has > 0 minutes (active games).
  3. For every teammate on T, compute:
       delta_minutes = avg_minutes(absent games) - avg_minutes(active games)
  4. Convert delta_minutes → delta_DK using that teammate's per-minute rate.
  5. Return teammates with positive delta as proj_boost candidates.

Requires player_game_logs table in data/dfs_edge.duckdb (built by ingest_game_logs.py).
Falls back to a flat MINUTES_BOOST_FALLBACK if the table is unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
from analysis.shared.db import get_conn

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "dfs_edge.duckdb"

log = logging.getLogger(__name__)

# Flat fallback when no historical data available
MINUTES_BOOST_FALLBACK = 3.0        # extra minutes per OUT teammate
DK_PER_MINUTE_FALLBACK = 1.15       # average DK pts/min for a starter
MIN_GAMES_REQUIRED = 5              # minimum absent games for a reliable estimate
MIN_DELTA_MINUTES = 1.5             # ignore micro-bumps below this threshold
LOOKBACK_GAMES = 25                 # how many of the OUT player's most recent games to use


@dataclass
class CompoundingConfig:
    """Controls diminishing-returns scaling when multiple players are OUT on the same team."""
    diminishing_factor: float = 0.75   # each subsequent OUT player's marginal boost multiplied by this
    max_compound_boost: float = 15.0   # cap total boost per player to prevent runaway
    min_out_for_compound: int = 2      # only apply compounding when >= 2 are out on same team


@dataclass
class ReplacementResult:
    out_player: str
    team: str
    absent_games: int
    boosts: pd.DataFrame          # columns: player_name, team, delta_minutes, dk_rate, proj_boost
    used_fallback: bool = False


def _db_available() -> bool:
    return DB_PATH.exists()


def _get_team_for_player(player_name: str, con: duckdb.DuckDBPyConnection) -> Optional[str]:
    """Look up the most recent team for a player."""
    row = con.execute("""
        SELECT team FROM player_game_logs
        WHERE LOWER(player_name) = LOWER(?)
        ORDER BY game_date DESC
        LIMIT 1
    """, [player_name]).fetchone()
    return row[0] if row else None


def compute_boosts(
    out_players: list[str],
    slate_players: pd.DataFrame | None = None,
    min_games: int = MIN_GAMES_REQUIRED,
    lookback: int = LOOKBACK_GAMES,
    compounding: CompoundingConfig | None = None,
) -> pd.DataFrame:
    """
    Main entry point. Given a list of OUT player names, return a DataFrame
    of teammates with projected minutes/DK boosts.

    Parameters
    ----------
    out_players   : list of OUT player names (as they appear on the slate)
    slate_players : DataFrame with current slate (used to filter to targeted players only)
                    Must have 'Name' or 'player_name' column.
    min_games     : minimum absent-game samples required to trust the estimate
    lookback      : how many total games per player to consider
    compounding   : config for diminishing-returns when multiple players out on same team

    Returns
    -------
    DataFrame with columns:
        player_name, team, out_player, delta_minutes, dk_rate, proj_boost
    """
    if compounding is None:
        compounding = CompoundingConfig()

    if not out_players:
        return pd.DataFrame(columns=["player_name", "team", "out_player", "delta_minutes", "dk_rate", "proj_boost"])

    if not _db_available():
        log.warning("dfs_edge.duckdb not found – using flat fallback boost")
        return _fallback_boosts(out_players, slate_players)

    try:
        con = get_conn(DB_PATH)
        all_boosts = []
        # Track which team each OUT player belongs to for compounding
        out_player_teams: dict[str, str] = {}
        for player in out_players:
            result = _compute_for_player(player, con, min_games, lookback)
            if result and result.team:
                out_player_teams[player] = result.team
            if result and not result.boosts.empty:
                result.boosts["out_player"] = result.out_player
                all_boosts.append(result.boosts)
            elif result and result.used_fallback:
                fb = _fallback_boosts([player], slate_players, result.team)
                if not fb.empty:
                    fb["out_player"] = player
                    all_boosts.append(fb)
    except Exception as exc:
        log.warning("Replacement engine DB error: %s – using fallback", exc)
        return _fallback_boosts(out_players, slate_players)

    if not all_boosts:
        return pd.DataFrame(columns=["player_name", "team", "out_player", "delta_minutes", "dk_rate", "proj_boost"])

    combined = pd.concat(all_boosts, ignore_index=True)

    # If a player absorbs boosts from multiple OUT players, sum them
    combined = (
        combined.groupby(["player_name", "team"], as_index=False)
        .agg(
            out_player=("out_player", lambda x: ", ".join(x.unique())),
            delta_minutes=("delta_minutes", "sum"),
            dk_rate=("dk_rate", "mean"),
            proj_boost=("proj_boost", "sum"),
        )
    )

    # ── Compounding: diminishing returns for multi-absence teams ─────────────
    combined = _apply_compounding(combined, out_player_teams, compounding)

    # Filter to slate players only when provided
    if slate_players is not None and not slate_players.empty:
        name_col = _detect_name_col(slate_players)
        slate_names = set(slate_players[name_col].str.lower())
        combined = combined[combined["player_name"].str.lower().isin(slate_names)]

    combined = combined[combined["proj_boost"] > 0].sort_values("proj_boost", ascending=False)
    log.info("Replacement engine: %d players boosted for %s", len(combined), out_players)
    return combined.reset_index(drop=True)


def _apply_compounding(
    combined: pd.DataFrame,
    out_player_teams: dict[str, str],
    cfg: CompoundingConfig,
) -> pd.DataFrame:
    """
    Apply diminishing-returns multiplier for teams with multiple OUT players.

    When 2+ players are out on the same team, the remaining minutes don't
    simply add — they overlap. We scale the summed boost by a compounding
    factor that gives a slight *extra* bump (since the opportunity is larger)
    but caps at a maximum to prevent runaway projections.

    Formula per team with N out:
      compound_multiplier = sum(factor^i for i in 0..N-1) / N
      adjusted_boost = raw_boost * (1 + (compound_multiplier - 1) * extra_weight)
    """
    if combined.empty:
        return combined

    # Count how many OUT players per team
    team_out_counts: dict[str, int] = {}
    for _, team in out_player_teams.items():
        if team:
            team_out_counts[team] = team_out_counts.get(team, 0) + 1

    # Only process teams with enough OUT players
    multi_out_teams = {t: n for t, n in team_out_counts.items() if n >= cfg.min_out_for_compound}
    if not multi_out_teams:
        return combined

    combined = combined.copy()
    for team, n_out in multi_out_teams.items():
        team_mask = combined["team"] == team
        if not team_mask.any():
            continue

        # Diminishing series: 1.0 + 0.75 + 0.5625 + ... averaged
        compound_multiplier = sum(cfg.diminishing_factor ** i for i in range(n_out)) / n_out
        # Apply as a modest extra bump (not a full multiplier) — typically 1.05–1.15x
        adjustment = 1.0 + (compound_multiplier - 1.0) * 0.5
        combined.loc[team_mask, "proj_boost"] = (
            combined.loc[team_mask, "proj_boost"] * adjustment
        ).round(2)
        # Cap at max
        combined.loc[team_mask, "proj_boost"] = combined.loc[team_mask, "proj_boost"].clip(
            upper=cfg.max_compound_boost
        )
        log.info(
            "Compounding boost for %s (%d OUT): multiplier=%.3f, adjustment=%.3f",
            team, n_out, compound_multiplier, adjustment,
        )

    return combined


def _compute_for_player(
    player_name: str,
    con: duckdb.DuckDBPyConnection,
    min_games: int,
    lookback: int,
) -> Optional[ReplacementResult]:
    """Compute minute deltas for a single OUT player's teammates."""

    team = _get_team_for_player(player_name, con)
    if not team:
        log.warning("No game log records found for '%s' – using fallback", player_name)
        return ReplacementResult(
            out_player=player_name, team="", absent_games=0,
            boosts=pd.DataFrame(), used_fallback=True,
        )

    # Game dates where this player has recorded minutes (active)
    active_dates = con.execute("""
        SELECT DISTINCT game_date
        FROM player_game_logs
        WHERE LOWER(player_name) = LOWER(?)
          AND minutes > 0
        ORDER BY game_date DESC
        LIMIT ?
    """, [player_name, lookback]).df()

    # Game dates where this player's team played but player had 0 min (absent/DNP)
    absent_dates = con.execute("""
        WITH team_dates AS (
            SELECT DISTINCT game_date
            FROM player_game_logs
            WHERE team = ?
            ORDER BY game_date DESC
            LIMIT ?
        ),
        player_played AS (
            SELECT DISTINCT game_date
            FROM player_game_logs
            WHERE LOWER(player_name) = LOWER(?)
              AND minutes > 0
        )
        SELECT t.game_date
        FROM team_dates t
        LEFT JOIN player_played p ON t.game_date = p.game_date
        WHERE p.game_date IS NULL
    """, [team, lookback * 2, player_name]).df()

    n_absent = len(absent_dates)
    if n_absent < min_games:
        log.info(
            "'%s': only %d absent games found (need %d) – using fallback",
            player_name, n_absent, min_games,
        )
        return ReplacementResult(
            out_player=player_name, team=team, absent_games=n_absent,
            boosts=pd.DataFrame(), used_fallback=True,
        )

    active_date_list  = active_dates["game_date"].tolist()
    absent_date_list  = absent_dates["game_date"].tolist()

    # Teammate minutes in absent vs active games
    if not active_date_list:
        active_mins_df = pd.DataFrame(columns=["player_name", "avg_mins"])
    else:
        active_mins_df = con.execute("""
            SELECT player_name,
                   AVG(minutes) AS avg_mins,
                   AVG(dk_pts / NULLIF(minutes, 0)) AS dk_rate
            FROM player_game_logs
            WHERE team = ?
              AND game_date IN (SELECT UNNEST(?::DATE[]))
              AND LOWER(player_name) != LOWER(?)
              AND minutes > 5
            GROUP BY player_name
            HAVING COUNT(*) >= 3
        """, [team, active_date_list, player_name]).df()

    absent_mins_df = con.execute("""
        SELECT player_name,
               AVG(minutes) AS avg_mins_absent,
               AVG(dk_pts / NULLIF(minutes, 0)) AS dk_rate_absent
        FROM player_game_logs
        WHERE team = ?
          AND game_date IN (SELECT UNNEST(?::DATE[]))
          AND LOWER(player_name) != LOWER(?)
          AND minutes > 5
        GROUP BY player_name
        HAVING COUNT(*) >= 2
    """, [team, absent_date_list, player_name]).df()

    if absent_mins_df.empty:
        return ReplacementResult(
            out_player=player_name, team=team, absent_games=n_absent,
            boosts=pd.DataFrame(), used_fallback=True,
        )

    # Merge and compute delta
    if active_mins_df.empty:
        merged = absent_mins_df.rename(columns={"avg_mins_absent": "absent_mins", "dk_rate_absent": "dk_rate"})
        merged["active_mins"] = 0.0
    else:
        merged = absent_mins_df.merge(
            active_mins_df[["player_name", "avg_mins", "dk_rate"]],
            on="player_name", how="left"
        )
        merged.rename(columns={
            "avg_mins_absent": "absent_mins",
            "dk_rate_absent": "dk_rate_absent_col",  # temp
            "avg_mins": "active_mins",
        }, inplace=True)
        # Use whichever dk_rate is available
        merged["dk_rate"] = merged["dk_rate"].fillna(merged["dk_rate_absent_col"])
        merged.drop(columns=["dk_rate_absent_col"], errors="ignore", inplace=True)

    merged["delta_minutes"] = merged["absent_mins"].fillna(0) - merged["active_mins"].fillna(0)
    merged = merged[merged["delta_minutes"] >= MIN_DELTA_MINUTES].copy()
    merged["dk_rate"]   = merged["dk_rate"].fillna(DK_PER_MINUTE_FALLBACK)
    merged["proj_boost"] = (merged["delta_minutes"] * merged["dk_rate"]).round(2)
    merged["team"]       = team

    result_cols = ["player_name", "team", "delta_minutes", "dk_rate", "proj_boost"]
    boosts = merged[result_cols].sort_values("proj_boost", ascending=False).reset_index(drop=True)

    log.info(
        "'%s' OUT (team=%s): %d absent games found, %d teammates boosted",
        player_name, team, n_absent, len(boosts),
    )
    return ReplacementResult(
        out_player=player_name, team=team, absent_games=n_absent, boosts=boosts,
    )


def _fallback_boosts(
    out_players: list[str],
    slate_players: pd.DataFrame | None,
    team: str = "",
) -> pd.DataFrame:
    """
    Flat fallback when no historical data available.
    Returns same-team slate players with a flat DK boost.
    """
    if slate_players is None or slate_players.empty:
        return pd.DataFrame(columns=["player_name", "team", "delta_minutes", "dk_rate", "proj_boost"])

    name_col = _detect_name_col(slate_players)
    team_col = next((c for c in ["Team", "team", "TEAM"] if c in slate_players.columns), None)

    rows = []
    out_lower = {p.lower() for p in out_players}

    for _, row in slate_players.iterrows():
        pname = str(row[name_col])
        if pname.lower() in out_lower:
            continue   # don't boost the OUT player themselves
        pteam = str(row[team_col]) if team_col else team
        if team and pteam != team:
            continue
        rows.append({
            "player_name":   pname,
            "team":          pteam,
            "delta_minutes": MINUTES_BOOST_FALLBACK,
            "dk_rate":       DK_PER_MINUTE_FALLBACK,
            "proj_boost":    round(MINUTES_BOOST_FALLBACK * DK_PER_MINUTE_FALLBACK, 2),
        })

    return pd.DataFrame(rows)


def _detect_name_col(df: pd.DataFrame) -> str:
    for col in ["Name", "name", "Player", "player_name", "PLAYER_NAME"]:
        if col in df.columns:
            return col
    return df.columns[0]
