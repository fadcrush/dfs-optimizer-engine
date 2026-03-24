"""
Player Trends Router
====================
Provides last-5 / last-10 / season analytics for individual NBA players
drawn from ``player_game_logs`` in dfs_edge.duckdb.

Endpoints
---------
GET /api/player-trends/search?q=<query>[&limit=20]
    Search players by name substring.

GET /api/player-trends/player?name=<full_name>[&games=10]
    Full trend profile for a single player:
    {last5, last10, season, recent_games, …}

GET /api/player-trends/slate[?limit=200]
    Lightweight last-10 averages for all active players —
    useful for a sortable slate-wide research table.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from services.auth import get_current_user
from services.player_trends_service import (
    get_player_trends,
    get_slate_trends,
    search_players,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/player-trends", tags=["Player Trends"])


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search")
async def player_search(
    q: str = Query(min_length=2, description="Player name search query (min 2 chars)"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results to return"),
    current_user=Depends(get_current_user),
):
    """Return players whose names match the search query.

    Example::

        GET /api/player-trends/search?q=LeBron
    """
    results = search_players(q, limit=limit)
    return {"players": results, "count": len(results)}


# ---------------------------------------------------------------------------
# Individual player trends
# ---------------------------------------------------------------------------

@router.get("/player")
async def player_trends(
    name: str = Query(description="Player full name (case-insensitive)"),
    games: int = Query(
        10,
        ge=5,
        le=25,
        description="Number of recent game-log rows to include in response",
    ),
    projection_dk: float = Query(
        default=None,
        description=(
            "Current DraftKings projection in FPTS.  When provided, the response "
            "includes ``projection_delta`` showing how the model compares to L5/L10 trends."
        ),
    ),
    projection_fd: float = Query(
        default=None,
        description="Current FanDuel projection in FPTS (same as projection_dk but for FD).",
    ),
    current_user=Depends(get_current_user),
):
    """Return the full trend profile for *name*.

    The response contains:

    * ``last5``   — averages over the most recent 5 qualifying games
    * ``last10``  — averages over the most recent 10 qualifying games
    * ``season``  — averages for all games in the detected current season
    * ``recent_games`` — per-game log rows (up to *games* entries, newest first)
    * ``projection_delta`` — delta vs L5/L10 for DK and FD (only when projection params supplied)

    ``projection_delta`` signal values:

    * ``"under_projecting"`` — model is below trend (potential value)
    * ``"over_projecting"``  — model is above trend (risky)
    * ``"aligned"``          — model within ±1.5 FPTS of trend

    All averages are **historical descriptive statistics**, not projections.

    Example::

        GET /api/player-trends/player?name=LeBron+James&projection_dk=48.5
    """
    if not name or not name.strip():
        raise HTTPException(status_code=422, detail="'name' query parameter is required")

    data = get_player_trends(
        name.strip(),
        recent_games_limit=games,
        projection_dk=projection_dk,
        projection_fd=projection_fd,
    )
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No qualifying game logs found for player '{name.strip()}'. "
                   "The player may not be in the current season's logs, or all "
                   "recorded games have 0 minutes (DNP).",
        )
    return data


# ---------------------------------------------------------------------------
# Slate-wide table
# ---------------------------------------------------------------------------

@router.get("/slate")
async def slate_trends(
    limit: int = Query(
        200,
        ge=10,
        le=600,
        description="Maximum players to return (ordered by avg points desc)",
    ),
    current_user=Depends(get_current_user),
):
    """Return last-10 stat averages for all active players.

    Players with fewer than 3 qualifying games are excluded.
    Results are ordered by last-10 average points descending.

    Note: Fantasy scores are not included in this endpoint to keep response
    size manageable.  Use ``/player`` for per-player DK/FD breakdowns.
    """
    players = get_slate_trends(limit=limit)
    return {"players": players, "count": len(players)}
