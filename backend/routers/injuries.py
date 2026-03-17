"""
Injuries Router
================
REST endpoints for on-demand injury data (complements the SSE stream in events.py).

Endpoints:
  GET  /api/injuries/summary       — current injury report as JSON
  GET  /api/injuries/team/{abbrev} — injuries for a specific team
  POST /api/injuries/refresh       — force an injury data refresh
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from services.auth import get_current_user

# Ensure analysis package is importable
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from analysis.shared.injury_utils import (
    load_injury_status,
    invalidate_cache,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/injuries", tags=["Injuries"])

_refresh_lock = threading.Lock()


@router.get("/summary")
async def injury_summary(sport: str = "nba"):
    """
    Return the current injury report as JSON (non-SSE, one-shot).

    Query params:
      sport : "nba" (only NBA supported currently)
    """
    if sport.lower() != "nba":
        return {"players": [], "out_count": 0, "questionable_count": 0, "total": 0}

    try:
        df = load_injury_status()
    except Exception as exc:
        log.warning("Could not load injury data: %s", exc)
        return {"players": [], "out_count": 0, "questionable_count": 0, "total": 0, "error": str(exc)}

    players = []
    out_count = 0
    q_count = 0
    d_count = 0

    for _, row in df.iterrows():
        status = str(row.get("status", "")).upper()
        record = {
            "player_name": str(row.get("player_name", row.get("player_id", ""))),
            "status": status,
            "detail": str(row.get("detail", "")),
            "team": str(row.get("team", "")),
            "game_date": str(row.get("game_date", "")),
        }
        players.append(record)
        if status in ("OUT", "O"):
            out_count += 1
        elif status in ("QUESTIONABLE", "Q", "GTD"):
            q_count += 1
        elif status in ("DOUBTFUL", "D"):
            d_count += 1

    return {
        "players": players,
        "total": len(players),
        "out_count": out_count,
        "questionable_count": q_count,
        "doubtful_count": d_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/team/{team_abbrev}")
async def team_injuries(team_abbrev: str):
    """Return injuries for a specific team abbreviation (e.g. LAL, BOS)."""
    try:
        df = load_injury_status()
    except Exception as exc:
        log.warning("Could not load injury data: %s", exc)
        return {"team": team_abbrev.upper(), "players": []}

    team_upper = team_abbrev.upper()
    players = []
    for _, row in df.iterrows():
        if str(row.get("team", "")).upper() == team_upper:
            players.append({
                "player_name": str(row.get("player_name", row.get("player_id", ""))),
                "status": str(row.get("status", "")).upper(),
                "detail": str(row.get("detail", "")),
                "game_date": str(row.get("game_date", "")),
            })

    return {"team": team_upper, "players": players, "count": len(players)}


@router.post("/refresh")
async def force_refresh(current_user=Depends(get_current_user)):
    """
    Trigger an on-demand injury data refresh from the official source.
    Requires authentication. Returns immediately; refresh runs in background.
    """
    if not _refresh_lock.acquire(blocking=False):
        return {"status": "already_running", "message": "A refresh is already in progress"}

    def _do_refresh():
        try:
            from scripts.fetch_nba_injuries import ensure_current
            ensure_current()
            invalidate_cache()
            log.info("Manual injury refresh completed")
        except Exception as exc:
            log.warning("Manual injury refresh failed: %s", exc)
        finally:
            _refresh_lock.release()

    thread = threading.Thread(target=_do_refresh, daemon=True)
    thread.start()

    return {
        "status": "started",
        "message": "Injury refresh started in background",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
