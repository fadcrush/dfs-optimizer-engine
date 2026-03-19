"""
Defense vs. Player (DvP) module for NBA projections.

Computes per-opponent-team defensive strength from ``player_game_logs``
in ``dfs_edge.duckdb``, then returns a multiplier per team that the
projection engine applies to each player's base projection.

How it works
------------
1. For each opposing team, calculate the average DK/FD points per player
   per game they have allowed (last ``lookback_days`` days of data).
2. Calculate the league-wide average for the same window.
3. DvP multiplier = team_allowed_avg / league_avg

   > 1.0  → soft defence  (opponent typically allows more pts → bump up projections)
   < 1.0  → tough defence (opponent locks players down → trim projections)

The multiplier is intentionally mild — it is capped at ±``max_adj`` of 1.0
(default ±12 %) so a single bad defensive sample doesn't over-correct.

Usage::
    from analysis.nba.dvp import load_dvp_table
    dvp = load_dvp_table(site="DK", lookback_days=30)
    multiplier = dvp.get("BOS", 1.0)
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

from analysis.shared.db import get_conn  # noqa: E402

_DEFAULT_DB = (
    Path(__file__).resolve().parent.parent.parent / "data" / "dfs_edge.duckdb"
)


def load_dvp_table(
    site: str = "DK",
    db_path: Path | str | None = None,
    lookback_days: int = 30,
    min_games: int = 5,
    max_adj: float = 0.12,
) -> dict[str, float]:
    """Return a dict mapping 3-letter team abbreviation → DvP multiplier.

    Args:
        site:         ``"DK"`` or ``"FD"`` — which fantasy scoring to use.
        db_path:      Override path to ``dfs_edge.duckdb``.
        lookback_days: Only consider games within this many calendar days.
        min_games:    Minimum player-games an opponent must have allowed to
                      be included (avoids tiny-sample outliers early in season).
        max_adj:      Cap the multiplier deviation from 1.0.
                      With the default 0.12 the range is [0.88, 1.12].

    Returns:
        Dict of ``{"BOS": 0.93, "MIA": 1.07, ...}``  — defaults to 1.0 for any
        team not present (neutral / no data).
    """
    resolved_db = Path(db_path) if db_path else _DEFAULT_DB

    if not resolved_db.exists():
        log.warning("DvP: dfs_edge.duckdb not found at %s — skipping", resolved_db)
        return {}

    try:
        import duckdb
    except ImportError:
        log.warning("DvP: duckdb not installed — skipping")
        return {}

    pts_col = "dk_pts" if site.upper() == "DK" else "fd_pts"

    sql = f"""
        WITH recent AS (
            SELECT
                opponent,
                {pts_col} AS pts
            FROM player_game_logs
            WHERE minutes > 0
              AND game_date >= CURRENT_DATE - INTERVAL '{lookback_days}' DAY
        ),
        team_allowed AS (
            SELECT
                opponent                       AS team,
                ROUND(AVG(pts), 4)             AS avg_allowed,
                COUNT(*)                       AS n_player_games
            FROM recent
            GROUP BY opponent
            HAVING COUNT(*) >= {min_games}
        ),
        league_avg AS (
            SELECT ROUND(AVG(pts), 4) AS league_avg_pts
            FROM recent
        )
        SELECT
            t.team,
            t.avg_allowed,
            l.league_avg_pts,
            ROUND(t.avg_allowed / NULLIF(l.league_avg_pts, 0), 6) AS raw_multiplier
        FROM team_allowed t
        CROSS JOIN league_avg l
        ORDER BY raw_multiplier DESC
    """

    try:
        con = get_conn(resolved_db)
        rows = con.execute(sql).fetchall()
    except Exception as exc:
        log.warning("DvP query failed: %s", exc)
        return {}

    if not rows:
        log.info("DvP: no rows returned (too few games in window) — neutral multipliers")
        return {}

    dvp: dict[str, float] = {}
    for team, avg_allowed, league_avg, raw_mult in rows:
        if raw_mult is None or league_avg is None or league_avg == 0:
            dvp[team] = 1.0
            continue
        # Clamp to [1 - max_adj, 1 + max_adj]
        clamped = max(1.0 - max_adj, min(1.0 + max_adj, float(raw_mult)))
        dvp[team] = round(clamped, 4)

    log.info(
        "DvP table loaded: %d teams | site=%s | L%dd | top-soft: %s | top-tough: %s",
        len(dvp),
        site,
        lookback_days,
        max(dvp, key=dvp.get, default="-"),
        min(dvp, key=dvp.get, default="-"),
    )
    return dvp


def load_dvp_by_position(
    site: str = "DK",
    db_path: Path | str | None = None,
    game_logs_db_path: Path | str | None = None,
    lookback_days: int = 30,
    min_games: int = 5,
    max_adj: float = 0.12,
) -> dict[str, dict[str, float]]:
    """Return position-specific DvP multipliers.

    Joins ``player_game_logs`` (actuals) with ``player_positions`` (positions
    snapshotted from previous slates) to compute how many fantasy points each
    opponent team allows **per position**.  This is more accurate than the
    team-level average returned by :func:`load_dvp_table` because a team may
    be elite at defending point guards but porous against centres.

    Args:
        site:              ``"DK"`` or ``"FD"`` — which fantasy scoring.
        db_path:           Path to ``dfs_edge.duckdb`` (contains
                           ``player_positions`` and projection tables).
        game_logs_db_path: Path to the DuckDB file that holds
                           ``player_game_logs``.  Defaults to *db_path* when
                           ``None`` (useful for tests).
        lookback_days:     Window of game history to use.
        min_games:         Minimum player-games per (team, position) bucket.
        max_adj:           Max multiplier deviation from 1.0 (default ±12 %).

    Returns:
        Nested dict ``{position: {team: multiplier}}``, e.g.::

            {
                "PG": {"BOS": 0.91, "MIA": 1.08, ...},
                "SF": {"BOS": 1.04, "MIA": 0.95, ...},
                ...
            }

        Any (position, team) pair that doesn't have enough data is **absent**
        from the inner dict — callers should fall back to the team-level
        multiplier from :func:`load_dvp_table` or 1.0.
    """
    resolved_db = Path(db_path) if db_path else _DEFAULT_DB

    if not resolved_db.exists():
        log.warning("DvP by position: DB not found at %s — returning empty", resolved_db)
        return {}

    # Determine where player_game_logs lives
    gl_db = Path(game_logs_db_path) if game_logs_db_path else resolved_db

    pts_col = "dk_pts" if site.upper() == "DK" else "fd_pts"

    try:
        con = get_conn(resolved_db)

        # ATTACH the game-logs DB as read-only if it's a different file
        if gl_db != resolved_db:
            gl_alias = "_pos_gl_db"
            con.execute(
                f"ATTACH IF NOT EXISTS '{str(gl_db).replace(chr(92), '/')}'"
                f" AS {gl_alias} (READ_ONLY)"
            )
            gl_ref = f"{gl_alias}.player_game_logs"
        else:
            gl_ref = "player_game_logs"

        sql = f"""
            WITH recent_logs AS (
                SELECT
                    g.player_name,
                    g.opponent      AS team,
                    g.{pts_col}     AS pts
                FROM {gl_ref} g
                WHERE g.minutes > 0
                  AND g.game_date >= CURRENT_DATE - INTERVAL '{lookback_days}' DAY
            ),
            positioned AS (
                SELECT
                    r.team,
                    pp.position,
                    r.pts
                FROM recent_logs r
                JOIN player_positions pp
                  ON pp.player_name = r.player_name
                 AND pp.site        = '{site.upper()}'
            ),
            team_pos_avg AS (
                SELECT
                    team,
                    position,
                    ROUND(AVG(pts), 4)  AS avg_allowed,
                    COUNT(*)            AS n_games
                FROM positioned
                GROUP BY team, position
                HAVING COUNT(*) >= {min_games}
            ),
            pos_league_avg AS (
                SELECT
                    position,
                    ROUND(AVG(pts), 4) AS league_pos_avg
                FROM positioned
                GROUP BY position
            )
            SELECT
                t.team,
                t.position,
                t.avg_allowed,
                p.league_pos_avg,
                ROUND(t.avg_allowed / NULLIF(p.league_pos_avg, 0), 6) AS raw_mult
            FROM team_pos_avg t
            JOIN pos_league_avg p USING (position)
            ORDER BY t.position, raw_mult DESC
        """

        rows = con.execute(sql).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("DvP by position query failed: %s", exc)
        return {}

    if not rows:
        log.info(
            "DvP by position: no rows (too few games or missing player_positions data)"
        )
        return {}

    result: dict[str, dict[str, float]] = {}
    for team, pos, avg_allowed, league_pos_avg, raw_mult in rows:
        if raw_mult is None or league_pos_avg is None or league_pos_avg == 0:
            continue
        clamped = round(
            max(1.0 - max_adj, min(1.0 + max_adj, float(raw_mult))), 4
        )
        result.setdefault(pos, {})[team] = clamped

    n_buckets = sum(len(v) for v in result.values())
    log.info(
        "DvP by position loaded: %d positions × %d team buckets | site=%s | L%dd",
        len(result),
        n_buckets,
        site,
        lookback_days,
    )
    return result
