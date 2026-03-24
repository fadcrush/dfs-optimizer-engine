"""
Injuries Router
================
REST endpoints for on-demand injury data (complements the SSE stream in events.py).

Endpoints:
  GET  /api/injuries/summary                  — current injury report as JSON
  GET  /api/injuries/team/{abbrev}            — injuries for a specific team
  POST /api/injuries/refresh                  — force an injury data refresh
  GET  /api/injuries/state                    — full probabilistic injury state snapshot
  GET  /api/injuries/slate                    — slate-focused injury board (enriched)
  GET  /api/injuries/beneficiaries            — beneficiaries (by player or top-N)
  POST /api/injuries/overrides                — set a user injury override
  GET  /api/injuries/overrides                — list user overrides
  DELETE /api/injuries/overrides/{player_id}  — remove a user override
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.auth import get_current_user

# Ensure analysis package is importable
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from analysis.shared.injury_utils import (
    load_injury_status,
    invalidate_cache,
)
from analysis.core.injury_intelligence import InjuryIntelligenceService
from analysis.shared.db import get_conn, write_lock

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/injuries", tags=["Injuries"])

_refresh_lock = threading.Lock()
_intelligence = InjuryIntelligenceService()
_NBA_NEWS_DB = _root / "data" / "nba_news.duckdb"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class OverrideRequest(BaseModel):
    player_id: str
    player_name: Optional[str] = ""
    slate_id: Optional[str] = ""
    action: str  # "favor" | "neutral" | "fade" | "exclude"
    projection_bump: Optional[float] = 0.0
    ownership_adjustment: Optional[float] = 0.0
    notes: Optional[str] = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _ts(val: Any) -> str:
    if val is None:
        return ""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _hash_id(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


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
    state_df = _intelligence.load_player_states_df()
    state_lookup: dict[str, dict] = {}
    if not state_df.empty:
        for _, state_row in state_df.iterrows():
            state_lookup[str(state_row.get("player_id") or "").strip().lower()] = state_row.to_dict()

    for _, row in df.iterrows():
        status = str(row.get("status", "")).upper()
        player_id = str(row.get("player_id") or "").strip().lower()
        state = state_lookup.get(player_id, {})
        record = {
            "player_id": str(row.get("player_id", "")),
            "player_name": str(row.get("player_name", row.get("player_id", ""))),
            "status": status,
            "detail": str(row.get("detail", "")),
            "team": str(row.get("team", "")),
            "game_date": str(row.get("game_date", "")),
            "p_play": float(state.get("p_play", 1.0 if status not in ("OUT", "O") else 0.0)),
            "p_limited": float(state.get("p_limited", 0.0)),
            "p_late_scratch": float(state.get("p_late_scratch", 0.0)),
            "confidence_score": float(state.get("confidence_score", 0.0)),
            "news_quality_score": float(state.get("news_quality_score", 0.0)),
            "source_agreement_score": float(state.get("source_agreement_score", 0.0)),
            "staleness_score": float(state.get("staleness_score", 0.0)),
            "expected_minutes_low": float(state.get("expected_minutes_low", 0.0)),
            "expected_minutes_mid": float(state.get("expected_minutes_mid", 0.0)),
            "expected_minutes_high": float(state.get("expected_minutes_high", 0.0)),
            "last_event_at": str(state.get("last_event_at", "")),
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


@router.get("/state")
async def injury_state_snapshot():
    """Return the current probabilistic injury state snapshot."""
    df = _intelligence.load_player_states_df()
    if df.empty:
        return {"players": [], "count": 0}
    records = df.to_dict(orient="records")
    for record in records:
        if hasattr(record.get("last_event_at"), "isoformat"):
            record["last_event_at"] = record["last_event_at"].isoformat()
        if hasattr(record.get("updated_at"), "isoformat"):
            record["updated_at"] = record["updated_at"].isoformat()
    return {"players": records, "count": len(records)}


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
            from scripts.jobs.fetch_nba_injuries import ensure_current
            ensure_current()
            invalidate_cache()
            injury_df = load_injury_status(force=True)
            sync_summary = _intelligence.sync_current_injuries(injury_df, source="official_report")
            log.info("Manual injury intelligence sync completed: %s", sync_summary)
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


# ---------------------------------------------------------------------------
# NEW: Slate-focused injury board
# ---------------------------------------------------------------------------

@router.get("/slate")
async def slate_injury_board(
    slate_date: Optional[str] = None,
    min_impact: float = 0.15,
):
    """
    Return the injury board for the current (or given) slate.

    Enriches player_injury_state with top beneficiaries and scenarios.
    Filters to players with non-trivial probability of not playing fully
    (1 - p_play >= min_impact).

    Query params:
      slate_date  : ISO-date string (default: today)
      min_impact  : minimum (1 - p_play) threshold (default 0.15)
    """
    if not _NBA_NEWS_DB.exists():
        return {"injuries": [], "count": 0, "slate_date": slate_date or "today"}

    conn = get_conn(_NBA_NEWS_DB, db_key="nba_news")

    states = conn.execute("""
        SELECT
            s.player_id, s.player_name, s.team_id, s.game_id,
            s.current_status, s.p_play, s.p_limited, s.p_late_scratch,
            s.p_start, s.expected_minutes_low, s.expected_minutes_mid,
            s.expected_minutes_high, s.confidence_score, s.news_quality_score,
            s.staleness_score, s.last_event_at, s.position, s.arbitration_context,
            e.detail_text, e.source, e.event_classification,
            e.market_impact_estimate, e.event_priority_score
        FROM player_injury_state s
        LEFT JOIN (
            SELECT DISTINCT ON (player_id)
                player_id, detail_text, source, event_classification,
                market_impact_estimate, event_priority_score
            FROM injury_events
            ORDER BY player_id, event_priority_score DESC, observed_at DESC
        ) e ON s.player_id = e.player_id
        WHERE (1.0 - s.p_play) >= ?
        ORDER BY (1.0 - s.p_play) * s.confidence_score DESC
    """, [min_impact]).df()

    if states.empty:
        return {"injuries": [], "count": 0}

    injured_ids = states["player_id"].tolist()
    ph = ", ".join("?" for _ in injured_ids)

    beneficiaries_df = conn.execute(f"""
        SELECT
            player_id, beneficiary_player_id, beneficiary_name, position,
            delta_minutes, delta_usage, delta_assist_rate, delta_rebound_rate,
            p_start, p_close, volatility_uplift, confidence, rank_score,
            reason_codes, scenario_name
        FROM injury_beneficiaries
        WHERE player_id IN ({ph})
        ORDER BY player_id, rank_score DESC
    """, injured_ids).df()

    scenarios_df = conn.execute(f"""
        SELECT player_id, scenario_name, scenario_probability,
               expected_minutes, usage_multiplier, volatility_multiplier
        FROM injury_scenarios
        WHERE player_id IN ({ph})
    """, injured_ids).df()

    injuries = []
    for _, row in states.iterrows():
        pid = str(row["player_id"])
        p_out = max(0.0, 1.0 - _safe_float(row.get("p_play"), 1.0))
        confidence = _safe_float(row.get("confidence_score"), 0.0)

        status = str(row.get("current_status") or "")
        if status == "OUT" and confidence >= 0.7:
            urgency = "critical"
        elif status in ("OUT", "DOUBTFUL") or p_out >= 0.5:
            urgency = "high"
        elif status == "GTD" or p_out >= 0.25:
            urgency = "moderate"
        else:
            urgency = "low"

        player_bens = beneficiaries_df[beneficiaries_df["player_id"] == pid].head(4) \
            if not beneficiaries_df.empty else []

        bens = []
        for _, b in (player_bens.iterrows() if not isinstance(player_bens, list) else []):
            reason_raw = b.get("reason_codes", "[]")
            try:
                reasons = json.loads(reason_raw) if isinstance(reason_raw, str) else reason_raw or []
            except Exception:
                reasons = []
            bens.append({
                "player_id": str(b.get("beneficiary_player_id", "")),
                "player_name": str(b.get("beneficiary_name", "")),
                "position": str(b.get("position", "")),
                "delta_minutes": _safe_float(b.get("delta_minutes")),
                "delta_usage": _safe_float(b.get("delta_usage")),
                "delta_assist_rate": _safe_float(b.get("delta_assist_rate")),
                "delta_rebound_rate": _safe_float(b.get("delta_rebound_rate")),
                "p_start": _safe_float(b.get("p_start")),
                "p_close": _safe_float(b.get("p_close")),
                "volatility_uplift": _safe_float(b.get("volatility_uplift")),
                "confidence": _safe_float(b.get("confidence")),
                "rank_score": _safe_float(b.get("rank_score")),
                "reason_codes": reasons,
                "scenario_name": str(b.get("scenario_name", "OUT")),
            })

        player_scen = scenarios_df[scenarios_df["player_id"] == pid] \
            if not scenarios_df.empty else []

        scenarios = []
        for _, s in (player_scen.iterrows() if not isinstance(player_scen, list) else []):
            scenarios.append({
                "scenario_name": str(s.get("scenario_name", "")),
                "probability": _safe_float(s.get("scenario_probability")),
                "expected_minutes": _safe_float(s.get("expected_minutes")),
                "usage_multiplier": _safe_float(s.get("usage_multiplier")),
                "volatility_multiplier": _safe_float(s.get("volatility_multiplier")),
            })

        arb_ctx: dict = {}
        try:
            raw_arb = row.get("arbitration_context")
            if raw_arb:
                arb_ctx = json.loads(raw_arb) if isinstance(raw_arb, str) else raw_arb
        except Exception:
            pass

        injuries.append({
            "player_id": pid,
            "player_name": str(row.get("player_name", pid)),
            "team": str(row.get("team_id", "")),
            "position": str(row.get("position", "")),
            "status": status,
            "detail": str(arb_ctx.get("detail", row.get("detail_text", ""))),
            "source": str(row.get("source", arb_ctx.get("dominant_source", ""))),
            "event_classification": str(row.get("event_classification", "")),
            "p_play": _safe_float(row.get("p_play"), 1.0),
            "p_limited": _safe_float(row.get("p_limited")),
            "p_late_scratch": _safe_float(row.get("p_late_scratch")),
            "p_start": _safe_float(row.get("p_start")),
            "expected_minutes_low": _safe_float(row.get("expected_minutes_low")),
            "expected_minutes_mid": _safe_float(row.get("expected_minutes_mid")),
            "expected_minutes_high": _safe_float(row.get("expected_minutes_high")),
            "confidence_score": _safe_float(row.get("confidence_score")),
            "news_quality_score": _safe_float(row.get("news_quality_score")),
            "staleness_score": _safe_float(row.get("staleness_score")),
            "market_impact_estimate": _safe_float(row.get("market_impact_estimate")),
            "last_event_at": _ts(row.get("last_event_at")),
            "urgency": urgency,
            "scenarios": scenarios,
            "beneficiaries": bens,
        })

    return {
        "injuries": injuries,
        "count": len(injuries),
        "slate_date": slate_date or datetime.now(timezone.utc).date().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# NEW: Beneficiaries endpoint
# ---------------------------------------------------------------------------

@router.get("/beneficiaries")
async def injury_beneficiaries(
    player_id: Optional[str] = None,
    limit: int = 20,
):
    """
    Return injury beneficiaries.

    When player_id is provided: returns beneficiaries for that specific injured player.
    When omitted: returns the top-N beneficiaries across all injured players.

    Query params:
      player_id : injured player's ID (optional)
      limit     : max results (default 20)
    """
    if not _NBA_NEWS_DB.exists():
        return {"beneficiaries": [], "count": 0}

    conn = get_conn(_NBA_NEWS_DB, db_key="nba_news")

    if player_id:
        rows = conn.execute("""
            SELECT b.*, s.player_name AS injured_player_name, s.current_status,
                   s.p_play, s.team_id
            FROM injury_beneficiaries b
            LEFT JOIN player_injury_state s ON b.player_id = s.player_id
            WHERE b.player_id = ?
            ORDER BY b.rank_score DESC
            LIMIT ?
        """, [player_id.strip().lower(), limit]).df()
    else:
        rows = conn.execute("""
            SELECT b.*, s.player_name AS injured_player_name, s.current_status,
                   s.p_play, s.team_id
            FROM injury_beneficiaries b
            LEFT JOIN player_injury_state s ON b.player_id = s.player_id
            ORDER BY b.rank_score DESC
            LIMIT ?
        """, [limit]).df()

    if rows.empty:
        return {"beneficiaries": [], "count": 0}

    results = []
    for _, row in rows.iterrows():
        reason_raw = row.get("reason_codes", "[]")
        try:
            reasons = json.loads(reason_raw) if isinstance(reason_raw, str) else reason_raw or []
        except Exception:
            reasons = []
        results.append({
            "beneficiary_id": str(row.get("beneficiary_id", "")),
            "injured_player_id": str(row.get("player_id", "")),
            "injured_player_name": str(row.get("injured_player_name", "")),
            "injured_status": str(row.get("current_status", "")),
            "injured_p_play": _safe_float(row.get("p_play")),
            "team": str(row.get("team_id", "")),
            "beneficiary_player_id": str(row.get("beneficiary_player_id", "")),
            "beneficiary_name": str(row.get("beneficiary_name", "")),
            "position": str(row.get("position", "")),
            "delta_minutes": _safe_float(row.get("delta_minutes")),
            "delta_usage": _safe_float(row.get("delta_usage")),
            "delta_assist_rate": _safe_float(row.get("delta_assist_rate")),
            "delta_rebound_rate": _safe_float(row.get("delta_rebound_rate")),
            "p_start": _safe_float(row.get("p_start")),
            "p_close": _safe_float(row.get("p_close")),
            "volatility_uplift": _safe_float(row.get("volatility_uplift")),
            "confidence": _safe_float(row.get("confidence")),
            "rank_score": _safe_float(row.get("rank_score")),
            "scenario_name": str(row.get("scenario_name", "OUT")),
            "reason_codes": reasons,
            "generated_at": _ts(row.get("generated_at")),
        })

    return {"beneficiaries": results, "count": len(results)}


# ---------------------------------------------------------------------------
# NEW: User injury overrides
# ---------------------------------------------------------------------------

@router.post("/overrides")
async def set_injury_override(
    req: OverrideRequest,
    current_user=Depends(get_current_user),
):
    """
    Set a user injury override for a player.

    Actions:
      favor   — projection bump + ownership uplift
      neutral — removes any existing override
      fade    — projection reduction + ownership suppression
      exclude — remove from optimizer pool
    """
    if req.action not in ("favor", "neutral", "fade", "exclude"):
        raise HTTPException(status_code=422, detail="action must be favor/neutral/fade/exclude")

    user_id = str(
        getattr(current_user, "id", "") or
        getattr(current_user, "user_id", "") or
        "anonymous"
    )
    if not _NBA_NEWS_DB.exists():
        raise HTTPException(status_code=500, detail="Injury database not available")

    conn = get_conn(_NBA_NEWS_DB, db_key="nba_news")
    override_id = _hash_id(user_id, req.player_id, req.slate_id or "global")

    proj_bump = req.projection_bump or 0.0
    own_adj = req.ownership_adjustment or 0.0
    if proj_bump == 0.0:
        if req.action == "favor":
            proj_bump = 0.10
        elif req.action == "fade":
            proj_bump = -0.08
    if own_adj == 0.0:
        if req.action == "favor":
            own_adj = 0.05
        elif req.action == "fade":
            own_adj = -0.04

    exclude = req.action == "exclude"
    now = datetime.now(timezone.utc)

    with write_lock("nba_news"):
        conn.execute(
            "DELETE FROM user_injury_overrides WHERE override_id = ?",
            [override_id],
        )
        if req.action != "neutral":
            conn.execute(
                """
                INSERT INTO user_injury_overrides (
                    override_id, user_id, slate_id, player_id, player_name,
                    action, projection_bump, ownership_adjustment,
                    exclude_from_pool, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    override_id, user_id, req.slate_id or "", req.player_id,
                    req.player_name or "", req.action, proj_bump, own_adj,
                    exclude, req.notes or "", now, now,
                ],
            )

    return {
        "status": "ok",
        "override_id": override_id,
        "action": req.action,
        "player_id": req.player_id,
        "projection_bump": proj_bump,
        "ownership_adjustment": own_adj,
        "exclude_from_pool": exclude,
    }


@router.get("/overrides")
async def get_injury_overrides(
    slate_id: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Return all active user injury overrides (for a slate or globally)."""
    user_id = str(
        getattr(current_user, "id", "") or
        getattr(current_user, "user_id", "") or
        "anonymous"
    )
    if not _NBA_NEWS_DB.exists():
        return {"overrides": [], "count": 0}

    conn = get_conn(_NBA_NEWS_DB, db_key="nba_news")
    if slate_id:
        rows = conn.execute(
            "SELECT * FROM user_injury_overrides WHERE user_id = ? AND (slate_id = ? OR slate_id = '') ORDER BY updated_at DESC",
            [user_id, slate_id],
        ).df()
    else:
        rows = conn.execute(
            "SELECT * FROM user_injury_overrides WHERE user_id = ? ORDER BY updated_at DESC",
            [user_id],
        ).df()

    if rows.empty:
        return {"overrides": [], "count": 0}

    results = rows.to_dict(orient="records")
    for r in results:
        for k in ("created_at", "updated_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
    return {"overrides": results, "count": len(results)}


@router.delete("/overrides/{player_id_path}")
async def delete_injury_override(
    player_id_path: str,
    slate_id: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Remove a specific user injury override."""
    user_id = str(
        getattr(current_user, "id", "") or
        getattr(current_user, "user_id", "") or
        "anonymous"
    )
    if not _NBA_NEWS_DB.exists():
        return {"status": "ok", "deleted": 0}

    conn = get_conn(_NBA_NEWS_DB, db_key="nba_news")
    pid = player_id_path.strip().lower()

    with write_lock("nba_news"):
        if slate_id:
            conn.execute(
                "DELETE FROM user_injury_overrides WHERE user_id = ? AND player_id = ? AND slate_id = ?",
                [user_id, pid, slate_id],
            )
        else:
            conn.execute(
                "DELETE FROM user_injury_overrides WHERE user_id = ? AND player_id = ?",
                [user_id, pid],
            )

    return {"status": "ok", "deleted": 1, "player_id": pid}
