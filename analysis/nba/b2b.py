"""
Back-to-back (B2B) and rest-days adjustment for NBA projections.

Queries ``player_game_logs`` in dfs_edge.duckdb to determine how many
days of rest each team has before a given slate date, then returns
projection multipliers that model fatigue / freshness effects.

B2B / rest multipliers
──────────────────────
  Days rest  │  Multiplier  │  Scenario
  ───────────┼──────────────┼──────────────────────────────────────
       0 (B2B)│  0.960       │ Played yesterday — fatigue risk
       1      │  0.985       │ Short rest — mild fatigue
       2      │  1.000       │ Normal rest — neutral
      3+      │  1.010       │ Extended rest — freshness bonus

Blowout risk multipliers
────────────────────────
  Spread (from team's perspective)  │  Multiplier
  ─────────────────────────────────┼─────────────
          > +15 (heavy underdog)   │  0.930   — blowout likely → fewer minutes/role
     +10 < spread ≤ +15 (underdog) │  0.970   — negative game script risk
              spread ≤ +10         │  1.000   — no adjustment
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "dfs_edge.duckdb"

# Rest-day bucket → multiplier
_REST_MULTIPLIERS: dict[str, float] = {
    "b2b":    0.960,   # 0 days rest
    "short":  0.985,   # 1 day rest
    "normal": 1.000,   # 2 days rest
    "long":   1.010,   # 3+ days rest
}


def _days_of_rest(last_game: date, slate: date) -> int:
    """
    Calendar days between the last game and the slate game, exclusive of
    both endpoints.  B2B = 0 (last game was yesterday).
    """
    return max(0, (slate - last_game).days - 1)


def compute_rest_days(
    slate_date: Optional[date] = None,
    db_path: Optional[Path] = None,
) -> dict[str, int]:
    """Return ``{TEAM: days_of_rest}`` for all teams with recent game history.

    Queries the most recent ``game_date`` per team from ``player_game_logs``
    (all rows *before* ``slate_date`` so the slate game itself is excluded).

    Returns an empty dict on any error — callers should default to 1.0
    (neutral multiplier) for missing teams.

    Args:
        slate_date: The date of the upcoming slate.  Defaults to today.
        db_path:    Override path to ``dfs_edge.duckdb``.
    """
    resolved_db = Path(db_path) if db_path else _DEFAULT_DB
    target: date = slate_date if slate_date is not None else date.today()

    if not resolved_db.exists():
        log.warning("B2B: dfs_edge.duckdb not found at %s — rest-days skipped", resolved_db)
        return {}

    try:
        from analysis.shared.db import get_conn  # noqa: PLC0415
        con = get_conn(resolved_db)
        rows = con.execute(
            """
            SELECT team, MAX(game_date) AS last_game
            FROM player_game_logs
            WHERE game_date < ?
            GROUP BY team
            """,
            [target.isoformat()],
        ).fetchall()
    except Exception as exc:  # pragma: no cover — DB failures logged, not raised
        log.warning("B2B: rest-days query failed: %s", exc)
        return {}

    result: dict[str, int] = {}
    for team, last_game in rows:
        if last_game is None:
            continue
        # DuckDB may return a ``datetime.date`` or an ISO-format string depending
        # on the duckdb-python version; normalise to date.
        if isinstance(last_game, str):
            last_game = datetime.strptime(last_game[:10], "%Y-%m-%d").date()
        elif hasattr(last_game, "date"):
            last_game = last_game.date()
        result[str(team).upper()] = _days_of_rest(last_game, target)

    log.debug("B2B: rest days computed for %d teams (slate=%s)", len(result), target)
    return result


def get_rest_multipliers(
    slate_date: Optional[date] = None,
    db_path: Optional[Path] = None,
) -> dict[str, float]:
    """Return ``{TEAM: multiplier}`` for B2B / rest-days projection adjustment.

    Multipliers come from :data:`_REST_MULTIPLIERS`.  Falls back to an empty
    dict on any DB error — the projection engine treats a missing team as 1.0
    (neutral / no adjustment).

    Args:
        slate_date: The upcoming slate date.  Defaults to today.
        db_path:    Override path to ``dfs_edge.duckdb``.
    """
    rest_days = compute_rest_days(slate_date, db_path)
    if not rest_days:
        return {}

    multipliers: dict[str, float] = {}
    for team, days in rest_days.items():
        if days == 0:
            multi = _REST_MULTIPLIERS["b2b"]
        elif days == 1:
            multi = _REST_MULTIPLIERS["short"]
        elif days == 2:
            multi = _REST_MULTIPLIERS["normal"]
        else:
            multi = _REST_MULTIPLIERS["long"]
        multipliers[team] = multi

    n_b2b = sum(1 for v in multipliers.values() if v == _REST_MULTIPLIERS["b2b"])
    n_short = sum(1 for v in multipliers.values() if v == _REST_MULTIPLIERS["short"])
    n_long = sum(1 for v in multipliers.values() if v == _REST_MULTIPLIERS["long"])
    log.info(
        "B2B multipliers: b2b=%d  short=%d  long=%d  (total teams=%d)",
        n_b2b, n_short, n_long, len(multipliers),
    )
    return multipliers


def get_blowout_multipliers(vegas_totals: dict[str, dict]) -> dict[str, float]:
    """Return ``{TEAM: multiplier}`` based on blowout risk from the point spread.

    ``vegas_totals`` must be in the format produced by
    ``analysis.shared.vegas_enricher._fetch_team_totals``:
    ``{team_abbrev: {"spread": float, "team_total": float, ...}}``.

    Spread convention used here: **positive spread = team is the underdog**
    (mirrors how ``vegas_enricher._parse_game_totals`` assigns the away team
    ``spread = -home_spread``, so +12 means 12-point underdog).

    Blowout tiers
    ─────────────
      spread > +15  → 0.930 — heavy underdog, likely garbage-time blowout
      spread > +10  → 0.970 — meaningful underdog, negative game-script risk
      spread ≤ +10  → 1.000 — no adjustment (also applies to favourites)

    Args:
        vegas_totals: Dict of team-level Vegas data.  Returns {} if empty.
    """
    if not vegas_totals:
        return {}

    multipliers: dict[str, float] = {}
    for team, info in vegas_totals.items():
        spread = float(info.get("spread", 0.0))
        if spread > 15.0:
            multi = 0.930
        elif spread > 10.0:
            multi = 0.970
        else:
            multi = 1.000
        multipliers[str(team).upper()] = multi

    n_penalized = sum(1 for v in multipliers.values() if v < 1.0)
    if n_penalized:
        log.debug("Blowout risk: %d/%d teams carry a spread penalty", n_penalized, len(multipliers))
    return multipliers


# ---------------------------------------------------------------------------
# Game-total (over/under) projection multiplier — §3.4 correlated signal
# ---------------------------------------------------------------------------

_LEAGUE_AVG_TOTAL: float = 220.0
# How much the multiplier shifts per point of total above/below league average.
# 0.0025 → ±2.5% for a 10-point swing in the O/U (e.g. 230 vs. 220 → +2.5%).
_TOTAL_SENSITIVITY: float = 0.0025
# Hard cap on the adjustment in either direction.
_TOTAL_CAP: float = 0.05   # ±5 %


def get_game_total_multipliers(
    vegas_totals: dict[str, dict],
    league_avg: float = _LEAGUE_AVG_TOTAL,
    sensitivity: float = _TOTAL_SENSITIVITY,
    cap: float = _TOTAL_CAP,
) -> dict[str, float]:
    """Return ``{TEAM: multiplier}`` based on the game over/under total.

    Players in high-scoring game environments (large O/U) get a modest boost;
    defensive slug-fests (low O/U) get a small penalty.  The adjustment is
    symmetric and linear around ``league_avg``, capped at ``±cap``.

    Formula (per team)::

        delta  = clamp((game_total - league_avg) * sensitivity, -cap, +cap)
        mult   = 1.0 + delta

    E.g. game total = 232, league avg = 220, sensitivity = 0.0025:
        delta = (232 - 220) * 0.0025 = 0.030  →  multiplier = 1.030

    ``vegas_totals`` format: ``{team_abbrev: {"total": float, ...}}``
    (same dict produced by ``analysis.shared.vegas_enricher._fetch_team_totals``).

    Returns ``{}`` if the input is empty or no team has a valid ``"total"`` key.
    Parameters are exposed for unit-testing and future calibration.
    """
    if not vegas_totals:
        return {}

    multipliers: dict[str, float] = {}
    for team, info in vegas_totals.items():
        raw_total = info.get("total") if "total" in info else info.get("game_total")
        if raw_total is None:
            continue
        game_total = float(raw_total)
        delta = max(-cap, min(cap, (game_total - league_avg) * sensitivity))
        multipliers[str(team).upper()] = round(1.0 + delta, 5)

    n_boosted = sum(1 for v in multipliers.values() if v > 1.0)
    n_reduced = sum(1 for v in multipliers.values() if v < 1.0)
    log.debug(
        "Game-total multipliers: boosted=%d  reduced=%d  neutral=%d",
        n_boosted, n_reduced, len(multipliers) - n_boosted - n_reduced,
    )
    return multipliers


# ---------------------------------------------------------------------------
# Injury usage-boost multiplier — §3.4 usage rate after teammate injuries
# ---------------------------------------------------------------------------

# Per-OUT-player boost for an active teammate at the same primary position.
_INJURY_BOOST_SAME_POS: float = 0.08   # +8 % per OUT player at same position
# Per-OUT-player boost for an active teammate at a different position on same team.
_INJURY_BOOST_DIFF_POS: float = 0.02   # +2 % per OUT player at different position
# Maximum cumulative boost any single active player can receive.
_INJURY_BOOST_CAP: float = 0.25        # ±25 % hard cap

# Status values treated as "player will not play" — excludes GTD (game-time decision).
_OUT_STATUSES: frozenset = frozenset({"OUT", "SSPD"})


def get_injury_boost_multipliers(
    df: "pd.DataFrame",
    team_col: str = "Team",
    pos_col: str = "Pos",
    status_col: str = "InjuryStatus",
    name_col: str = "Name",
    same_pos_boost: float = _INJURY_BOOST_SAME_POS,
    diff_pos_boost: float = _INJURY_BOOST_DIFF_POS,
    cap: float = _INJURY_BOOST_CAP,
    out_statuses: frozenset = _OUT_STATUSES,
) -> dict[str, float]:
    """Return ``{NAME_KEY: multiplier}`` for injury usage boosts.

    When a starter or rotation player is marked OUT or SSPD, remaining active
    players on the same team receive a usage boost.  Teammates at the same
    primary position get a larger boost than those playing a different position.

    Formula per active player A::

        boost   = (n_same_pos_out × same_pos_boost) + (n_diff_pos_out × diff_pos_boost)
        boost   = min(boost, cap)
        mult    = 1.0 + boost

    The OUT/SSPD player themselves is never included in the result (they will be
    filtered from lineups by the pool-filter anyway).

    ``NAME_KEY = str(name).strip().upper()``

    Args:
        df:              Full slate DataFrame.
        team_col:        Column name for team abbreviation.
        pos_col:         Column name for position (e.g. "PG/SG" normalised to "PG").
        status_col:      Column name for injury status.  Absent column → no OUT players.
        name_col:        Column name for player name.
        same_pos_boost:  Fractional boost per OUT teammate at the same primary position.
        diff_pos_boost:  Fractional boost per OUT teammate at a different position.
        cap:             Maximum total boost for any single player.
        out_statuses:    Set of status strings that mark a player as unavailable.
    """
    import pandas as pd  # noqa: PLC0415
    from collections import defaultdict  # noqa: PLC0415

    if df is None or df.empty:
        return {}

    needed = [team_col, pos_col, name_col]
    if any(c not in df.columns for c in needed):
        return {}

    has_status = status_col in df.columns

    def _primary_pos(raw) -> str:
        """Normalise 'PG/SG' → 'PG'; upper-case."""
        return str(raw).strip().split("/")[0].strip().upper()

    def _name_key(raw) -> str:
        return str(raw).strip().upper()

    # Build compact record list
    records = []
    for _, row in df.iterrows():
        status_raw = str(row[status_col]).strip().upper() if has_status else ""
        records.append({
            "key":    _name_key(row[name_col]),
            "team":   str(row[team_col]).strip().upper(),
            "pos":    _primary_pos(row[pos_col]),
            "is_out": status_raw in out_statuses,
        })

    # Count OUT players per (team, position) bucket
    out_per_team_pos: dict[tuple, int] = defaultdict(int)
    out_per_team_total: dict[str, int] = defaultdict(int)
    for rec in records:
        if rec["is_out"]:
            out_per_team_pos[(rec["team"], rec["pos"])] += 1
            out_per_team_total[rec["team"]] += 1

    if not out_per_team_total:
        return {}  # No OUT/SSPD players on the slate — nothing to boost

    multipliers: dict[str, float] = {}
    for rec in records:
        if rec["is_out"]:
            continue  # OUT players receive no boost
        team = rec["team"]
        pos  = rec["pos"]
        n_same  = out_per_team_pos.get((team, pos), 0)
        n_total = out_per_team_total.get(team, 0)
        n_diff  = n_total - n_same
        raw_boost = (n_same * same_pos_boost) + (n_diff * diff_pos_boost)
        capped = min(raw_boost, cap)
        if capped > 0.0:
            multipliers[rec["key"]] = round(1.0 + capped, 5)

    n_boosted = len(multipliers)
    n_out_teams = len(out_per_team_total)
    log.debug(
        "Injury boosts: %d players boosted across %d team(s) with OUT players",
        n_boosted, n_out_teams,
    )
    return multipliers
