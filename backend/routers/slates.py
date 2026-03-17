"""
Slate Routes - Slate registry with live injury enrichment
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import Response

from services.auth import get_current_user
from services.file_service import get_file_path, get_slate_index_file, save_slate_file

# Ensure analysis package is importable
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from analysis.shared.injury_utils import (
    slug as _slug,
    load_injury_status,
    build_injury_summary,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/slates", tags=["Slates"])

def _user_id(user) -> str:
    if hasattr(user, "id"):
        return str(user.id)
    if isinstance(user, dict):
        return str(user.get("id", ""))
    return ""


def _load_index(user_id: str) -> list[dict]:
    index_file = get_slate_index_file(user_id)
    if not index_file.exists():
        return []
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_index(user_id: str, items: list[dict]) -> None:
    index_file = get_slate_index_file(user_id)
    index_file.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _extract_teams(csv_bytes: bytes) -> list[str]:
    """Parse CSV bytes and return sorted unique team abbreviations."""
    _TEAM_CANDIDATES = {"teamabbrev", "team_abbrev", "team"}
    try:
        text = csv_bytes.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        # Map lowercase → original fieldname so row.get() uses the real key
        orig_headers: list[str] = reader.fieldnames or []
        lower_to_orig = {h.lower().strip(): h for h in orig_headers}
        team_col_orig = next(
            (lower_to_orig[lh] for lh in lower_to_orig if lh in _TEAM_CANDIDATES),
            None,
        )
        if not team_col_orig:
            return []
        teams: set[str] = set()
        for row in reader:
            val = row.get(team_col_orig, "").strip()
            if val:
                teams.add(val)
        return sorted(teams)
    except Exception:
        return []


@router.get("")
async def list_slates(current_user=Depends(get_current_user)):
    slates = _load_index(_user_id(current_user))
    return {"success": True, "slates": slates, "total": len(slates)}


@router.post("/upload")
async def upload_slate(
    file: UploadFile = File(...),
    platform: str = "draftkings",
    sport: str = "nba",
    slate_date: str | None = None,
    current_user=Depends(get_current_user),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    # Read bytes once so we can (a) extract teams, (b) pass to save_slate_file
    raw_bytes = await file.read()
    teams = _extract_teams(raw_bytes)

    # Reset so save_slate_file can read the file again
    from io import BytesIO
    file.file = BytesIO(raw_bytes)

    user_id = _user_id(current_user)
    file_info = await save_slate_file(file, user_id=user_id)
    created_at = datetime.utcnow().isoformat() + "Z"
    date_value = slate_date or datetime.utcnow().date().isoformat()

    entry = {
        "id": file_info["file_id"],
        "platform": platform.lower(),
        "sport": sport.lower(),
        "date": date_value,
        "lock_time": None,
        "slate_type": "Main",
        "player_count": None,
        "created_at": created_at,
        "status": "active",
        "file_name": file_info["file_name"],
        "teams": teams,
    }

    slates = _load_index(user_id)
    slates.append(entry)
    _save_index(user_id, slates)

    return {"success": True, "slate": entry}


@router.get("/{slate_id}/teams")
async def get_slate_teams(slate_id: str, current_user=Depends(get_current_user)):
    """Return the unique team abbreviations extracted from a slate's CSV."""
    user_id = _user_id(current_user)
    slates = _load_index(user_id)
    slate = next((s for s in slates if s.get("id") == slate_id), None)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")

    teams = slate.get("teams", [])
    if not teams:
        file_path = get_file_path(slate.get("file_name", ""), "slate", user_id)
        if file_path.exists():
            teams = _extract_teams(file_path.read_bytes())

    return {"slate_id": slate_id, "teams": teams}


def _col(row: dict, *candidates: str) -> str:
    """Case-insensitive column lookup across multiple candidate names."""
    lower = {k.lower().strip(): v for k, v in row.items()}
    for c in candidates:
        val = lower.get(c.lower())
        if val is not None:
            return str(val).strip()
    return ""


def _parse_players(csv_bytes: bytes) -> list[dict]:
    """Parse a slate CSV into a list of player dicts (handles FD and DK formats)."""
    try:
        text = csv_bytes.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        players = []
        for row in reader:
            # Name: prefer Nickname (FD), then Name (DK), then strip ID prefix
            name = _col(row, "nickname", "name", "player name", "playername")
            if not name:
                continue
            # DK sometimes includes an ID prefix: "12345678:Josh Giddey"
            if ":" in name:
                name = name.split(":", 1)[1].strip()

            # Position: prefer Roster Position (DFS slot), fallback Position
            pos_raw = _col(row, "roster position", "position", "pos")
            # Normalize: take first position if multi (PF/SF → PF)
            pos = pos_raw.split("/")[0].strip() if pos_raw else ""

            team = _col(row, "team", "teamabbrev", "team_abbrev")
            opponent = _col(row, "opponent", "opp", "opponent team")

            # Game string (e.g. ATL@DEN or ATL@DEN 07:30PM ET)
            game = _col(row, "game", "game info", "gameinfo", "match")
            if not opponent and "@" in game:
                parts = game.split(" ")[0].split("@")
                # figure out which side is opponent
                if len(parts) == 2:
                    opponent = parts[1] if parts[0] == team else parts[0]

            # Salary
            sal_raw = _col(row, "salary", "sal").replace("$", "").replace(",", "")
            try:
                salary = int(float(sal_raw)) if sal_raw else 0
            except ValueError:
                salary = 0

            # FPPG / avg points
            fppg_raw = _col(row, "fppg", "avgpointspergame", "avg points per game", "avg_points_per_game", "fpts")
            try:
                fppg = round(float(fppg_raw), 2) if fppg_raw else 0.0
            except ValueError:
                fppg = 0.0

            # Value = FPPG / (salary / 1000)
            value = round(fppg / (salary / 1000), 2) if salary > 0 and fppg > 0 else 0.0

            # Injury
            inj = _col(row, "injury indicator", "injury_indicator", "injury status", "status")

            players.append({
                "name": name,
                "position": pos,
                "position_raw": pos_raw,
                "team": team,
                "opponent": opponent,
                "salary": salary,
                "fppg": fppg,
                "value": value,
                "injury": inj,
                "game": game.split(" ")[0] if game else "",
            })

        # Sort: by salary desc
        players.sort(key=lambda p: p["salary"], reverse=True)
        return players
    except Exception:
        return []


def _enrich_with_injuries(players: list[dict]) -> tuple[list[dict], dict]:
    """
    Cross-reference parsed slate players against the live injury DB.

    For each player, adds:
      - injury_status  : live status from DB (e.g. "OUT", "QUESTIONABLE"), or ""
      - injury_detail  : reason string from DB
      - injury_source  : "official_report" if from DB, "csv" if only from CSV column
      - status_changed : True if DB status differs from the CSV's injury column

    Returns (enriched_players, injury_summary).
    """
    try:
        injury_df = load_injury_status()
    except Exception as exc:
        log.warning("Could not load injury data for slate enrichment: %s", exc)
        for p in players:
            p["injury_status"] = p.get("injury", "")
            p["injury_detail"] = ""
            p["injury_source"] = "csv"
            p["status_changed"] = False
        return players, {"out_count": 0, "questionable_count": 0, "doubtful_count": 0,
                         "probable_count": 0, "out_players": [], "questionable_players": [],
                         "doubtful_players": [], "all_injuries": [], "changed_since_export": 0,
                         "last_updated": None}

    # Build slug → injury record map
    id_col = "player_id" if "player_id" in injury_df.columns else "player_name"
    injury_df_copy = injury_df.copy()
    injury_df_copy["_slug"] = injury_df_copy[id_col].apply(_slug)
    injury_map = {}
    for _, row in injury_df_copy.iterrows():
        injury_map[row["_slug"]] = {
            "status": str(row.get("status", "")).upper(),
            "detail": str(row.get("detail", "")),
            "team": str(row.get("team", "")),
        }

    changed_count = 0
    player_names = [p["name"] for p in players]

    for p in players:
        p_slug = _slug(p["name"])
        db_entry = injury_map.get(p_slug)
        csv_injury = str(p.get("injury", "")).upper().strip()

        if db_entry and db_entry["status"]:
            p["injury_status"] = db_entry["status"]
            p["injury_detail"] = db_entry["detail"]
            p["injury_source"] = "official_report"
            # Detect if status changed since the CSV was exported
            if csv_injury != db_entry["status"] and db_entry["status"] in ("OUT", "O", "QUESTIONABLE", "Q", "DOUBTFUL", "D"):
                p["status_changed"] = True
                changed_count += 1
            else:
                p["status_changed"] = False
        else:
            p["injury_status"] = csv_injury
            p["injury_detail"] = ""
            p["injury_source"] = "csv" if csv_injury else ""
            p["status_changed"] = False

    summary = build_injury_summary(player_names, injury_df)
    summary["changed_since_export"] = changed_count
    return players, summary


@router.get("/{slate_id}/players")
async def get_slate_players(slate_id: str, current_user=Depends(get_current_user)):
    """Return the full player pool from a slate's CSV, enriched with live injury data."""
    user_id = _user_id(current_user)
    slates = _load_index(user_id)
    slate = next((s for s in slates if s.get("id") == slate_id), None)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")

    file_path = get_file_path(slate.get("file_name", ""), "slate", user_id)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Slate file not found")

    players = _parse_players(file_path.read_bytes())
    players, injury_summary = _enrich_with_injuries(players)

    return {
        "slate_id": slate_id,
        "players": players,
        "total": len(players),
        "injury_summary": injury_summary,
    }


@router.get("/{slate_id}/download")
async def download_slate(slate_id: str, current_user=Depends(get_current_user)):
    """Return the raw CSV bytes of a saved slate."""
    user_id = _user_id(current_user)
    slates = _load_index(user_id)
    slate = next((s for s in slates if s.get("id") == slate_id), None)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")

    file_path = get_file_path(slate.get("file_name", ""), "slate", user_id)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Slate file not found on disk")

    data = file_path.read_bytes()
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{slate["file_name"]}"'},
    )


@router.delete("/{slate_id}")
async def delete_slate(slate_id: str, current_user=Depends(get_current_user)):
    user_id = _user_id(current_user)
    slates = _load_index(user_id)
    remaining = [s for s in slates if s.get("id") != slate_id]
    if len(remaining) == len(slates):
        raise HTTPException(status_code=404, detail="Slate not found")

    _save_index(user_id, remaining)

    # Best-effort file cleanup
    for slate in slates:
        if slate.get("id") == slate_id:
            file_name = slate.get("file_name")
            if file_name:
                file_path = get_file_path(file_name, "slate", user_id)
                if file_path.exists():
                    file_path.unlink()
            break

    return {"success": True}
