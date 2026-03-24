import logging
import pandas as pd
import re
from typing import Any

from analysis.shared.csv_aliases import apply_site_column_aliases, extract_opponent

log = logging.getLogger(__name__)


# ----------------------------
# Basic helpers
# ----------------------------
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Trim column names."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def detect_site_from_lineups(lineups_df: pd.DataFrame) -> str:
    """
    Detect FD vs DK by checking for FD-style IDs: slateId-playerId
    """
    lowered_cols = {str(col).strip().lower() for col in lineups_df.columns}
    if "nickname" in lowered_cols or "injury indicator" in lowered_cols:
        return "FD"
    if "name" in lowered_cols or "game info" in lowered_cols or {"first name", "last name"}.issubset(lowered_cols):
        return "DK"

    sample = lineups_df.astype(str).head(30).values.ravel()
    for v in sample:
        if isinstance(v, str) and "-" in v:
            left, right = v.split("-", 1)
            if left.isdigit() and right.isdigit():
                return "FD"
    return "DK"


# ----------------------------
# Player ID extraction
# ----------------------------
def extract_fd_player_id(cell: str) -> str | None:
    """FanDuel: '124088-203801' -> '203801'"""
    if not isinstance(cell, str):
        return None
    cell = cell.strip()
    if not cell:
        return None
    if "-" in cell:
        left, right = cell.split("-", 1)
        if left.isdigit() and right.isdigit():
            return right
    return None


def extract_dk_player_key(cell: str) -> str | None:
    """
    DraftKings common patterns:
      'Name (12345)' -> '12345'
      '12345:Name'   -> '12345'
      '12345'        -> '12345'
      fallback: cleaned name

    FanDuel composite IDs ('slateId-playerId', e.g. '126855-203801'):
      Return the playerId half ('203801') so DFS_IDs stay unique per player
      even when an FD slate is run through the DK site path.
    """
    if cell is None:
        return None

    v = str(cell).strip()
    if not v or v.lower() in ("nan", "none"):
        return None

    # FD composite ID: "126855-203801" — two pure digit groups joined by hyphen.
    # Extract the PLAYER-ID (right half) so each player has a unique key.
    fd_match = re.match(r"^(\d+)-(\d+)$", v)
    if fd_match:
        return fd_match.group(2)  # player-ID half, not slate-prefix

    m = re.search(r"\((\d+)\)", v)
    if m:
        return m.group(1)

    m = re.match(r"^(\d+)\s*[:\-]", v)
    if m:
        return m.group(1)

    if v.isdigit():
        return v

    return re.sub(r"\s+", " ", v)


# ----------------------------
# Projection standardization
# ----------------------------
def standardize_projection_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standard output:
      DFS_ID, Name, Team, Pos, Salary, Proj, Own

    Supports your 'DFS ID' column.
    """
    df = normalize_columns(df)
    cols = {c.lower().strip(): c for c in df.columns}

    id_col = (
        cols.get("dfs id") or cols.get("dfs_id") or cols.get("dfsid") or
        cols.get("playerid") or cols.get("id")
    )
    name_col = cols.get("name") or cols.get("player") or cols.get("player_name")

    if not id_col or not name_col:
        raise ValueError("Projection file must contain 'DFS ID' (or equivalent) and 'Name'.")

    team_col = cols.get("team")
    pos_col = cols.get("pos") or cols.get("position")
    salary_col = cols.get("salary")

    proj_col = (
        cols.get("my proj") or cols.get("ss proj") or cols.get("proj") or cols.get("projection")
    )
    own_col = cols.get("my own") or cols.get("adj own") or cols.get("own") or cols.get("ownership")

    out = pd.DataFrame()
    out["DFS_ID"] = df[id_col].astype(str).str.replace(".0", "", regex=False).str.strip()
    out["Name"] = df[name_col].astype(str).str.strip()
    out["Team"] = df[team_col].astype(str).str.strip() if team_col else ""
    out["Pos"] = df[pos_col].astype(str).str.strip() if pos_col else ""
    out["Salary"] = df[salary_col] if salary_col else None
    out["Proj"] = df[proj_col] if proj_col else None
    out["Own"] = df[own_col] if own_col else None

    return out


def _resolve_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


def _to_float_series(df: pd.DataFrame, col: str | None, default: float = 0.0) -> pd.Series:
    if not col:
        return pd.Series([default] * len(df), index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def normalize_slate_df(raw_df: pd.DataFrame, site: str | None = None) -> tuple[pd.DataFrame, str]:
    """Normalize slate CSV once into canonical optimizer/projection columns.

    Canonical columns:
      DFS_ID, Name, Team, Opp, Pos, Salary, Base_Proj, Own
    """
    df = normalize_columns(raw_df)
    if site:
        resolved_site = site.upper().strip()
    else:
        resolved_site = detect_site_from_lineups(df)

    df = apply_site_column_aliases(df, resolved_site)

    id_col = _resolve_column(df, ["dfs id", "dfs_id", "id", "playerid"])
    first_col = _resolve_column(df, ["first name", "firstname"])
    last_col = _resolve_column(df, ["last name", "lastname"])
    nickname_col = _resolve_column(df, ["nickname", "name", "player", "player_name"])
    pos_col = _resolve_column(df, ["position", "pos", "roster position"])
    team_col = _resolve_column(df, ["team", "teamabbrev", "team_abbrev"])
    opp_col = _resolve_column(df, ["opp", "opponent"])
    salary_col = _resolve_column(df, ["salary", "sal"])
    # ── Projection column: NEVER use DK/FD stock averages (AvgPointsPerGame, FPPG, etc.)
    # Only accept explicitly user-supplied columns.  GL_L10 from the game-log DB
    # is the true projection baseline and fires in Layer 2 of the projection engine.
    proj_col = _resolve_column(df, ["my proj", "ss proj", "proj", "projection"])
    # Emit a clear log when DK/FD stock columns are present but intentionally ignored
    _dk_fd_blocked = [c for c in df.columns if str(c).lower().strip() in (
        "avgpointspergame", "fppg", "fpts", "points", "fp"
    )]
    if _dk_fd_blocked:
        log.info(
            "PROJECTION GUARD: DK/FD stock columns %s detected and BLOCKED — "
            "our L10 game-log baseline will be used instead.",
            _dk_fd_blocked,
        )
    own_col = _resolve_column(df, ["my own", "adj own", "own", "ownership"])
    # FanDuel includes an "Injury Indicator" column ("O","Q","GTD","SSPD","NA").
    # DraftKings uses "Game Time Decision" or "Status".
    injury_col = _resolve_column(df, [
        "injury indicator", "injury_indicator", "injuryindicator",
        "game time decision", "gametimededecision", "status", "injury status",
    ])

    if not id_col:
        raise ValueError("Slate is missing required ID column (DFS ID / id / playerId).")

    if nickname_col:
        name_series = df[nickname_col].astype(str).str.strip()
    elif first_col and last_col:
        name_series = (df[first_col].astype(str).str.strip() + " " + df[last_col].astype(str).str.strip()).str.strip()
    else:
        raise ValueError("Slate is missing player name columns (Nickname/Name or First Name + Last Name).")

    out = pd.DataFrame(index=df.index)
    out["Name"] = name_series
    out["Team"] = df[team_col].astype(str).str.strip() if team_col else ""
    opp_series = df[opp_col].astype(str).str.strip() if opp_col else pd.Series("", index=df.index, dtype=str)
    game_info_col = _resolve_column(df, ["game info", "game"])
    if game_info_col and team_col:
        derived_opp = df.apply(
            lambda row: extract_opponent(row.get(game_info_col, ""), row.get(team_col, "")),
            axis=1,
        )
        out["Opp"] = opp_series.mask(opp_series.eq(""), derived_opp)
    else:
        out["Opp"] = opp_series
    out["Pos"] = df[pos_col].astype(str).str.strip() if pos_col else "UTIL"
    out["Salary"] = _to_float_series(df, salary_col, default=0.0)
    out["Base_Proj"] = _to_float_series(df, proj_col, default=0.0)
    out["Own"] = _to_float_series(df, own_col, default=0.0)

    # Carry the site's injury indicator forward so pool_filter can hard-gate OUT players
    # without relying solely on nba_news.duckdb.  Normalise to uppercase.
    if injury_col:
        raw_inj = df[injury_col].astype(str).str.strip().str.upper()
        # FD uses "NA" meaning "no status" — treat as blank
        out["InjuryStatus"] = raw_inj.replace({"NA": "", "N/A": "", "NAN": "", "NONE": ""})
    else:
        out["InjuryStatus"] = ""

    if resolved_site == "FD":
        out["DFS_ID"] = df[id_col].astype(str).map(lambda value: extract_fd_player_id(value) or str(value).strip())
    elif resolved_site == "DK":
        out["DFS_ID"] = df[id_col].astype(str).map(lambda value: extract_dk_player_key(value) or str(value).strip())
    else:
        raise ValueError(f"Unsupported site: {resolved_site}. Expected FD or DK.")

    # Preserve the original (full) ID before extraction so export functions can
    # reconstruct the site's lineup-upload format (e.g. FD "126855-157833").
    out["Raw_DFS_ID"] = df[id_col].astype(str).str.strip()

    out = out[out["DFS_ID"].astype(str).str.len() > 0].copy()
    out["DFS_ID"] = out["DFS_ID"].astype(str)
    return out, resolved_site
