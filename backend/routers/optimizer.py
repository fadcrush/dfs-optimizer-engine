"""
Optimizer Routes - Generate lineups and optional Monte Carlo simulations
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
import pandas as pd

from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.schemas import ProjectionContext
from analysis.core.contest_sim import ContestConfig
from analysis.core.exposure_optimizer import ExposureConfig
from analysis.nba.optimizer import assign_lineup_slots
from analysis.nba.pool_filter import PoolFilterConfig
from services.auth import get_current_user, require_plan
from services.file_service import get_file_path, save_lineup_file, save_slate_file
from services.late_swap_service import (
    resolve_weights,
    rank_normalize,
    score_candidates,
    scoring_projection,
)

router = APIRouter(prefix="/api/optimizer", tags=["Optimizer"])


def _user_id(user) -> str:
    if hasattr(user, "id"):
        return str(user.id)
    if isinstance(user, dict):
        return str(user.get("id", ""))
    return ""


def _lineups_to_preview(lineups_df: pd.DataFrame, site: str) -> list[dict]:
    """Build UI preview rows — always uses plain player names."""
    previews = []
    for lineup_index, group in lineups_df.groupby("LineupIndex"):
        assignment = assign_lineup_slots(group, site)
        if not assignment:
            continue
        total_salary = int(pd.to_numeric(group["Salary"], errors="coerce").fillna(0).sum())
        projected_points = float(pd.to_numeric(group["Proj"], errors="coerce").fillna(0).sum())
        total_ownership = round(float(pd.to_numeric(group.get("Own", group.get("Own_Est", None)), errors="coerce").fillna(0).sum()), 1)
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


@router.post("/run")
async def run_optimizer(
    file: UploadFile = File(...),
    site: str = "FD",
    sport: str = "NBA",
    n_lineups: int = 20,
    n_sims: int = 0,
    pre_sim: bool = False,
    pre_sim_sims: int = 300,
    apply_filter: bool = True,
    out_players: str = "",           # comma-separated confirmed OUT players
    out_teams: str = "",             # comma-separated team abbrs to exclude (e.g. "BOS,LAL")
    # Contest EV params (only used when n_sims > 0)
    contest_type: str = "gpp",       # gpp | double_up | top_heavy | winner_take_all
    entry_fee: float = 3.0,
    field_size: int = 100,
    # Exposure params
    max_exposure: float = 0.60,      # global max pct any player appears
    player_caps: str = "",           # JSON: {"LeBron James": 0.35, ...}
    player_floors: str = "",         # JSON: {"Anthony Davis": 0.20, ...}
    num_unique: int = 2,             # unique players required across each lineup (1-6)
    # Pool filter overrides
    chalk_threshold: float = 0.0,    # auto-fade players projected > this % owned (0 = disabled)
    # Player-pool overrides from the UI player-pool table
    projection_overrides: str = "",  # JSON: {"LeBron James": 42.5, ...} — user-adjusted projections
    locked_players: str = "",        # comma-separated: force into every lineup
    current_user=Depends(require_plan("pro")),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    user_id = _user_id(current_user)
    file_info = await save_slate_file(file, user_id=user_id)

    # Build injuries dict from out_players query param
    injuries: dict[str, str] = {}
    if out_players:
        for name in out_players.split(","):
            name = name.strip()
            if name:
                injuries[name] = "OUT"

    # Fade entire teams — read the CSV and mark every player on those teams OUT
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
                            # Strip DK ID prefix if present: "123456789:Name" → "Name"
                            if ":" in player_name:
                                player_name = player_name.split(":", 1)[1].strip()
                            injuries[player_name] = "OUT"
            except Exception:
                pass

    import json
    try:
        _proj_overrides = json.loads(projection_overrides) if projection_overrides else {}
    except (json.JSONDecodeError, ValueError):
        _proj_overrides = {}

    _locks: list[str] = [n.strip() for n in locked_players.split(",") if n.strip()] if locked_players else []

    context = ProjectionContext(
        sport=sport.upper(),
        site=site.upper(),
        injuries=injuries,
        projection_overrides=_proj_overrides,
        locks=_locks,
    )
    contest_cfg = ContestConfig(
        site=site.upper(),
        contest_type=contest_type,
        entry_fee=entry_fee,
        field_size=field_size,
    )

    try:
        _caps   = json.loads(player_caps)   if player_caps   else {}
        _floors = json.loads(player_floors) if player_floors else {}
    except (json.JSONDecodeError, ValueError):
        _caps, _floors = {}, {}
    exposure_cfg = ExposureConfig(
        global_max=max_exposure,
        player_caps=_caps,
        player_floors=_floors,
    )

    # Build optional pool filter config (chalk auto-fade)
    pool_filter_cfg: PoolFilterConfig | None = None
    if chalk_threshold > 0 and apply_filter:
        pool_filter_cfg = PoolFilterConfig(chalk_own_threshold=chalk_threshold)

    result = await asyncio.to_thread(
        run_dfs_pipeline,
        slate_file_path=file_info["file_path"],
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

    lineups_df = result.get("lineups_df", pd.DataFrame())
    if lineups_df.empty:
        raise HTTPException(status_code=500, detail="Optimizer failed to generate lineups")

    previews = _lineups_to_preview(lineups_df, result["site"])
    csv_data = _lineups_to_export_csv(lineups_df, result["site"])
    lineup_file = await save_lineup_file(csv_data, file_info["file_id"], user_id=user_id)

    stats = {
        "avg_projection": round(float(lineups_df.groupby("LineupIndex")["Proj"].sum().mean()), 2),
        "avg_salary": round(float(lineups_df.groupby("LineupIndex")["Salary"].sum().mean()), 2),
        "projection_range": f"{round(float(lineups_df.groupby('LineupIndex')['Proj'].sum().min()), 2)} - {round(float(lineups_df.groupby('LineupIndex')['Proj'].sum().max()), 2)}",
    }

    response = {
        "success": True,
        "message": "Optimizer completed",
        "total_lineups": int(len(previews)),
        "lineups": previews[: min(50, len(previews))],
        "stats": stats,
        "download_file": lineup_file["file_name"],
        "filter_report": result.get("filter_report", {}),
        "replacement_boosts": result.get("replacement_boosts", []),
    }

    if "simulation_df" in result and not result["simulation_df"].empty:
        sim_df = result["simulation_df"]
        # EV columns from contest simulation
        ev_cols = [c for c in ["LineupIndex", "EV", "ROI", "ExpectedPayout",
                                "CashRate", "Top1Rate", "AvgFinishPct",
                                "Mean", "P90", "P95", "StdDev", "WinRate"]
                   if c in sim_df.columns]
        response["simulation_summary"] = sim_df[ev_cols].to_dict(orient="records")

    if "exposure_report" in result and not result["exposure_report"].empty:
        exp_cols = [c for c in ["Name", "Team", "Pos", "Salary", "Proj",
                                 "Own", "Leverage", "ActualCount", "ActualPct",
                                 "CapPct", "FloorPct", "OverCap", "UnderFloor"]
                    if c in result["exposure_report"].columns]
        response["exposure_report"] = result["exposure_report"][exp_cols].to_dict(orient="records")

    if "stack_report" in result and not result["stack_report"].empty:
        response["stack_report"] = result["stack_report"].to_dict(orient="records")

    if pre_sim and "Sim_Mean" in result.get("projections_df", pd.DataFrame()).columns:
        response["player_sim_summary"] = result["projections_df"][["DFS_ID", "Sim_Mean", "Sim_P90", "Sim_P95", "Sim_P99", "Sim_StdDev"]].to_dict(orient="records")

    return response


@router.post("/parse-lineup-csv")
async def parse_lineup_csv(
    entry_file: UploadFile = File(..., description="DK or FD entry CSV exported from this app"),
    slate_file: UploadFile = File(..., description="DK salary CSV or FD players CSV for ID → name resolution"),
    site: str = "DK",
):
    """
    Parse a DK/FD entry CSV and resolve player IDs back to player names using the slate file.
    Returns one or more lineups (each a list of player names in slot order).
    """
    import csv as _csv
    import io as _io
    entry_content = (await entry_file.read()).decode("utf-8", errors="replace")
    slate_content = (await slate_file.read()).decode("utf-8", errors="replace")
    site = site.upper()

    # Build id → name lookup from the slate file.
    # Handles two formats:
    #   a) Standard FD salary CSV  — "Id" and "Nickname" columns from row 0
    #   b) FD upload template      — player list is embedded mid-file with its own
    #      header row ("Player ID + Player Name,Id,Position,Name,Nickname,...")
    id_to_name: dict[str, str] = {}
    slate_rows = list(_csv.reader(_io.StringIO(slate_content)))
    # Find the row that carries the player-data column headers
    player_header_idx = 0
    id_col_names     = {"id"}
    name_col_names   = {"name", "nickname", "first name"}
    for i, row in enumerate(slate_rows):
        lower_cols = {c.strip().lower() for c in row}
        if id_col_names & lower_cols and name_col_names & lower_cols:
            player_header_idx = i
            break
    if player_header_idx < len(slate_rows):
        # Rebuild a DictReader from this row onward
        sub_content = "\n".join(
            ",".join(r) for r in slate_rows[player_header_idx:]
        )
        slate_reader = _csv.DictReader(_io.StringIO(sub_content))
        for row in slate_reader:
            player_id = str(row.get("ID", row.get("Id", ""))).strip()
            name = str(row.get("Name", row.get("Nickname", ""))).strip()
            if not name:
                first = str(row.get("First Name", "")).strip()
                last  = str(row.get("Last Name", "")).strip()
                name  = f"{first} {last}".strip()
            if player_id and name:
                id_to_name[player_id] = name

    lineups: list[dict] = []

    if site == "DK":
        dk_slot_order = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
        entry_reader = _csv.DictReader(_io.StringIO(entry_content))
        headers = list(entry_reader.fieldnames or [])
        slot_cols = [c for c in headers if c in set(dk_slot_order)]
        for row in entry_reader:
            players: list[str] = []
            labels: list[str] = []
            for col in slot_cols:
                cell = str(row.get(col, "")).strip()
                if not cell:
                    continue
                # Handle both formats:
                #   Old export: bare numeric ID  (e.g. "41699649")
                #   New export: Name (ID) format (e.g. "Nikola Jokic (41699649)")
                import re as _re
                m = _re.search(r"\((\d+)\)$", cell)
                if m:
                    # "Name (ID)" — extract name directly
                    name_part = cell[: m.start()].strip()
                    pid = m.group(1)
                    players.append(id_to_name.get(pid, name_part))
                elif cell.isdigit():
                    # bare numeric ID
                    players.append(id_to_name.get(cell, cell))
                else:
                    # plain name or unknown format — use as-is
                    players.append(id_to_name.get(cell, cell))
                labels.append(col)
            if players:
                lineups.append({"players": players, "slot_labels": labels})
    else:  # FD — duplicate column headers, parse positionally
        # FD upload template column layout:
        #   0: entry_id  1: contest_id  2: contest_name  3: entry_fee
        #   4-12: PG PG SG SG SF SF PF PF C   (the 9 player slots)
        #   13+: empty / player-pool data (ignored)
        # Rows with a blank entry_id are the embedded player-pool section — skip them.
        fd_slot_order = ["PG", "PG", "SG", "SG", "SF", "SF", "PF", "PF", "C"]
        entry_rows = list(_csv.reader(_io.StringIO(entry_content)))
        for row in entry_rows[1:]:          # skip header
            if len(row) < 4 + len(fd_slot_order):
                continue
            entry_id = row[0].strip()
            # Only process real lineup rows (entry_id is a numeric string)
            if not entry_id or not entry_id.isdigit():
                continue
            composites = row[4: 4 + len(fd_slot_order)]
            players = []
            labels = []
            for label, composite in zip(fd_slot_order, composites):
                composite = composite.strip()
                if not composite:
                    continue
                # FD slot values are bare player IDs: "127246-84669"
                # id_to_name is keyed on the full ID string — look up directly.
                players.append(id_to_name.get(composite, composite))
                labels.append(label)
            if players:
                lineups.append({"players": players, "slot_labels": labels})

    if not lineups:
        raise HTTPException(status_code=400, detail="No lineups could be parsed from the entry CSV")

    return {"success": True, "site": site, "count": len(lineups), "lineups": lineups}


@router.post("/late-swap")
async def late_swap(
    file: UploadFile = File(...),
    site: str = "FD",
    sport: str = "NBA",
    lineup: str = "",         # comma-separated player names in the existing lineup
    slot_labels: str = "",    # comma-separated slot labels aligned to lineup names
    scratched: str = "",      # comma-separated player names that are OUT / need replacing
    locked_players: str = "",  # comma-separated player names that must remain fixed in place
    apply_filter: bool = True,
    randomness: float = 0.0,  # 0.0 = deterministic; 1.0 = max variance (±4 FPTS noise)
    n_candidates: int = 15,   # how many top candidates to return per scratched player (max 50)
    sort_by: str = "swap_score",  # swap_score | projection | value | ceiling | own | floor
    # ── Scoring weights (must sum to 1.0; omit to use contest_type preset) ──
    w_proj: float | None = None,
    w_value: float | None = None,
    w_own: float | None = None,
    # ── Contest type: balanced | cash | gpp | tournament ──
    contest_type: str | None = None,
    current_user=Depends(get_current_user),
):
    """
    Late Swap endpoint.

    Given an existing lineup and one or more scratched players, returns the best
    eligible swap candidates for each scratched player.

    Eligibility rules:
      - Position must be compatible with at least one slot the scratched player filled
      - New player salary must not cause total lineup salary to exceed the cap
      - Player must not already be in the lineup

    Candidate scoring:
      swap_score = w_proj * rank_norm(projection)
                 + w_value * rank_norm(value)
                 + w_own   * rank_norm(own, ascending=True)
                 + correlation_bonus (capped at 0.25)

    Default weights (balanced): w_proj=0.50, w_value=0.30, w_own=0.20
    Cash preset:                 w_proj=0.70, w_value=0.20, w_own=0.10
    GPP preset:                  w_proj=0.30, w_value=0.20, w_own=0.50
    """
    import difflib

    # Resolve scoring weights (raises HTTPException on bad input)
    try:
        wp, wv, wo = resolve_weights(w_proj, w_value, w_own, contest_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    n_candidates = max(1, min(50, n_candidates))
    sort_by = sort_by if sort_by in ("swap_score", "projection", "value", "ceiling", "own", "floor") else "swap_score"

    user_id = _user_id(current_user)
    file_info = await save_slate_file(file, user_id=user_id)

    context = ProjectionContext(sport=sport.upper(), site=site.upper())
    result = run_dfs_pipeline(
        slate_file_path=file_info["file_path"],
        context=context,
        n_lineups=0,  # no optimization needed — just projections
        apply_filter=apply_filter,
    )

    proj_df = result.get("projections_df", pd.DataFrame())
    if proj_df.empty:
        raise HTTPException(status_code=500, detail="Could not generate projections from slate")

    resolved_site = result.get("site", site.upper())
    from analysis.nba.optimizer import SITE_RULES, _eligibility_set, _slot_is_eligible

    rules = SITE_RULES.get(resolved_site, SITE_RULES["DK"])
    salary_cap: int = rules["salary_cap"]
    slots: dict = rules["slots"]

    has_sim = "Sim_P90" in proj_df.columns
    has_floor = "Sim_P10" in proj_df.columns

    # Build name → row lookup (case-insensitive) and a list of all names for fuzzy matching
    name_to_row: dict[str, pd.Series] = {}
    all_slate_names: list[str] = []
    for _, row in proj_df.iterrows():
        n = str(row.get("Name", "")).strip()
        if n:
            name_to_row[n.lower()] = row
            all_slate_names.append(n.lower())

    def lookup(name: str) -> tuple[pd.Series | None, str | None]:
        """Returns (row, resolved_name) — uses exact, then fuzzy match."""
        exact = name_to_row.get(name.lower())
        if exact is not None:
            return exact, name
        # fuzzy fallback
        matches = difflib.get_close_matches(name.lower(), all_slate_names, n=1, cutoff=0.72)
        if matches:
            resolved = matches[0]
            return name_to_row[resolved], resolved
        # substring fallback
        hits = [n for n in all_slate_names if name.lower() in n]
        if hits:
            return name_to_row[hits[0]], hits[0]
        return None, None

    # Parse inputs
    lineup_names = [n.strip() for n in lineup.split(",") if n.strip()]
    lineup_slot_labels = [label.strip().upper() for label in slot_labels.split(",") if label.strip()]
    scratch_names = [n.strip() for n in scratched.split(",") if n.strip()]
    locked_names_lower = {n.strip().lower() for n in locked_players.split(",") if n.strip()}

    if not lineup_names:
        raise HTTPException(status_code=400, detail="lineup parameter is required")
    if not scratch_names:
        raise HTTPException(status_code=400, detail="scratched parameter is required")
    if lineup_slot_labels and len(lineup_slot_labels) != len(lineup_names):
        raise HTTPException(status_code=400, detail="slot_labels must align 1:1 with lineup")

    def _normalize_slot_label(label: str) -> str:
        normalized = label.strip().upper()
        if normalized.endswith("_2"):
            return normalized.split("_", 1)[0]
        return normalized

    lineup_entries: list[dict[str, str | pd.Series | None]] = []

    # Calculate baseline lineup salary and capture the exact slot layout when provided.
    current_salary = 0
    for index, name in enumerate(lineup_names):
        row, resolved = lookup(name)
        if row is not None:
            current_salary += int(pd.to_numeric(row.get("Salary", 0), errors="coerce") or 0)
        lineup_entries.append({
            "input_name": name,
            "resolved_name": resolved or name,
            "row": row,
            "slot_label": lineup_slot_labels[index] if index < len(lineup_slot_labels) else None,
        })

    # Pre-resolve scratched player rows so we can compute budget interactions
    scratch_resolved: list[dict] = []
    for sn in scratch_names:
        row, resolved = lookup(sn)
        sal = int(pd.to_numeric(row.get("Salary", 0), errors="coerce") or 0) if row is not None else 0
        proj = float(pd.to_numeric(row.get("Proj", 0), errors="coerce") or 0) if row is not None else 0
        pos = str(row.get("Pos", "UTIL")) if row is not None else "UTIL"
        game_info = str(row.get("GameInfo", row.get("Game Info", row.get("Game", "")))) if row is not None else ""
        scratch_resolved.append({
            "input_name": sn, "resolved_name": resolved or sn,
            "row": row, "salary": sal, "proj": proj, "pos": pos, "game_info": game_info,
        })

    locked_scratches = [
        sr["input_name"] for sr in scratch_resolved
        if sr["input_name"].lower() in locked_names_lower or sr["resolved_name"].lower() in locked_names_lower
    ]
    if locked_scratches:
        raise HTTPException(
            status_code=400,
            detail=f"Locked players cannot be scratched: {', '.join(locked_scratches)}",
        )

    # Build game → teams lookup for correlation flags
    team_col = "Team" if "Team" in proj_df.columns else "TeamAbbrev"
    game_col = next((c for c in ["GameInfo", "Game Info", "Game"] if c in proj_df.columns), None)
    game_to_teams: dict[str, set] = {}
    if game_col:
        for _, row in proj_df.iterrows():
            g = str(row.get(game_col, ""))
            t = str(row.get(team_col, ""))
            if g and t:
                game_to_teams.setdefault(g, set()).add(t)

    def _game_of(row: pd.Series | None) -> str:
        if row is None or game_col is None:
            return ""
        return str(row.get(game_col, ""))

    # Build lineup team/game sets for correlation context
    lineup_teams: set[str] = set()
    lineup_games: set[str] = set()
    lineup_game_counts: dict[str, int] = {}
    lineup_team_counts: dict[str, int] = {}
    scratch_names_lower_set = {s.lower() for s in scratch_names}
    for name in lineup_names:
        if name.lower() in scratch_names_lower_set:
            continue  # don't count scratched players for stacking
        row, _ = lookup(name)
        if row is not None:
            team = str(row.get(team_col, ""))
            game = _game_of(row)
            lineup_teams.add(team)
            lineup_games.add(game)
            if team:
                lineup_team_counts[team] = lineup_team_counts.get(team, 0) + 1
            if game:
                lineup_game_counts[game] = lineup_game_counts.get(game, 0) + 1

    def _rank_normalize(vals: list[float], ascending: bool = False) -> list[float]:
        """Return 0-1 rank-normalized values (higher = better by default)."""
        return rank_normalize(vals, ascending=ascending)

    swaps: list[dict] = []

    for idx_s, sr in enumerate(scratch_resolved):
        scratch_pos = sr["pos"]
        scratch_salary = sr["salary"]
        scratch_proj = sr["proj"]
        scratch_elig = _eligibility_set(scratch_pos)
        exact_slots = [
            _normalize_slot_label(str(entry["slot_label"]))
            for entry in lineup_entries
            if entry.get("slot_label")
            and (
                str(entry["input_name"]).lower() == sr["input_name"].lower()
                or str(entry["resolved_name"]).lower() == sr["resolved_name"].lower()
            )
        ]

        # Multi-scratch budget: remaining cap budget after accounting for the cheapest
        # minimum-salary placeholder for ALL OTHER scratched players.
        other_scratch_min_cost = 0
        for idx_o, other in enumerate(scratch_resolved):
            if idx_o == idx_s:
                continue
            # Cheapest player eligible for the other scratch position
            other_elig = _eligibility_set(other["pos"])
            min_sal = min(
                (
                    int(pd.to_numeric(r.get("Salary", 99999), errors="coerce") or 99999)
                    for _, r in proj_df.iterrows()
                    if any(
                        _slot_is_eligible(resolved_site, sl, other_elig) and
                        _slot_is_eligible(resolved_site, sl, _eligibility_set(str(r.get("Pos", "UTIL"))))
                        for sl in slots
                    )
                ),
                default=3500,
            )
            other_scratch_min_cost += min_sal - other["salary"]  # already removed from salary

        budget = salary_cap - current_salary + scratch_salary - max(0, other_scratch_min_cost)

        # Build raw candidate list
        raw_candidates = []
        lineup_names_lower = {
            str(entry["resolved_name"]).lower()
            for entry in lineup_entries
            if entry.get("resolved_name")
        }
        for _, cand_row in proj_df.iterrows():
            cand_name = str(cand_row.get("Name", "")).strip()
            if not cand_name:
                continue
            if cand_name.lower() in lineup_names_lower:
                continue
            if cand_name.lower() == sr["resolved_name"].lower():
                continue
            if cand_name.lower() in locked_names_lower:
                continue
            cand_salary = int(pd.to_numeric(cand_row.get("Salary", 0), errors="coerce") or 0)
            if cand_salary > budget:
                continue
            cand_pos = str(cand_row.get("Pos", "UTIL"))
            cand_elig = _eligibility_set(cand_pos)
            if exact_slots:
                eligible = any(_slot_is_eligible(resolved_site, slot_name, cand_elig) for slot_name in exact_slots)
            else:
                eligible = any(
                    _slot_is_eligible(resolved_site, slot_name, scratch_elig) and
                    _slot_is_eligible(resolved_site, slot_name, cand_elig)
                    for slot_name in slots
                )
            if not eligible:
                continue

            cand_proj = float(pd.to_numeric(cand_row.get("Proj", 0), errors="coerce") or 0)
            cand_own = round(float(pd.to_numeric(cand_row.get("Own", 0), errors="coerce") or 0), 1)
            cand_value = round(cand_proj / cand_salary * 1000, 2) if cand_salary > 0 else 0.0

            # Ceiling: use Sim_P90 if available, else estimate as proj * 1.30
            if has_sim:
                cand_ceil = round(float(pd.to_numeric(cand_row.get("Sim_P90", 0), errors="coerce") or 0), 1)
            else:
                cand_ceil = round(cand_proj * 1.30, 1)

            # Floor: use Sim_P10 if available, else estimate as proj * 0.75
            if has_floor:
                cand_floor = round(float(pd.to_numeric(cand_row.get("Sim_P10", 0), errors="coerce") or 0), 1)
            else:
                cand_floor = round(cand_proj * 0.75, 1)

            # Scoring projection: use floor/ceil based on contest_type
            cand_score_proj = scoring_projection(cand_proj, cand_floor, cand_ceil, contest_type, has_sim)

            cand_game = _game_of(cand_row)
            cand_team = str(cand_row.get(team_col, ""))
            new_total_salary = current_salary - scratch_salary + cand_salary

            raw_candidates.append({
                "name": cand_name,
                "resolved_name": cand_name,
                "position": cand_pos,
                "team": cand_team,
                "game_info": cand_game,
                "is_same_game": cand_game in lineup_games and bool(cand_game),
                "is_same_team": cand_team in lineup_teams and bool(cand_team),
                "salary": cand_salary,
                "projection": round(cand_proj, 2),
                "ceiling": cand_ceil,
                "floor": cand_floor,
                "value": cand_value,
                "proj_delta": round(cand_proj - scratch_proj, 2),
                "salary_delta": cand_salary - scratch_salary,
                "new_total_salary": new_total_salary,
                "dfs_id": str(cand_row.get("DFS_ID", "")),
                "own": cand_own,
                # Override projection for scoring with contest-type-aware value
                "_score_proj": round(cand_score_proj, 2),
                "swap_score": 0.0,  # filled below
            })

        # Score candidates using the configurable weights + correlation bonus
        if raw_candidates:
            # Override "projection" field temporarily with the scoring-aware projection
            for c in raw_candidates:
                c["projection"] = c.pop("_score_proj", c["projection"])

            score_candidates(
                raw_candidates,
                w_proj=wp, w_value=wv, w_own=wo,
                lineup_games=lineup_games,
                lineup_teams=lineup_teams,
                game_to_teams=game_to_teams,
                lineup_game_counts=lineup_game_counts,
                lineup_team_counts=lineup_team_counts,
            )

            # Restore median projection for display (the scoring projection was
            # only needed for rank-normalisation; callers want the original Proj value)
            for c in raw_candidates:
                if contest_type and contest_type.lower() != "balanced":
                    pass  # projection field was already overwritten; restore from scratch_proj offset
                # Recompute correct display projection from proj_delta and scratch_proj
                c["projection"] = round(c["proj_delta"] + scratch_proj, 2)

        # Apply randomness noise
        if randomness > 0:
            import random as _rnd
            noise_scale = max(0.0, min(1.0, randomness)) * 0.15  # noise on 0-1 score scale
            _valid_sort = sort_by if sort_by not in ("own",) else "swap_score"
            for c in raw_candidates:
                c["_sort_key"] = c.get(_valid_sort, c["swap_score"]) + _rnd.gauss(0, noise_scale)
            raw_candidates.sort(key=lambda c: c.pop("_sort_key"), reverse=True)
        else:
            sort_key = sort_by if sort_by not in ("own",) else "swap_score"
            # own: lower is better (more contrarian), so ascending sort
            reverse = sort_by != "own"
            raw_candidates.sort(key=lambda c: c.get(sort_key, 0), reverse=reverse)

        swaps.append({
            "scratched_player": sr["input_name"],
            "resolved_player": sr["resolved_name"],
            "scratched_salary": scratch_salary,
            "scratched_proj": round(scratch_proj, 2),
            "scratched_position": scratch_pos,
            "salary_budget": budget,
            "candidates": raw_candidates[:n_candidates],
            "total_candidates_found": len(raw_candidates),
        })

    return {
        "success": True,
        "site": resolved_site,
        "lineup": lineup_names,
        "current_salary": current_salary,
        "salary_cap": salary_cap,
        "has_sim_data": has_sim,
        "has_floor_data": has_floor,
        "sort_by": sort_by,
        "contest_type": contest_type or "balanced",
        "scoring_weights": {"w_proj": round(wp, 3), "w_value": round(wv, 3), "w_own": round(wo, 3)},
        "swaps": swaps,
    }


@router.post("/batch-late-swap")
async def batch_late_swap(
    file: UploadFile = File(...),
    site: str = "DK",
    sport: str = "NBA",
    lineups: str = "",         # JSON: [["PlayerA","PlayerB",...], [...], ...]
    scratched: str = "",       # comma-separated scratched player names (case-insensitive)
    locked_players: str = "",  # comma-separated: force-keep these players (started-game locks)
    apply_filter: bool = True,
    # ── Scoring weights ──────────────────────────────────────────────────────
    w_proj: float | None = None,
    w_value: float | None = None,
    w_own: float | None = None,
    contest_type: str | None = None,
    # ── Multi-lineup diversity ────────────────────────────────────────────────
    diversity_factor: float = 0.3,   # 0 = no diversity penalty; 1 = max penalty
    current_user=Depends(get_current_user),
):
    """
    Batch Late Swap

    Accepts a slate file, a list of existing lineups, and a list of scratched players.
    For every lineup:
      • Locked (non-scratched) players are kept exactly as submitted.
      • Each scratched slot is auto-filled with the best available eligible replacement
        that fits within the remaining salary cap.
      • diversity_factor (0–1) penalises repeatedly selecting the same player across
        lineups: effective_score = swap_score / (1 + diversity_factor * times_used).
    Returns a complete set of modified lineups and a downloadable DK/FD bulk-upload CSV.
    """
    import difflib as _difflib
    import json as _json

    # Validate / resolve scoring weights
    try:
        wp, wv, wo = resolve_weights(w_proj, w_value, w_own, contest_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    diversity_factor = max(0.0, min(1.0, diversity_factor))

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    try:
        raw_lineups: list[list[str]] = _json.loads(lineups) if lineups else []
    except (_json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="lineups must be a JSON array of string arrays")

    if not raw_lineups:
        raise HTTPException(status_code=400, detail="At least one lineup is required")

    scratch_names_lower = {s.strip().lower() for s in scratched.split(",") if s.strip()}
    locked_names_lower = {s.strip().lower() for s in locked_players.split(",") if s.strip()} if locked_players else set()

    user_id = _user_id(current_user)
    file_info = await save_slate_file(file, user_id=user_id)
    context = ProjectionContext(sport=sport.upper(), site=site.upper())
    result = run_dfs_pipeline(
        slate_file_path=file_info["file_path"],
        context=context,
        n_lineups=0,
        apply_filter=apply_filter,
    )

    proj_df = result.get("projections_df", pd.DataFrame())
    if proj_df.empty:
        raise HTTPException(status_code=500, detail="Could not generate projections from slate")

    resolved_site = result.get("site", site.upper())
    from analysis.nba.optimizer import SITE_RULES, _eligibility_set, _slot_is_eligible

    rules = SITE_RULES.get(resolved_site, SITE_RULES["DK"])
    salary_cap: int = rules["salary_cap"]
    slots: dict = rules["slots"]

    # Name → projection row lookup (case-insensitive + fuzzy)
    name_to_row: dict[str, pd.Series] = {}
    all_slate_names: list[str] = []
    for _, row in proj_df.iterrows():
        n = str(row.get("Name", "")).strip()
        if n:
            name_to_row[n.lower()] = row
            all_slate_names.append(n.lower())

    def lookup(name: str):
        exact = name_to_row.get(name.lower())
        if exact is not None:
            return exact, str(exact.get("Name", name))
        matches = _difflib.get_close_matches(name.lower(), all_slate_names, n=1, cutoff=0.72)
        if matches:
            return name_to_row[matches[0]], str(name_to_row[matches[0]].get("Name", matches[0]))
        hits = [n for n in all_slate_names if name.lower() in n]
        if hits:
            return name_to_row[hits[0]], str(name_to_row[hits[0]].get("Name", hits[0]))
        return None, None

    has_sim_batch = "Sim_P90" in proj_df.columns
    has_floor_batch = "Sim_P10" in proj_df.columns

    # game→teams map for correlation bonuses
    team_col_batch = "Team" if "Team" in proj_df.columns else "TeamAbbrev"
    game_col_batch = next((c for c in ["GameInfo", "Game Info", "Game"] if c in proj_df.columns), None)
    game_to_teams_batch: dict[str, set] = {}
    if game_col_batch:
        for _, _row in proj_df.iterrows():
            g = str(_row.get(game_col_batch, ""))
            t = str(_row.get(team_col_batch, ""))
            if g and t:
                game_to_teams_batch.setdefault(g, set()).add(t)

    # Track how many times a player has been selected across lineups (for diversity)
    replacement_count: dict[str, int] = {}

    all_lineup_rows: list[dict] = []
    swap_log: list[dict] = []

    for lineup_idx, lineup_players in enumerate(raw_lineups):
        # Resolve each player name and flag if scratched
        resolved: list[dict] = []
        for pname in lineup_players:
            row, rname = lookup(pname)
            salary = int(pd.to_numeric(row.get("Salary", 0), errors="coerce") or 0) if row is not None else 0
            pos = str(row.get("Pos", "UTIL")) if row is not None else "UTIL"
            proj = float(pd.to_numeric(row.get("Proj", 0), errors="coerce") or 0) if row is not None else 0
            is_scratched = (
                (pname.lower() in scratch_names_lower or (rname is not None and rname.lower() in scratch_names_lower))
                and pname.lower() not in locked_names_lower
                and (rname is None or rname.lower() not in locked_names_lower)
            )
            resolved.append({
                "input_name": pname,
                "resolved_name": rname or pname,
                "row": row,
                "salary": salary,
                "pos": pos,
                "proj": proj,
                "is_scratched": is_scratched,
            })

        locked = [p for p in resolved if not p["is_scratched"]]
        scratched_slots = [p for p in resolved if p["is_scratched"]]
        locked_salary = sum(p["salary"] for p in locked)

        # Build the final lineup by filling each scratched slot with the best available player
        new_lineup = list(locked)
        current_salary = locked_salary
        lineup_swaps: list[dict] = []

        for sr in scratched_slots:
            scratch_elig = _eligibility_set(sr["pos"])
            already_in_lower = {p["resolved_name"].lower() for p in new_lineup}
            budget = salary_cap - current_salary

            # Gather eligible candidates
            candidates: list[dict] = []
            for _, cand_row in proj_df.iterrows():
                cand_name = str(cand_row.get("Name", "")).strip()
                if not cand_name:
                    continue
                if cand_name.lower() in already_in_lower:
                    continue
                if cand_name.lower() in scratch_names_lower:
                    continue
                if cand_name.lower() in locked_names_lower:
                    continue  # can't swap in a locked (started-game) player
                cand_salary = int(pd.to_numeric(cand_row.get("Salary", 0), errors="coerce") or 0)
                if cand_salary > budget:
                    continue
                cand_pos = str(cand_row.get("Pos", "UTIL"))
                cand_elig = _eligibility_set(cand_pos)
                eligible = any(
                    _slot_is_eligible(resolved_site, slot_name, scratch_elig)
                    and _slot_is_eligible(resolved_site, slot_name, cand_elig)
                    for slot_name in slots
                )
                if not eligible:
                    continue
                cand_proj = float(pd.to_numeric(cand_row.get("Proj", 0), errors="coerce") or 0)
                cand_own = float(pd.to_numeric(cand_row.get("Own", 0), errors="coerce") or 0)
                cand_value = round(cand_proj / cand_salary * 1000, 2) if cand_salary > 0 else 0.0
                # Floor / ceil for scoring projection
                cand_ceil_b = float(pd.to_numeric(cand_row.get("Sim_P90", 0), errors="coerce") or 0) if has_sim_batch else cand_proj * 1.30
                cand_floor_b = float(pd.to_numeric(cand_row.get("Sim_P10", 0), errors="coerce") or 0) if has_floor_batch else cand_proj * 0.75
                cand_game_b = str(cand_row.get(game_col_batch, "")) if game_col_batch else ""
                cand_team_b = str(cand_row.get(team_col_batch, ""))
                score_proj_b = scoring_projection(cand_proj, cand_floor_b, cand_ceil_b, contest_type, has_sim_batch)
                candidates.append({
                    "name": cand_name,
                    "row": cand_row,
                    "salary": cand_salary,
                    "proj": cand_proj,
                    "own": cand_own,
                    "value": cand_value,
                    # Use score_proj for ranking (may be floor or ceil depending on contest_type)
                    "projection": score_proj_b,
                    "game_info": cand_game_b,
                    "team": cand_team_b,
                    "swap_score": 0.0,
                })

            if not candidates:
                lineup_swaps.append({
                    "scratched": sr["resolved_name"],
                    "replacement": None,
                    "note": "no eligible replacement found within budget",
                })
                continue

            # Build lineup game/team context for correlation (non-scratched players only)
            lu_games_b: set[str] = set()
            lu_teams_b: set[str] = set()
            lu_game_counts_b: dict[str, int] = {}
            lu_team_counts_b: dict[str, int] = {}
            for p in new_lineup:
                p_row = p.get("row")
                if p_row is not None:
                    g = str(p_row.get(game_col_batch, "")) if game_col_batch else ""
                    t = str(p_row.get(team_col_batch, ""))
                    if g:
                        lu_games_b.add(g)
                        lu_game_counts_b[g] = lu_game_counts_b.get(g, 0) + 1
                    if t:
                        lu_teams_b.add(t)
                        lu_team_counts_b[t] = lu_team_counts_b.get(t, 0) + 1

            # Score candidates with configurable weights + correlation bonus
            score_candidates(
                candidates,
                w_proj=wp, w_value=wv, w_own=wo,
                lineup_games=lu_games_b,
                lineup_teams=lu_teams_b,
                game_to_teams=game_to_teams_batch,
                lineup_game_counts=lu_game_counts_b,
                lineup_team_counts=lu_team_counts_b,
            )

            # Apply diversity penalty: effective_score = swap_score / (1 + diversity_factor * times_used)
            for c in candidates:
                times_used = replacement_count.get(c["name"], 0)
                c["_effective_score"] = c["swap_score"] / (1 + diversity_factor * times_used)

            best_idx = max(range(len(candidates)), key=lambda i: candidates[i]["_effective_score"])
            best = candidates[best_idx]
            # Track diversity — increment usage count for the chosen player
            replacement_count[best["name"]] = replacement_count.get(best["name"], 0) + 1
            new_lineup.append({
                "input_name": best["name"],
                "resolved_name": best["name"],
                "row": best["row"],
                "salary": best["salary"],
                "pos": str(best["row"].get("Pos", "UTIL")),
                "proj": best["proj"],
                "is_scratched": False,
            })
            current_salary += best["salary"]
            lineup_swaps.append({
                "scratched": sr["resolved_name"],
                "replacement": best["name"],
                "salary_delta": best["salary"] - sr["salary"],
                "proj_delta": round(best["proj"] - sr["proj"], 2),
            })

        # Append rows for this lineup into the export DataFrame
        for player in new_lineup:
            row = player["row"]
            if row is None:
                continue
            row_dict = row.to_dict()
            row_dict["Name"] = player["resolved_name"]
            row_dict["LineupIndex"] = lineup_idx
            all_lineup_rows.append(row_dict)

        swap_log.append({
            "lineup_index": lineup_idx + 1,
            "total_salary": current_salary,
            "salary_cap": salary_cap,
            "players_kept": len(locked),
            "swaps_made": len([s for s in lineup_swaps if s.get("replacement")]),
            "swaps_failed": len([s for s in lineup_swaps if not s.get("replacement")]),
            "swaps": lineup_swaps,
        })

    if not all_lineup_rows:
        raise HTTPException(status_code=500, detail="Failed to produce any modified lineups")

    lineups_df = pd.DataFrame(all_lineup_rows)
    csv_data = _lineups_to_export_csv(lineups_df, resolved_site)
    lineup_file = await save_lineup_file(csv_data, file_info["file_id"], user_id=user_id)

    return {
        "success": True,
        "site": resolved_site,
        "total_lineups": len(raw_lineups),
        "swap_log": swap_log,
        "download_file": lineup_file["file_name"],
    }


@router.get("/download/{file_name}")
async def download_lineups(file_name: str, current_user=Depends(get_current_user)):
    file_path = get_file_path(file_name, "lineup", _user_id(current_user))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    with open(file_path, "r") as f:
        content = f.read()

    return {
        "success": True,
        "file_name": file_name,
        "data": content,
    }
