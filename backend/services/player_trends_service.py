"""
Player Trends Service
=====================
Queries ``player_game_logs`` from ``dfs_edge.duckdb`` to compute:

  * Last-5 and Last-10 game averages
  * Season averages (current season only)
  * Per-game log detail for the most recent N games
  * Per-minute production rates
  * Volatility metrics (standard deviation)

All DK / FD fantasy scores are *recomputed* from raw stat columns using the
canonical formulas in ``fantasy_scoring.py``.  This guarantees accuracy
regardless of whether the ingestion pipeline stored correct values.

The service is deliberately read-only and stateless.  All errors are caught
and logged; callers receive ``None`` or empty lists, never exceptions.
"""
from __future__ import annotations

import logging
import math
import statistics
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve paths and make the analysis package importable
# ---------------------------------------------------------------------------

_ROOT  = Path(__file__).resolve().parent.parent.parent
_EDGE_DB = _ROOT / "data" / "dfs_edge.duckdb"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from analysis.shared.db import get_conn as _get_conn
except ImportError:
    import duckdb as _duckdb_mod  # type: ignore[import]

    def _get_conn(path, *, db_key=None, read_only=False):  # type: ignore[misc]
        return _duckdb_mod.connect(str(path), read_only=read_only)

# Import scoring helpers from the sibling module
from services.fantasy_scoring import score_game_row  # noqa: E402


# ---------------------------------------------------------------------------
# Internal type aliases
# ---------------------------------------------------------------------------

GameRow  = dict[str, Any]
SplitDict = dict[str, Any]


# ---------------------------------------------------------------------------
# DB connection helper
# ---------------------------------------------------------------------------

def _edge():
    """Return the shared process-level connection for dfs_edge.duckdb.

    Keep the connection mode aligned with the rest of the backend so trend
    reads do not conflict with projection/health routes that use the same DB.
    """
    return _get_conn(_EDGE_DB, db_key="dfs_edge", read_only=False)


# ---------------------------------------------------------------------------
# Null-safe helpers
# ---------------------------------------------------------------------------

def _safe(v: Any) -> float | None:
    """Return float or None (never NaN)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _f(v: Any) -> float:
    """Return float, defaulting to 0.0 for None / NaN."""
    s = _safe(v)
    return s if s is not None else 0.0


# ---------------------------------------------------------------------------
# Aggregate split computation
# ---------------------------------------------------------------------------

def _compute_split(games: list[GameRow]) -> SplitDict | None:
    """Aggregate a list of game dicts into a single split summary.

    Each game dict must already contain ``dk_points`` and ``fd_points`` keys
    (computed by ``_row_to_game``).  Returns ``None`` when the list is empty.
    """
    if not games:
        return None

    n = len(games)

    def avg(key: str) -> float:
        return sum(_f(g.get(key)) for g in games) / n

    dk_vals: list[float] = [g["dk_points"] for g in games if g["dk_points"] is not None]
    fd_vals: list[float] = [g["fd_points"] for g in games if g["fd_points"] is not None]

    avg_min = avg("minutes")
    avg_dk  = sum(dk_vals) / len(dk_vals) if dk_vals else None
    avg_fd  = sum(fd_vals) / len(fd_vals) if fd_vals else None

    def per_min(stat_avg: float | None) -> float | None:
        if stat_avg is None or avg_min <= 0:
            return None
        return round(stat_avg / avg_min, 4)

    def stddev(vals: list[float]) -> float | None:
        if len(vals) < 2:
            return None
        return round(statistics.stdev(vals), 4)

    return {
        "games_used":           n,
        "avg_minutes":          round(avg_min, 2),
        "avg_points":           round(avg("points"), 2),
        "avg_rebounds":         round(avg("rebounds"), 2),
        "avg_assists":          round(avg("assists"), 2),
        "avg_steals":           round(avg("steals"), 2),
        "avg_blocks":           round(avg("blocks"), 2),
        "avg_turnovers":        round(avg("turnovers"), 2),
        "avg_three_pm":         round(avg("three_pointers"), 2),
        "avg_fgm":              round(avg("fg_made"), 2),
        "avg_ftm":              round(avg("ft_made"), 2),
        "avg_dk_points":        round(avg_dk, 2) if avg_dk is not None else None,
        "avg_fd_points":        round(avg_fd, 2) if avg_fd is not None else None,
        "points_per_min":       per_min(avg("points")),
        "rebounds_per_min":     per_min(avg("rebounds")),
        "assists_per_min":      per_min(avg("assists")),
        "dk_points_per_min":    per_min(avg_dk),
        "fd_points_per_min":    per_min(avg_fd),
        "stddev_dk_points":     stddev(dk_vals),
        "stddev_fd_points":     stddev(fd_vals),
    }


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------

# Column names returned from the main query (order must match SELECT)
_COL_NAMES = (
    "game_id", "player_id", "player_name", "team", "opponent",
    "game_date", "season", "wl", "is_home",
    "minutes", "points", "rebounds", "assists", "steals", "blocks",
    "turnovers", "pf", "three_pointers", "fg_made", "ft_made", "fg_pct",
)


def _row_to_game(raw: GameRow) -> GameRow:
    """Convert a raw DuckDB row dict to a fully annotated game-log dict.

    Fantasy scores are computed fresh from raw stats (not from stored dk_pts /
    fd_pts) to guarantee formula correctness.
    """
    dk, fd = score_game_row(raw)

    game_date = raw.get("game_date")
    if game_date is not None and not isinstance(game_date, str):
        game_date = str(game_date)

    return {
        "game_id":       raw.get("game_id"),
        "game_date":     game_date,
        "opponent":      raw.get("opponent"),
        "is_home":       bool(raw["is_home"]) if raw.get("is_home") is not None else None,
        "wl":            raw.get("wl"),
        "season":        raw.get("season"),
        "started":       None,   # not in current schema; reserved for future ingestion
        "minutes":       _safe(raw.get("minutes")),
        "points":        _safe(raw.get("points")),
        "rebounds":      _safe(raw.get("rebounds")),
        "assists":       _safe(raw.get("assists")),
        "steals":        _safe(raw.get("steals")),
        "blocks":        _safe(raw.get("blocks")),
        "turnovers":     _safe(raw.get("turnovers")),
        "three_pointers": _safe(raw.get("three_pointers")),
        "fg_made":       _safe(raw.get("fg_made")),
        "ft_made":       _safe(raw.get("ft_made")),
        "pf":            _safe(raw.get("pf")),
        "dk_points":     dk,
        "fd_points":     fd,
    }


# ---------------------------------------------------------------------------
# Main query
# ---------------------------------------------------------------------------

_SELECT_COLS = ", ".join([
    "game_id", "player_id", "player_name", "team", "opponent",
    "game_date", "season", "wl", "is_home",
    "COALESCE(minutes, 0)        AS minutes",
    "COALESCE(points, 0)         AS points",
    "COALESCE(rebounds, 0)       AS rebounds",
    "COALESCE(assists, 0)        AS assists",
    "COALESCE(steals, 0)         AS steals",
    "COALESCE(blocks, 0)         AS blocks",
    "COALESCE(turnovers, 0)      AS turnovers",
    "COALESCE(pf, 0)             AS pf",
    "COALESCE(three_pointers, 0) AS three_pointers",
    "COALESCE(fg_made, 0)        AS fg_made",
    "COALESCE(ft_made, 0)        AS ft_made",
    "fg_pct",
])


def _table_exists(con) -> bool:
    """Return True when player_game_logs exists in dfs_edge."""
    try:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'player_game_logs'"
        ).fetchall()
        return bool(rows)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_players(q: str, limit: int = 20) -> list[dict]:
    """Return players whose names contain *q* (case-insensitive).

    Returns an empty list on any error or when the game log table is absent.
    Minimum query length is 2 characters.
    """
    if len(q.strip()) < 2:
        return []

    if not _EDGE_DB.exists():
        return []

    try:
        con = _edge()
        if not _table_exists(con):
            return []

        rows = con.execute(
            """
            SELECT DISTINCT player_name,
                            COALESCE(player_id, '') AS player_id,
                            COALESCE(team, '')       AS team
            FROM   player_game_logs
            WHERE  lower(player_name) LIKE lower(?)
            ORDER  BY player_name
            LIMIT  ?
            """,
            [f"%{q.strip()}%", limit],
        ).fetchall()
        return [
            {"player_name": r[0], "player_id": r[1] or None, "team": r[2] or None}
            for r in rows
        ]
    except Exception as exc:
        log.warning("player search failed: %s", exc)
        return []


def _compute_projection_delta(
    split: "SplitDict | None",
    projection: float | None,
    site: str = "dk",
) -> dict | None:
    """Return delta analysis comparing a projection to historical averages.

    Parameters
    ----------
    split      : _compute_split() output dict (last5 or last10)
    projection : current model projection in FPTS (DK or FD)
    site       : "dk" or "fd" — selects avg_dk_points vs avg_fd_points

    Returns a dict with::

        avg_historical   : float — the historical average for the split
        projection       : float — the current model projection
        delta            : float — projection − avg_historical (positive = model over-projects)
        delta_pct        : float — delta / avg_historical * 100
        signal           : "under_projecting" | "over_projecting" | "aligned"
        confidence       : "high" | "medium" | "low"  (based on games_used)
    """
    if split is None or projection is None:
        return None
    key = f"avg_{site.lower()}_points"
    avg_hist = split.get(key)
    if avg_hist is None or avg_hist == 0:
        return None
    delta = round(projection - avg_hist, 2)
    delta_pct = round(delta / avg_hist * 100, 1) if avg_hist != 0 else None
    games = split.get("games_used", 0) or 0

    if delta < -1.5:
        signal = "under_projecting"    # model is BELOW the trend (green: model cheap)
    elif delta > 1.5:
        signal = "over_projecting"     # model is ABOVE the trend (red: risky)
    else:
        signal = "aligned"

    confidence = "high" if games >= 8 else ("medium" if games >= 5 else "low")

    return {
        "avg_historical": round(avg_hist, 2),
        "projection": round(projection, 2),
        "delta": delta,
        "delta_pct": delta_pct,
        "signal": signal,
        "confidence": confidence,
        "games_used": games,
    }


def get_player_trends(
    player_name: str,
    recent_games_limit: int = 10,
    projection_dk: float | None = None,
    projection_fd: float | None = None,
) -> dict | None:
    """Fetch complete trend data for *player_name*.

    Returns a structured dict with keys::

        player_name, player_id, team, current_season,
        total_games_available, last5, last10, season, recent_games

    Returns ``None`` when the player has no qualifying game logs
    (``minutes > 0``).  Logs warnings on unexpected errors.
    """
    if not _EDGE_DB.exists():
        log.warning("dfs_edge.duckdb not found — player trends unavailable")
        return None

    try:
        con = _edge()
        if not _table_exists(con):
            log.warning("player_game_logs table not found in dfs_edge.duckdb")
            return None
    except Exception as exc:
        log.warning("DB table check failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Fetch all games for this player, newest first
    # ------------------------------------------------------------------
    try:
        con = _edge()
        rows = con.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM   player_game_logs
            WHERE  lower(player_name) = lower(?)
              AND  COALESCE(minutes, 0) > 0
            ORDER  BY game_date DESC
            """,
            [player_name],
        ).fetchall()
    except Exception as exc:
        log.warning("game log query failed for '%s': %s", player_name, exc)
        return None

    if not rows:
        return None

    # Convert to list of dicts
    all_raw: list[GameRow] = [dict(zip(_COL_NAMES, r)) for r in rows]
    all_games: list[GameRow] = [_row_to_game(r) for r in all_raw]

    # ------------------------------------------------------------------
    # Player identity from the most recent row
    # ------------------------------------------------------------------
    most_recent = all_raw[0]
    player_id = most_recent.get("player_id") or None
    team      = most_recent.get("team") or None

    # ------------------------------------------------------------------
    # Season detection — use the lexicographically largest season string
    # ------------------------------------------------------------------
    seasons = [r.get("season") for r in all_raw if r.get("season")]
    current_season = max(seasons) if seasons else None

    # ------------------------------------------------------------------
    # Slices
    # ------------------------------------------------------------------
    last10  = all_games[:10]
    last5   = all_games[:5]
    season_games = (
        [g for g, r in zip(all_games, all_raw) if r.get("season") == current_season]
        if current_season
        else all_games
    )
    recent_display = all_games[:recent_games_limit]

    _split5 = _compute_split(last5)
    _split10 = _compute_split(last10)

    result = {
        "player_name":            player_name,
        "player_id":              player_id,
        "team":                   team,
        "current_season":         current_season,
        "total_games_available":  len(all_games),
        "last5":                  _split5,
        "last10":                 _split10,
        "season":                 _compute_split(season_games),
        "recent_games":           recent_display,
    }

    # Projection delta — only populated when caller passes current projections
    if projection_dk is not None or projection_fd is not None:
        result["projection_delta"] = {
            "dk": {
                "l5":  _compute_projection_delta(_split5,  projection_dk, "dk"),
                "l10": _compute_projection_delta(_split10, projection_dk, "dk"),
            },
            "fd": {
                "l5":  _compute_projection_delta(_split5,  projection_fd, "fd"),
                "l10": _compute_projection_delta(_split10, projection_fd, "fd"),
            },
        }
    else:
        result["projection_delta"] = None

    return result


def get_slate_trends(limit: int = 200) -> list[dict]:
    """Return basic last-10 averages for all active players.

    "Active" means at least 3 games in the player_game_logs table with
    ``minutes > 0``.  Results are ordered by descending average points.

    This endpoint powers the slate-wide table view.  Fantasy scoring is not
    included here to keep response size manageable; use ``get_player_trends``
    for per-player fantasy breakdowns.
    """
    if not _EDGE_DB.exists():
        return []

    try:
        con = _edge()
        if not _table_exists(con):
            return []

        rows = con.execute(
            """
            WITH ranked AS (
                SELECT
                    player_name,
                    COALESCE(player_id, '')   AS player_id,
                    COALESCE(team, '')         AS team,
                    COALESCE(minutes, 0)       AS minutes,
                    COALESCE(points, 0)        AS points,
                    COALESCE(rebounds, 0)      AS rebounds,
                    COALESCE(assists, 0)       AS assists,
                    COALESCE(steals, 0)        AS steals,
                    COALESCE(blocks, 0)        AS blocks,
                    COALESCE(turnovers, 0)     AS turnovers,
                    COALESCE(three_pointers, 0) AS three_pointers,
                    ROW_NUMBER() OVER (
                        PARTITION BY player_name
                        ORDER BY game_date DESC
                    ) AS rn
                FROM player_game_logs
                WHERE COALESCE(minutes, 0) > 0
            )
            SELECT
                player_name,
                player_id,
                team,
                COUNT(*)                FILTER (WHERE rn <= 10)  AS games_l10,
                ROUND(AVG(minutes)      FILTER (WHERE rn <= 10), 2) AS avg_min,
                ROUND(AVG(points)       FILTER (WHERE rn <= 10), 2) AS avg_pts,
                ROUND(AVG(rebounds)     FILTER (WHERE rn <= 10), 2) AS avg_reb,
                ROUND(AVG(assists)      FILTER (WHERE rn <= 10), 2) AS avg_ast,
                ROUND(AVG(steals)       FILTER (WHERE rn <= 10), 2) AS avg_stl,
                ROUND(AVG(blocks)       FILTER (WHERE rn <= 10), 2) AS avg_blk,
                ROUND(AVG(turnovers)    FILTER (WHERE rn <= 10), 2) AS avg_tov,
                ROUND(AVG(three_pointers) FILTER (WHERE rn <= 10), 2) AS avg_3pm
            FROM ranked
            GROUP BY player_name, player_id, team
            HAVING COUNT(*) FILTER (WHERE rn <= 10) >= 3
            ORDER BY avg_pts DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()

        col_names = [
            "player_name", "player_id", "team", "games_l10",
            "avg_min", "avg_pts", "avg_reb", "avg_ast",
            "avg_stl", "avg_blk", "avg_tov", "avg_3pm",
        ]
        return [dict(zip(col_names, r)) for r in rows]

    except Exception as exc:
        log.warning("slate trends query failed: %s", exc)
        return []
