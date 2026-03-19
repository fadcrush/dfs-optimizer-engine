"""
Optimizer Celery Tasks
======================
Background tasks for lineup generation.  These run inside the Celery worker
process, NOT inside the FastAPI process, so they can safely block for seconds
or minutes without impacting the API.

Usage (from a FastAPI route):
    from backend.tasks.optimizer import run_optimizer_task
    task = run_optimizer_task.apply_async(kwargs={...})
    return {"task_id": task.id, "status": "queued"}
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from backend.celery_app import app
from analysis.core.contest_sim import ContestConfig
from analysis.core.exposure_optimizer import ExposureConfig
from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.schemas import ProjectionContext
from analysis.nba.optimizer import assign_lineup_slots
from analysis.nba.pool_filter import PoolFilterConfig
from services.file_service import get_user_storage_dir

log = logging.getLogger(__name__)


# ── shared helpers (mirrors backend/routers/optimizer.py) ────────────────────

def _lineups_to_preview(lineups_df: pd.DataFrame, site: str) -> list[dict]:
    previews = []
    for lineup_index, group in lineups_df.groupby("LineupIndex"):
        assignment = assign_lineup_slots(group, site)
        if not assignment:
            continue
        total_salary = int(pd.to_numeric(group["Salary"], errors="coerce").fillna(0).sum())
        projected_points = float(pd.to_numeric(group["Proj"], errors="coerce").fillna(0).sum())
        own_col = group.get("Own", group.get("Own_Est", None))
        total_ownership = round(float(pd.to_numeric(own_col, errors="coerce").fillna(0).sum()), 1) if own_col is not None else 0.0
        preview = {
            "lineup_num": int(lineup_index) + 1,
            "total_salary": total_salary,
            "projected_points": round(projected_points, 2),
            "total_ownership": total_ownership,
        }
        preview.update(assignment)
        previews.append(preview)
    return previews


def _lineups_to_export_csv(lineups_df: pd.DataFrame, site: str) -> str:
    site = site.upper()
    if site == "DK":
        dk_slots = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
        prefix_cols = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
        rows = []
        for lineup_index, group in lineups_df.groupby("LineupIndex"):
            assignment = assign_lineup_slots(group, site)
            if not assignment:
                continue
            name_id_lookup: dict[str, str] = {}
            for _, row in group.iterrows():
                name = str(row.get("Name", "")).strip()
                dfs_id = str(row.get("DFS_ID", "")).strip()
                name_id_lookup[name] = (
                    f"{name} ({dfs_id})" if dfs_id and dfs_id not in ("nan", "None", "") else name
                )
            export_row: dict = {col: "" for col in prefix_cols}
            for slot in dk_slots:
                player_name = assignment.get(slot, "")
                export_row[slot] = name_id_lookup.get(player_name, player_name)
            rows.append(export_row)
        return pd.DataFrame(rows, columns=prefix_cols + dk_slots).to_csv(index=False)

    # FanDuel
    fd_slot_order = ["PG", "PG_2", "SG", "SG_2", "SF", "SF_2", "PF", "PF_2", "C"]
    header = "entry_id,contest_id,contest_name,entry_fee,PG,PG,SG,SG,SF,SF,PF,PF,C"
    lines = [header]
    for lineup_index, group in lineups_df.groupby("LineupIndex"):
        assignment = assign_lineup_slots(group, site)
        if not assignment:
            continue
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
        values = ["", "", "", ""]
        for slot in fd_slot_order:
            values.append(id_lookup.get(assignment.get(slot, ""), ""))
        lines.append(",".join(values))
    return "\n".join(lines) + "\n"


# ── task ─────────────────────────────────────────────────────────────────────

@app.task(bind=True, track_started=True, name="dfs_edge.run_optimizer")
def run_optimizer_task(
    self,
    *,
    file_path: str,
    site: str,
    sport: str,
    n_lineups: int,
    n_sims: int,
    pre_sim: bool,
    pre_sim_sims: int,
    max_exposure: float,
    contest_type: str,
    entry_fee: float,
    field_size: int,
    num_unique: int,
    injuries: dict,
    locks: list,
    projection_overrides: dict,
    apply_filter: bool,
    chalk_threshold: float,
    player_caps: dict,
    player_floors: dict,
    file_id: str,
    user_id: str,
) -> dict:
    """Run the full DFS optimizer pipeline in a background Celery worker.

    All CPU-heavy computation happens here so the FastAPI process stays
    responsive.  Returns the same JSON-serializable dict shape as the
    synchronous ``POST /api/optimizer/run`` endpoint.
    """
    log.info(
        "[task:run_optimizer] starting — file=%s site=%s n=%d",
        file_path, site, n_lineups,
    )

    context = ProjectionContext(
        sport=sport.upper(),
        site=site.upper(),
        injuries=injuries,
        projection_overrides=projection_overrides,
        locks=locks,
    )
    contest_cfg = ContestConfig(
        site=site.upper(),
        contest_type=contest_type,
        entry_fee=entry_fee,
        field_size=field_size,
    )
    exposure_cfg = ExposureConfig(
        global_max=max_exposure,
        player_caps=player_caps,
        player_floors=player_floors,
    )
    pool_filter_cfg = (
        PoolFilterConfig(chalk_own_threshold=chalk_threshold)
        if chalk_threshold > 0 and apply_filter
        else None
    )

    result = run_dfs_pipeline(
        slate_file_path=file_path,
        context=context,
        n_lineups=n_lineups,
        n_sims=n_sims,
        pre_sim=pre_sim,
        pre_sim_sims=pre_sim_sims,
        export_dir=None,
        apply_filter=apply_filter,
        contest_cfg=contest_cfg,
        exposure_cfg=exposure_cfg,
        pool_filter_cfg=pool_filter_cfg,
        num_unique=num_unique,
    )

    lineups_df: pd.DataFrame = result.get("lineups_df", pd.DataFrame())
    if lineups_df.empty:
        raise RuntimeError("Optimizer produced no lineups")

    previews = _lineups_to_preview(lineups_df, result["site"])
    csv_data = _lineups_to_export_csv(lineups_df, result["site"])

    # Persist lineup CSV synchronously (save_lineup_file is async in name only)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    lineup_file_name = f"lineups_{ts}_{file_id}.csv"
    user_dir = get_user_storage_dir("lineup", user_id or "anonymous")
    (user_dir / lineup_file_name).write_text(csv_data)

    stats = {
        "avg_projection": round(
            float(lineups_df.groupby("LineupIndex")["Proj"].sum().mean()), 2
        ),
        "avg_salary": round(
            float(lineups_df.groupby("LineupIndex")["Salary"].sum().mean()), 2
        ),
        "projection_range": (
            f"{round(float(lineups_df.groupby('LineupIndex')['Proj'].sum().min()), 2)}"
            f" - "
            f"{round(float(lineups_df.groupby('LineupIndex')['Proj'].sum().max()), 2)}"
        ),
    }

    response: dict = {
        "success": True,
        "message": "Optimizer completed",
        "total_lineups": int(len(previews)),
        "lineups": previews[: min(50, len(previews))],
        "stats": stats,
        "download_file": lineup_file_name,
        "filter_report": result.get("filter_report", {}),
        "replacement_boosts": result.get("replacement_boosts", []),
    }

    if "simulation_df" in result and not result["simulation_df"].empty:
        sim_df = result["simulation_df"]
        ev_cols = [
            c for c in [
                "LineupIndex", "EV", "ROI", "ExpectedPayout", "CashRate",
                "Top1Rate", "AvgFinishPct", "Mean", "P90", "P95", "StdDev", "WinRate",
            ]
            if c in sim_df.columns
        ]
        response["simulation_summary"] = sim_df[ev_cols].to_dict(orient="records")

    if "exposure_report" in result and not result["exposure_report"].empty:
        exp_cols = [
            c for c in [
                "Name", "Team", "Pos", "Salary", "Proj", "Own", "Leverage",
                "ActualCount", "ActualPct", "CapPct", "FloorPct", "OverCap", "UnderFloor",
            ]
            if c in result["exposure_report"].columns
        ]
        response["exposure_report"] = result["exposure_report"][exp_cols].to_dict(orient="records")

    if "stack_report" in result and not result["stack_report"].empty:
        response["stack_report"] = result["stack_report"].to_dict(orient="records")

    log.info("[task:run_optimizer] done — %d lineups", len(previews))
    return response
