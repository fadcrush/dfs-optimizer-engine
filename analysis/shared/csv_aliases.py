from __future__ import annotations

import re

import pandas as pd


DK_COLUMN_ALIASES: dict[str, str] = {
    "id": "dfs id",
    "playerid": "dfs id",
    "name": "name",
    "position": "position",
    "roster position": "position",
    "teamabbrev": "team",
    "team_abbrev": "team",
    "team": "team",
    "game info": "game info",
    "salary": "salary",
}


FD_COLUMN_ALIASES: dict[str, str] = {
    "id": "dfs id",
    "playerid": "dfs id",
    "nickname": "nickname",
    "position": "position",
    "roster position": "position",
    "team": "team",
    "game": "game",
    "salary": "salary",
    "injury indicator": "injury indicator",
}


def _clean_column_name(value: object) -> str:
    return str(value).strip().lower()


def apply_site_column_aliases(df: pd.DataFrame, site: str) -> pd.DataFrame:
    aliases = FD_COLUMN_ALIASES if site.upper() == "FD" else DK_COLUMN_ALIASES
    renamed = df.copy()
    lowered = {_clean_column_name(col): col for col in renamed.columns}
    rename_map: dict[str, str] = {}

    # Track which canonical names are already occupied (case-insensitive) so
    # that two different source columns (e.g. "Position" and "Roster Position")
    # never both get renamed to the same target, creating duplicate columns that
    # cause df[col] to return a DataFrame instead of a Series.
    occupied_canonicals: set[str] = {_clean_column_name(col) for col in renamed.columns}

    for alias, canonical in aliases.items():
        source = lowered.get(alias)
        if not source:
            continue
        # Already at the canonical name (case-insensitive) — no rename needed
        if _clean_column_name(source) == _clean_column_name(canonical):
            continue
        # Canonical target already occupied by an existing (or already-mapped) column
        if _clean_column_name(canonical) in occupied_canonicals:
            continue
        rename_map[source] = canonical
        # Mark canonical as occupied so no second alias can claim the same target
        occupied_canonicals.add(_clean_column_name(canonical))

    if rename_map:
        renamed = renamed.rename(columns=rename_map)
    return renamed


def extract_opponent(game_info: object, team_abbr: object) -> str:
    if not isinstance(game_info, str) or not isinstance(team_abbr, str):
        return ""

    team = team_abbr.strip().upper()
    if not team:
        return ""

    text = game_info.upper().strip()
    if not text:
        return ""

    tokens = re.findall(r"[A-Z]{2,4}", text)
    excluded = {team, "AT", "VS", "ET", "PM", "AM"}
    for token in tokens:
        if token not in excluded:
            return token
    return ""