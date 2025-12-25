import pandas as pd
import re


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
      '12345 - Name' -> '12345'
      '12345'        -> '12345'
      fallback: cleaned name
    """
    if cell is None:
        return None

    v = str(cell).strip()
    if not v or v.lower() in ("nan", "none"):
        return None

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
