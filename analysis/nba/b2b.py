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
