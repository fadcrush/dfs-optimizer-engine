"""
Server-Sent Events Router
=========================
Streams real-time injury + lineup-status updates to connected clients.

Endpoint: GET /api/events/injuries
  Sends ``data: {...}\\n\\n`` messages every 60 seconds (or on forced push).

Frontend usage::

    const es = new EventSource('/api/events/injuries');
    es.onmessage = (e) => {
        const data = JSON.parse(e.data);
        // { type: 'injury_update', players: [...], timestamp: '...' }
    };
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])

# Polling interval for the SSE stream (seconds between pushes)
_POLL_INTERVAL = int(__import__("os").getenv("SSE_INJURY_INTERVAL", "60"))


async def _injury_event_generator() -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE-formatted text every _POLL_INTERVAL seconds.
    Each event contains the latest injury data from nba_news.duckdb.
    """
    while True:
        payload = _build_injury_payload()
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(_POLL_INTERVAL)


def _build_injury_payload() -> dict:
    """Read current injury state from nba_news.duckdb and return serialisable dict.

    Prefers the probabilistic ``player_injury_state`` + ``injury_scenarios``
    tables produced by the injury-intelligence service.  Falls back to the raw
    ``vw_nba_injury_status`` / ``nba_injury_report`` snapshot on any error so
    the SSE stream continues to function during a data gap.
    """
    try:
        import sys
        from pathlib import Path
        _root = Path(__file__).resolve().parent.parent.parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from analysis.shared.db import get_conn

        db_path = _root / "data" / "nba_news.duckdb"
        if not db_path.exists():
            return _empty_payload("nba_news.duckdb not found")

        conn = get_conn(db_path, db_key="nba_news")

        # ── Try probabilistic state (injury-intelligence tables) ────────────
        try:
            state_rows = conn.execute(
                """
                SELECT s.player_id, s.player_name, s.team_id,
                       s.current_status, s.p_play, s.p_limited, s.p_late_scratch,
                       s.expected_minutes_low, s.expected_minutes_mid,
                       s.expected_minutes_high, s.p_start,
                       s.confidence_score, s.staleness_score, s.updated_at
                FROM   player_injury_state s
                ORDER  BY s.updated_at DESC
                LIMIT  300
                """
            ).fetchall()
            state_cols = [
                "player_id", "player_name", "team_id", "current_status",
                "p_play", "p_limited", "p_late_scratch",
                "expected_minutes_low", "expected_minutes_mid", "expected_minutes_high",
                "p_start", "confidence_score", "staleness_score", "updated_at",
            ]
            players_map: dict[str, dict] = {}
            for row in state_rows:
                rec = dict(zip(state_cols, row))
                if hasattr(rec.get("updated_at"), "isoformat"):
                    rec["updated_at"] = rec["updated_at"].isoformat()
                rec["scenarios"] = []
                players_map[rec["player_id"]] = rec

            # Attach scenarios
            if players_map:
                scenario_rows = conn.execute(
                    """
                    SELECT player_id, scenario_name, scenario_probability,
                           expected_minutes, usage_multiplier, p_start AS scenario_p_start
                    FROM   injury_scenarios
                    WHERE  player_id IN (SELECT player_id FROM player_injury_state)
                    ORDER  BY player_id, scenario_probability DESC
                    """
                ).fetchall()
                scen_cols = [
                    "player_id", "scenario_name", "scenario_probability",
                    "expected_minutes", "usage_multiplier", "scenario_p_start",
                ]
                for sr in scenario_rows:
                    s = dict(zip(scen_cols, sr))
                    pid = s.pop("player_id")
                    if pid in players_map:
                        players_map[pid]["scenarios"].append(s)

            players = list(players_map.values())
            return {
                "type": "injury_update",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "players": players,
                "count": len(players),
                "source": "player_injury_state",
            }

        except Exception as exc:
            log.debug("SSE: probabilistic state unavailable (%s) — falling back to snapshot", exc)

        # ── Fallback: raw snapshot view ─────────────────────────────────────
        try:
            rows = conn.execute(
                """
                SELECT player_name, status, detail, game_date AS report_date, team
                FROM   vw_nba_injury_status
                ORDER  BY game_date DESC
                LIMIT  200
                """
            ).fetchall()
        except Exception:
            rows = conn.execute(
                """
                SELECT player_name, UPPER(status) AS status,
                       reason AS detail, game_date AS report_date, team
                FROM   nba_injury_report
                ORDER  BY fetched_at DESC
                LIMIT  200
                """
            ).fetchall()
        cols = ["player_name", "status", "detail", "report_date", "team"]
        players = [dict(zip(cols, row)) for row in rows]
        for p in players:
            if hasattr(p.get("report_date"), "isoformat"):
                p["report_date"] = p["report_date"].isoformat()
        return {
            "type": "injury_update",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "players": players,
            "count": len(players),
            "source": "snapshot",
        }
    except Exception as exc:
        log.warning("SSE injury payload error: %s", exc)
        return _empty_payload(str(exc))


def _empty_payload(reason: str) -> dict:
    return {
        "type": "injury_update",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "players": [],
        "count": 0,
        "error": reason,
    }


@router.get("/injuries")
async def injury_events():
    """
    Server-Sent Events stream of NBA injury updates.

    The client receives an event every ``SSE_INJURY_INTERVAL`` seconds
    (default 60 s). Each message payload is JSON with::

        {
          "type": "injury_update",
          "timestamp": "2025-01-15T14:00:00Z",
          "players": [{"player_name": "...", "status": "OUT", ...}, ...],
          "count": 12
        }
    """
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # disable nginx buffering
    }
    return StreamingResponse(
        _injury_event_generator(),
        headers=headers,
        media_type="text/event-stream",
    )


@router.get("/ping")
async def events_ping():
    """Health check for the events endpoint."""
    return {"status": "ok", "interval_seconds": _POLL_INTERVAL}
