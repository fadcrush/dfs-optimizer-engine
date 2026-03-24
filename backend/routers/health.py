"""
System Health Router
====================
Endpoints that expose the runtime status of external signals and data-quality
indicators — Vegas enrichment, ownership model state, and env key presence.

Endpoints
---------
GET /api/health/vegas
    Returns Vegas enrichment health: key loaded, last run, games enriched, last error.

GET /api/health/env
    Returns which critical API keys are present (values never exposed).

GET /api/health/ownership
    Returns ownership model file ages and row counts.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/health", tags=["Health"])

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Vegas enrichment health
# ---------------------------------------------------------------------------

@router.get("/vegas")
async def vegas_health():
    """
    Return the current Vegas enrichment health state.

    Fields
    ------
    api_key_loaded
        ``true`` when THE_ODDS_API_KEY is set in the environment.
    last_successful_enrichment_at
        ISO-8601 UTC timestamp of the last successful enrichment run.
        ``null`` if no run has completed since the process started.
    games_enriched_on_last_run
        Number of raw games returned by TheOddsAPI on the last successful run.
    players_enriched_on_last_run
        Number of player rows that received non-null team_total on the last run.
    last_error
        Error message from the most recent failure, or ``null`` if last run succeeded.
    """
    try:
        from analysis.shared.vegas_enricher import get_vegas_health
        return get_vegas_health()
    except Exception as exc:
        log.warning("Vegas health check failed: %s", exc)
        return {
            "api_key_loaded": bool(os.getenv("THE_ODDS_API_KEY", "")),
            "last_successful_enrichment_at": None,
            "games_enriched_on_last_run": 0,
            "players_enriched_on_last_run": 0,
            "last_error": f"Health module unavailable: {exc}",
        }


# ---------------------------------------------------------------------------
# Environment key presence (values are never exposed)
# ---------------------------------------------------------------------------

_KEY_NAMES = [
    "THE_ODDS_API_KEY",
    "DATABASE_URL",
    "JWT_SECRET_KEY",
    "SUPABASE_JWT_SECRET",
    "SPORTSDATA_API_KEY",
    "BALLDONTLIE_API_KEY",
    "STRIPE_SECRET_KEY",
]


@router.get("/env")
async def env_health():
    """
    Return which critical environment variables are set.
    Values are never included — only boolean presence is reported.
    """
    return {
        "keys": {k: bool(os.getenv(k, "")) for k in _KEY_NAMES},
        "note": "Values are never exposed — only presence is checked.",
    }


# ---------------------------------------------------------------------------
# Ownership model status (lightweight alias — no auth required for monitoring)
# ---------------------------------------------------------------------------

@router.get("/ownership")
async def ownership_health():
    """
    Return ownership model file ages and training-data row counts.

    Delegates to ``GET /analytics/ownership-model-status`` logic without
    requiring authentication — safe for internal monitoring tools.
    """
    try:
        from services.analytics_service import get_ownership_model_status
        return get_ownership_model_status()
    except Exception as exc:
        log.warning("Ownership health check failed: %s", exc)
        return {"status": "unavailable", "error": str(exc)}


# ---------------------------------------------------------------------------
# Score integrity check — sanity-check stored dk_pts/fd_pts vs recomputed
# ---------------------------------------------------------------------------

@router.get("/score-integrity")
async def score_integrity():
    """
    Sample up to 200 recent game-log rows and compare stored dk_pts/fd_pts
    against the canonical recomputed values.

    Returns
    -------
    rows_sampled        : int
    dk_exact_match_pct  : float  (% where |stored - recomputed| < 0.05)
    fd_exact_match_pct  : float
    dk_null_pct         : float  (% of rows where stored dk_pts is NULL/0)
    fd_null_pct         : float
    status              : "ok" | "drift_detected" | "unavailable"
    """
    edge_db = _REPO_ROOT / "data" / "dfs_edge.duckdb"
    if not edge_db.exists():
        return {"status": "unavailable", "reason": "dfs_edge.duckdb not found"}

    try:
        from analysis.shared.db import get_conn
        from services.fantasy_scoring import score_game_row

        con = get_conn(edge_db, db_key="dfs_edge")
        rows = con.execute(
            """
            SELECT points, three_pointers, rebounds, assists, steals, blocks,
                   turnovers, fg_made, ft_made, dk_pts, fd_pts, minutes
            FROM player_game_logs
            WHERE minutes > 0
            ORDER BY game_date DESC
            LIMIT 200
            """
        ).fetchall()
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}

    if not rows:
        return {"status": "unavailable", "reason": "No rows in player_game_logs"}

    cols = ["points", "three_pointers", "rebounds", "assists", "steals",
            "blocks", "turnovers", "fg_made", "ft_made", "dk_pts", "fd_pts", "minutes"]

    dk_matches = dk_nulls = fd_matches = fd_nulls = 0
    n = len(rows)

    for row in rows:
        r = dict(zip(cols, row))
        stored_dk = r.get("dk_pts") or 0.0
        stored_fd = r.get("fd_pts") or 0.0
        calc_dk, calc_fd = score_game_row(r)

        if stored_dk == 0.0:
            dk_nulls += 1
        elif abs(stored_dk - calc_dk) < 0.05:
            dk_matches += 1

        if stored_fd == 0.0:
            fd_nulls += 1
        elif abs(stored_fd - calc_fd) < 0.05:
            fd_matches += 1

    dk_non_null = n - dk_nulls
    fd_non_null = n - fd_nulls

    result = {
        "rows_sampled": n,
        "dk_null_pct": round(100 * dk_nulls / n, 1),
        "fd_null_pct": round(100 * fd_nulls / n, 1),
        "dk_exact_match_pct": round(100 * dk_matches / dk_non_null, 1) if dk_non_null else None,
        "fd_exact_match_pct": round(100 * fd_matches / fd_non_null, 1) if fd_non_null else None,
    }

    # Flag as drift if >5% of non-null rows disagree with canonical formulas
    dk_ok = result["dk_exact_match_pct"] is None or result["dk_exact_match_pct"] >= 95
    fd_ok = result["fd_exact_match_pct"] is None or result["fd_exact_match_pct"] >= 95
    result["status"] = "ok" if (dk_ok and fd_ok) else "drift_detected"
    return result
