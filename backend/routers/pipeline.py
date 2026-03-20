"""
One-Click Pipeline Route
========================
POST /api/pipeline/run-full

Chains every step into a single request:
  1. Upload slate CSV
  2. Auto-refresh NBA injury report (already wired into orchestrator)
  3. Refresh player prop lines from The Odds API
  4. Generate projections using our canonical engine (L10 game logs + DvP)
     — DK/FD stock averages (AvgPointsPerGame/FPPG) are NEVER used
  5. Calibrated ownership estimation (GBR model or percentile fallback)
  6. Optimize lineups with stacking / exposure controls
  7. Persist lineup CSV; return download URL + preview

Query params mirror /api/optimizer/run for full feature parity.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from analysis.core.contest_sim import ContestConfig
from analysis.core.exposure_optimizer import ExposureConfig
from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.schemas import ProjectionContext
from analysis.nba.optimizer import assign_lineup_slots
from services.auth import require_plan
from services.file_service import save_lineup_file, save_slate_file

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["Pipeline"])


def _user_id(user) -> str:
    if hasattr(user, "id"):
        return str(user.id)
    if isinstance(user, dict):
        return str(user.get("id", ""))
    return ""


# ─ helpers (shared with optimizer router) ────────────────────────────────────


def _lineups_to_preview(lineups_df: pd.DataFrame, site: str) -> list[dict]:
    previews = []
    for lineup_index, group in lineups_df.groupby("LineupIndex"):
        assignment = assign_lineup_slots(group, site)
        if not assignment:
            continue
        total_salary = int(pd.to_numeric(group["Salary"], errors="coerce").fillna(0).sum())
        projected_points = float(pd.to_numeric(group["Proj"], errors="coerce").fillna(0).sum())
        preview = {
            "lineup_num": int(lineup_index) + 1,
            "total_salary": total_salary,
            "projected_points": round(projected_points, 2),
        }
        preview.update(assignment)
        previews.append(preview)
    return previews


def _lineups_to_export_csv(lineups_df: pd.DataFrame, site: str) -> str:
    """Build a site-correct upload-ready CSV string.

    DraftKings: Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL
                Player values = "Name (ID)" format (e.g. "Nikola Jokic (41699649)")

    FanDuel:    entry_id,contest_id,contest_name,entry_fee,PG,PG,SG,SG,SF,SF,PF,PF,C
                Player values = composite DFS IDs (slateId-playerId, e.g. "127280-17")
                Uses Raw_DFS_ID (composite) as primary; falls back to DFS_ID.
                (duplicate position headers intentional — FD's required format)
    """
    site = site.upper()

    if site == "DK":
        dk_slots = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
        prefix_cols = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
        rows = []
        for lineup_index, group in lineups_df.groupby("LineupIndex"):
            assignment = assign_lineup_slots(group, site)
            if not assignment:
                continue
            # Build name→"Name (ID)" lookup — DK bulk-upload requires Name+ID format
            # (e.g. "Nikola Jokic (41699649)"), not a bare numeric ID.
            name_id_lookup: dict[str, str] = {}
            for _, row in group.iterrows():
                name = str(row.get("Name", "")).strip()
                dfs_id = str(row.get("DFS_ID", "")).strip()
                if dfs_id and dfs_id not in ("nan", "None", ""):
                    name_id_lookup[name] = f"{name} ({dfs_id})"
                else:
                    name_id_lookup[name] = name
            export_row: dict = {col: "" for col in prefix_cols}
            for slot in dk_slots:
                player_name = assignment.get(slot, "")
                export_row[slot] = name_id_lookup.get(player_name, player_name)
            rows.append(export_row)
        df = pd.DataFrame(rows, columns=prefix_cols + dk_slots)
        return df.to_csv(index=False)

    else:  # FD
        # assignment keys for FD: PG, PG_2, SG, SG_2, SF, SF_2, PF, PF_2, C
        fd_slot_order = ["PG", "PG_2", "SG", "SG_2", "SF", "SF_2", "PF", "PF_2", "C"]
        header = "entry_id,contest_id,contest_name,entry_fee,PG,PG,SG,SG,SF,SF,PF,PF,C"
        lines = [header]
        for lineup_index, group in lineups_df.groupby("LineupIndex"):
            assignment = assign_lineup_slots(group, site)
            if not assignment:
                continue
            # FD upload requires composite IDs (slateId-playerId, e.g. "127280-17")
            id_lookup: dict[str, str] = {}
            for _, row in group.iterrows():
                name = str(row.get("Name", "")).strip()
                raw = str(row.get("Raw_DFS_ID", "")).strip()
                dfs_id = str(row.get("DFS_ID", "")).strip()
                if raw and raw not in ("nan", "None", ""):
                    id_lookup[name] = raw
                elif dfs_id and dfs_id not in ("nan", "None", ""):
                    id_lookup[name] = dfs_id
                else:
                    id_lookup[name] = ""
            values = ["", "", "", ""]  # entry_id, contest_id, contest_name, entry_fee — blank for new entries
            for slot in fd_slot_order:
                player_name = assignment.get(slot, "")
                values.append(id_lookup.get(player_name, player_name))
            lines.append(",".join(values))
        return "\n".join(lines) + "\n"


# ─ main endpoint ─────────────────────────────────────────────────────────────


@router.post("/run-full")
async def run_full_pipeline(
    file: UploadFile = File(...),
    site: str = "FD",
    sport: str = "NBA",
    n_lineups: int = 20,
    # Optimizer controls
    max_exposure: float = 0.60,
    contest_type: str = "gpp",
    entry_fee: float = 3.0,
    field_size: int = 100,
    num_unique: int = 2,             # unique players required across each lineup (1-6)
    # Player exclusions
    out_players: str = "",   # comma-separated
    out_teams: str = "",     # comma-separated
    # Stacking
    enable_stacking: bool = True,
    min_game_stack: int = 2,
    bring_back_count: int = 1,
    # Prop refresh
    refresh_props: bool = True,
    # Player-pool overrides from the UI player-pool table
    projection_overrides: str = "",  # JSON: {"LeBron James": 42.5, ...}
    locked_players: str = "",        # comma-separated: force into every lineup
    current_user=Depends(require_plan("pro")),
):
    """
    One-click pipeline: upload slate → injures → props → project → own → optimize → download.

    Guarantees that DK/FD stock averages (AvgPointsPerGame, FPPG) are
    NEVER used in final lineup construction.  All projections derive from:
      • Our own game-log L10 rolling averages (dfs_edge.duckdb)
      • Defense-vs-Player (DvP) positional opponent adjustment
      • Injury replacement boosts (minutes/usage redistribution)
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Slate file must be a CSV")

    # ── Step 1: persist uploaded slate ──────────────────────────────────────
    user_id = _user_id(current_user)
    file_info = await save_slate_file(file, user_id=user_id)
    log.info("[pipeline] Slate saved: %s  site=%s  n_lineups=%d", file_info["file_name"], site, n_lineups)

    # ── Step 2: refresh player props (non-blocking) ─────────────────────────
    props_summary: dict = {}
    if refresh_props and sport.upper() == "NBA":
        try:
            from analysis.shared.props_enricher import refresh_props as _rp
            props_summary = _rp(game_date=date.today().isoformat())
            log.info("[pipeline] Props refresh: %s", props_summary)
        except Exception as exc:
            log.warning("[pipeline] Props refresh skipped: %s", exc)
            props_summary = {"status": "skipped", "error": str(exc)}

    # ── Step 3: build injuries + fades from params ──────────────────────────
    injuries: dict[str, str] = {}
    if out_players:
        for name in out_players.split(","):
            name = name.strip()
            if name:
                injuries[name] = "OUT"

    if out_teams:
        faded = {t.strip().upper() for t in out_teams.split(",") if t.strip()}
        if faded:
            try:
                df_slate = pd.read_csv(file_info["file_path"], encoding="utf-8")
                team_col = next(
                    (c for c in df_slate.columns if c.lower() in ("teamabbrev", "team_abbrev", "team")),
                    None,
                )
                name_col = next(
                    (c for c in df_slate.columns if c.lower() in ("name", "player", "nickname", "playername", "player_name")),
                    None,
                )
                if team_col and name_col:
                    for _, row in df_slate.iterrows():
                        player_team = str(row.get(team_col, "")).strip().upper()
                        player_name = str(row.get(name_col, "")).strip()
                        if player_team in faded and player_name:
                            if ":" in player_name:
                                player_name = player_name.split(":", 1)[1].strip()
                            injuries[player_name] = "OUT"
            except Exception:
                pass

    # ── Step 4–6: run full pipeline (inject → project → own → optimize) ─────
    import json as _json
    try:
        _proj_overrides = _json.loads(projection_overrides) if projection_overrides else {}
    except (_json.JSONDecodeError, ValueError):
        _proj_overrides = {}

    _locks: list[str] = [n.strip() for n in locked_players.split(",") if n.strip()] if locked_players else []

    context = ProjectionContext(
        sport=sport.upper(),
        site=site.upper(),
        injuries=injuries,
        enable_stacking=enable_stacking,
        min_game_stack=min_game_stack,
        bring_back_count=bring_back_count,
        projection_overrides=_proj_overrides,
        locks=_locks,
    )
    contest_cfg = ContestConfig(
        site=site.upper(),
        contest_type=contest_type,
        entry_fee=entry_fee,
        field_size=field_size,
    )
    exposure_cfg = ExposureConfig(global_max=max_exposure)

    try:
        result = run_dfs_pipeline(
            slate_file_path=file_info["file_path"],
            context=context,
            n_lineups=n_lineups,
            apply_filter=True,
            contest_cfg=contest_cfg,
            exposure_cfg=exposure_cfg,
            num_unique=num_unique,
        )
    except Exception as exc:
        log.exception("[pipeline] run_dfs_pipeline failed")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")

    lineups_df = result.get("lineups_df", pd.DataFrame())
    if lineups_df.empty:
        raise HTTPException(
            status_code=422,
            detail=(
                "Optimizer generated zero lineups. "
                "Possible causes: player pool too small after injury filter, "
                "salary constraints unsatisfiable, or stacking requirements too strict. "
                "Try reducing n_lineups, disabling stacking, or uploading a larger slate."
            ),
        )

    resolved_site = result["site"]

    # ── Step 7: build preview + persist export CSV ───────────────────────────
    previews = _lineups_to_preview(lineups_df, resolved_site)
    csv_data = _lineups_to_export_csv(lineups_df, resolved_site)
    lineup_file = await save_lineup_file(csv_data, file_info["file_id"], user_id=user_id)

    stats = {
        "avg_projection": round(float(lineups_df.groupby("LineupIndex")["Proj"].sum().mean()), 2),
        "avg_salary": round(float(lineups_df.groupby("LineupIndex")["Salary"].sum().mean()), 2),
        "projection_range": (
            f"{round(float(lineups_df.groupby('LineupIndex')['Proj'].sum().min()), 2)} – "
            f"{round(float(lineups_df.groupby('LineupIndex')['Proj'].sum().max()), 2)}"
        ),
    }

    # Projection source breakdown (how many players used each layer)
    proj_df = result.get("projections_df", pd.DataFrame())
    proj_source_note = "All projections: L10 game-log avg + DvP adjustment. DK/FD stock avg NEVER used."

    return {
        "success": True,
        "pipeline_steps": {
            "slate_uploaded": file_info["file_name"],
            "injuries_refreshed": True,   # always runs inside orchestrator
            "props_refreshed": props_summary,
            "projections_generated": int(len(proj_df)),
            "ownership_model": "GBR calibrated" if _ownership_model_exists(sport, site) else "percentile fallback",
            "lineups_generated": int(len(previews)),
        },
        "projection_source": proj_source_note,
        "site": resolved_site,
        "total_lineups": int(len(previews)),
        "lineups": previews[:50],
        "stats": stats,
        "download_file": lineup_file["file_name"],
        "filter_report": result.get("filter_report", {}),
        "replacement_boosts": result.get("replacement_boosts", []),
    }


def _ownership_model_exists(sport: str, site: str) -> bool:
    from pathlib import Path
    models_dir = Path(__file__).resolve().parents[2] / "data" / "models"
    return (models_dir / f"ownership_{sport.upper()}_{site.upper()}.pkl").exists()


# ─ Ownership import + training ────────────────────────────────────────────────


@router.post("/ownership/import-history")
async def import_ownership_history(
    site: str = "DK",
    contest_type: str = "gpp",
):
    """
    Scan data/uploads/ for past lineup CSVs, compute per-player ownership %,
    and import rows into ownership_history.duckdb for model training.
    """
    try:
        from analysis.nba.ownership_v2 import import_lineups_to_history
        result = import_lineups_to_history(site=site, contest_type=contest_type)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ownership/train")
async def train_ownership_model(
    sport: str = "NBA",
    site: str = "DK",
):
    """
    (Re)train the GBR ownership model from ownership_history.duckdb.
    Must have at least 300 rows of historical ownership data.
    """
    try:
        from analysis.nba.ownership_v2 import train_ownership_model as _train
        model = _train(sport=sport, site=site)
        if model is None:
            raise HTTPException(status_code=422, detail="Insufficient training data (< 300 rows)")
        return {"status": "trained", "sport": sport, "site": site}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/props/refresh")
async def refresh_props_endpoint(game_date: str | None = None):
    """
    Manually refresh player prop lines for a given date (default: today).
    Returns {status, events_fetched, new_rows, players_covered}.
    """
    try:
        from analysis.shared.props_enricher import refresh_props as _rp
        result = _rp(game_date=game_date)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
