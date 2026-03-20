"""Backend projection service wired to canonical analysis orchestrator."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

parent_dir = str(Path(__file__).parent.parent.parent)
sys.path.insert(0, parent_dir)

from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.schemas import ProjectionContext
from services.analytics_service import init_analytics_tables, log_projections

# Ensure analytics tables exist at import time (no-op if already created)
try:
    init_analytics_tables()
except Exception:
    pass


async def generate_projections(
    slate_file_path: str,
    user_id: str,
    site: str = "FD",
    sport: str = "NBA",
) -> dict[str, Any]:
    """Generate projections using the canonical pipeline.

    Production path never falls back to salary-only projections.
    Dev fallback exists behind DFS_ENABLE_DEV_PLACEHOLDER=1.
    """
    try:
        context = ProjectionContext(sport=sport.upper(), site=site.upper())
        pipeline_result = await asyncio.to_thread(
            run_dfs_pipeline,
            slate_file_path=slate_file_path,
            context=context,
            n_lineups=0,
        )

        projections_df: pd.DataFrame = pipeline_result["projections_df"]
        projections_df = projections_df.sort_values("Proj", ascending=False)

        # Build projections list without iterrows — pre-cast columns once, then
        # use to_dict('records') which allocates plain dicts (no per-row Series).
        _pf = projections_df.assign(
            dfs_id=projections_df["DFS_ID"].astype(str),
            salary=projections_df["Salary"].astype(int),
            projection=projections_df["Proj"].astype(float).round(2),
            floor=projections_df["Floor"].astype(float).round(2),
            ceiling=projections_df["Ceiling"].astype(float).round(2),
            value=projections_df["Value"].astype(float).round(2),
            ownership=projections_df["Own"].astype(float).round(2),
        ).rename(columns={"Name": "name", "Pos": "position", "Team": "team", "Opp": "opponent"})
        projections = _pf[
            ["dfs_id", "name", "position", "team", "opponent",
             "salary", "projection", "floor", "ceiling", "value", "ownership"]
        ].to_dict("records")

        # Log projections to analytics DB for later accuracy reconciliation.
        # Runs silently — never blocks the response.
        try:
            from datetime import date as _date
            import re as _re
            _date_match = _re.search(r"\d{4}-\d{2}-\d{2}", Path(slate_file_path).name)
            _slate_date = _date.fromisoformat(_date_match.group()) if _date_match else _date.today()
            _log_rows = [
                {
                    "player_id": p.get("dfs_id", ""),
                    "player_name": p["name"],
                    "salary": p["salary"],
                    "proj": p["projection"],
                    "floor": p["floor"],
                    "ceiling": p["ceiling"],
                    "ownership": p["ownership"],
                }
                for p in projections
            ]
            log_projections(_log_rows, slate_date=_slate_date, site=site.upper())
        except Exception:
            pass  # silent — accuracy logging must never break the pipeline

        return {
            "success": True,
            "projections": projections,
            "stats": {
                "total_players": int(len(projections)),
                "avg_projection": round(float(projections_df["Proj"].mean()), 2) if len(projections_df) else 0.0,
                "total_salary_available": int(projections_df["Salary"].sum()) if len(projections_df) else 0,
                "slate_file": Path(slate_file_path).name,
                "site": site.upper(),
                "sport": sport.upper(),
            },
            "algorithm": "Canonical projection engine",
            "user_id": user_id,
        }
    except Exception as exc:
        if os.getenv("DFS_ENABLE_DEV_PLACEHOLDER", "0") == "1":
            slate_df = pd.read_csv(slate_file_path)
            return await _generate_dev_placeholder_projections(slate_df, slate_file_path)
        return {
            "success": False,
            "error": str(exc),
            "slate_file": Path(slate_file_path).name,
        }


async def _generate_dev_placeholder_projections(slate_df: pd.DataFrame, slate_file_path: str) -> dict[str, Any]:
    """DEV-only fallback. Never used unless DFS_ENABLE_DEV_PLACEHOLDER=1."""
    projections = []
    for _, player in slate_df.iterrows():
        player_name = player.get("Nickname", player.get("Name", "Unknown"))
        position = player.get("Position", "UTIL")
        salary = float(player.get("Salary", 0))
        team = player.get("Team", "")
        opponent = player.get("Opponent", "")
        base_projection = (salary / 1000.0) * 5 if salary > 0 else 0.0

        projections.append(
            {
                "name": player_name,
                "position": position,
                "team": team,
                "opponent": opponent,
                "salary": int(salary),
                "projection": round(base_projection, 2),
                "floor": round(base_projection * 0.7, 2),
                "ceiling": round(base_projection * 1.4, 2),
                "value": round(base_projection / (salary / 1000.0), 2) if salary > 0 else 0,
                "ownership": 0.0,
            }
        )

    projections.sort(key=lambda item: item["projection"], reverse=True)
    return {
        "success": True,
        "projections": projections,
        "stats": {
            "total_players": len(projections),
            "avg_projection": round(sum(p["projection"] for p in projections) / max(len(projections), 1), 2),
            "total_salary_available": int(pd.to_numeric(slate_df.get("Salary", 0), errors="coerce").fillna(0).sum()),
            "slate_file": Path(slate_file_path).name,
        },
        "algorithm": "DEV_ONLY placeholder",
        "note": "Enabled via DFS_ENABLE_DEV_PLACEHOLDER=1",
    }


def projections_to_csv(projections: list) -> str:
    """
    Convert projections list to CSV string
    """
    if not projections:
        return ""
    
    df = pd.DataFrame(projections)
    return df.to_csv(index=False)


