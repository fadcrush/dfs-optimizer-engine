"""NFL player pool and lineup / projection analysis."""

import pandas as pd


def _guess_player_columns(df: pd.DataFrame) -> list:
    """
    Guess columns that contain NFL player identifiers for lineups.

    Your format example:

        QB  RB  RB  WR  WR  WR  TE  FLEX  DST
        41033941 41034020 ...

    So we treat:
        QB, RB, WR, TE, FLEX, DST, D/ST, SUPERFLEX
    as "player columns".
    """
    position_like = {"QB", "RB", "WR", "TE", "FLEX", "DST", "D/ST", "SUPERFLEX"}

    cols = []
    for col in df.columns:
        upper = str(col).upper()
        if upper in position_like or "PLAYER" in upper:
            cols.append(col)
    return cols


def analyze_nfl_lineups(lineups_df: pd.DataFrame) -> dict:
    """
    NFL single-file analysis:

    - If it's a lineup export (one row per lineup, multiple player columns)
      -> return player_frequency dict.

    - If it's a projection slate (one row per player, with Name, Pos, etc.)
      -> return projections list.
    """

    result = {
        "mode": None,
        "player_frequency": {},
        "projections": [],
    }

    # --- Try lineup mode first ---
    player_cols = _guess_player_columns(lineups_df)

    if player_cols:
        players = lineups_df[player_cols].values.ravel()
        players = [
            p.strip()
            for p in players
            if isinstance(p, str) and str(p).strip()
        ]
        if players:
            freq = pd.Series(players).value_counts().to_dict()
            result["mode"] = "lineups"
            result["player_frequency"] = freq
            return result

    # --- Fallback: projection-only mode ---
    if "Name" in lineups_df.columns:
        preferred_cols = [
            "Name", "Pos", "Team", "Opp", "Salary",
            "My Proj", "SS Proj", "Actual", "My Own", "Adj Own", "Value",
        ]
        cols_to_use = [c for c in preferred_cols if c in lineups_df.columns]

        proj_df = lineups_df[cols_to_use].copy()

        rename_map = {
            "My Proj": "My_Proj",
            "SS Proj": "SS_Proj",
            "My Own": "My_Own",
            "Adj Own": "Adj_Own",
        }
        proj_df.rename(columns=rename_map, inplace=True)

        sort_candidates = ["My_Proj", "SS_Proj", "Actual"]
        sort_col = next((c for c in sort_candidates if c in proj_df.columns), None)
        if sort_col:
            proj_df = proj_df.sort_values(sort_col, ascending=False)

        result["mode"] = "projections"
        result["projections"] = proj_df.to_dict(orient="records")
        return result

    result["mode"] = "unknown"
    return result


def analyze_nfl_lineups_with_projections(
    lineups_df: pd.DataFrame, proj_df: pd.DataFrame
) -> dict:
    """
    MAIN NFL ANALYSIS:

    - Takes a lineup CSV + projections CSV
    - Uses lineup position columns (QB/RB/WR/TE/FLEX/DST) to pull player identifiers
    - Joins on proj_df['DFS ID'] if present, otherwise on Name
    - Returns:
      - lineup_exposure by player
      - core/secondary/contrarian/fade pools
      - per-lineup summaries: projected FP + leverage profile
    """

    result = {
        "mode": "lineups_with_projections",
        "lineup_exposure": [],
        "num_lineups": len(lineups_df),
        "core_pool": [],
        "secondary_pool": [],
        "contrarian_pool": [],
        "fades": [],
        "lineups_summary": [],
    }

    player_cols = _guess_player_columns(lineups_df)
    if not player_cols:
        base = analyze_nfl_lineups(lineups_df)
        base["note"] = "Could not detect player columns in NFL lineups."
        return base

    # Flatten player IDs from lineup columns
    ids = lineups_df[player_cols].values.ravel()
    ids = [
        str(x).strip()
        for x in ids
        if isinstance(x, str) and str(x).strip()
    ]
    if not ids:
        return result

    total_lineups = len(lineups_df)
    freq = pd.Series(ids).value_counts()  # index: identifier (DFS ID or ID), value: count

    # --- Build lookup from projections ---

    # Prefer to join on 'DFS ID'; if not present, join on 'Name'
    join_field = None
    if "DFS ID" in proj_df.columns:
        join_field = "DFS ID"
    elif "Name" in proj_df.columns:
        join_field = "Name"

    if join_field is None:
        base = analyze_nfl_lineups(lineups_df)
        base["note"] = "Projections file does not have 'DFS ID' or 'Name' column to join on."
        return base

    proj_idx = proj_df.set_index(join_field)

    # Extra: if using 'DFS ID', also build a suffix index
    # Example:
    #   DFS ID: "123820-62239"
    #   Lineup ID: "62239"
    # We want to map "62239" -> that projection row.
    suffix_idx = {}
    if join_field == "DFS ID":
        for dfs_id, pdata in proj_idx.iterrows():
            s = str(dfs_id)
            if "-" in s:
                _, suffix = s.split("-", 1)
                if suffix not in suffix_idx:
                    suffix_idx[suffix] = pdata

    rows = []
    for identifier, count in freq.items():
        row = {
            "identifier": identifier,  # could be DFS ID, suffix, or Name
            "count": int(count),
            "exposure_pct": round(100.0 * count / total_lineups, 1),
            "name": None,
            "pos": None,
            "team": None,
            "salary": None,
            "my_proj": None,
            "my_own": None,
            "adj_own": None,
        }

        pdata = None

        # 1) Direct match on main index (DFS ID or Name)
        if identifier in proj_idx.index:
            pdata = proj_idx.loc[identifier]
            if isinstance(pdata, pd.DataFrame):
                pdata = pdata.iloc[0]
        # 2) If using DFS ID, try matching by suffix
        elif join_field == "DFS ID":
            suffix = str(identifier)
            if suffix in suffix_idx:
                pdata = suffix_idx[suffix]

        if pdata is not None:
            row["name"] = pdata.get("Name") or identifier
            row["pos"] = pdata.get("Pos")
            row["team"] = pdata.get("Team")
            row["salary"] = pdata.get("Salary")
            row["my_proj"] = pdata.get("My Proj") or pdata.get("My_Proj")
            row["my_own"] = pdata.get("My Own") or pdata.get("My_Own")
            row["adj_own"] = pdata.get("Adj Own") or pdata.get("Adj_Own")
        else:
            # No match in projections; leave name as identifier
            row["name"] = identifier

        rows.append(row)

    exposure_df = pd.DataFrame(rows).sort_values("count", ascending=False)

    # --- Player pool classification (similar to NBA) ---

    exposure_df["my_proj_filled"] = exposure_df["my_proj"].fillna(0.0)
    high_proj_threshold = exposure_df["my_proj_filled"].quantile(0.70)  # top 30% by projection

    CORE_EXPOSURE_MIN = 40.0       # 40%+ of lineups
    SECONDARY_EXPOSURE_MIN = 20.0  # 20–40% range
    CONTRARIAN_MAX_EXPOSURE = 15.0 # <= 15% but high projection

    def classify(row):
        proj = float(row["my_proj_filled"] or 0.0)
        exp = float(row["exposure_pct"] or 0.0)

        if proj >= high_proj_threshold and exp >= CORE_EXPOSURE_MIN:
            return "core"
        if proj >= high_proj_threshold and exp <= CONTRARIAN_MAX_EXPOSURE:
            return "contrarian"
        if proj >= high_proj_threshold or exp >= SECONDARY_EXPOSURE_MIN:
            return "secondary"
        return "fade"

    exposure_df["bucket"] = exposure_df.apply(classify, axis=1)

    core_df = exposure_df[exposure_df["bucket"] == "core"]
    secondary_df = exposure_df[exposure_df["bucket"] == "secondary"]
    contrarian_df = exposure_df[exposure_df["bucket"] == "contrarian"]
    fades_df = exposure_df[exposure_df["bucket"] == "fade"]

    # Build map identifier -> info
    info_by_id = {
        str(r["identifier"]): r
        for _, r in exposure_df.iterrows()
    }

    # --- Per-lineup summary ---

    lineup_summaries = []
    for idx, lrow in lineups_df.iterrows():
        ids_in_lineup = []
        for col in player_cols:
            val = lrow.get(col)
            if isinstance(val, str) and val.strip():
                ids_in_lineup.append(val.strip())

        players = [info_by_id.get(i) for i in ids_in_lineup if i in info_by_id]
        if not players:
            continue

        proj_sum = sum(float(p.get("my_proj") or 0.0) for p in players)
        exps = [float(p.get("exposure_pct") or 0.0) for p in players]
        avg_exp = sum(exps) / len(exps)
        max_exp = max(exps)

        core_count = sum(1 for p in players if p.get("bucket") == "core")
        secondary_count = sum(1 for p in players if p.get("bucket") == "secondary")
        contrarian_count = sum(1 for p in players if p.get("bucket") == "contrarian")
        fade_count = sum(1 for p in players if p.get("bucket") == "fade")

        lineup_summaries.append({
            "lineup_index": int(idx) + 1,
            "projected_sum_raw": round(proj_sum, 2),
            "avg_exposure": round(avg_exp, 1),
            "max_exposure": round(max_exp, 1),
            "core_count": core_count,
            "secondary_count": secondary_count,
            "contrarian_count": contrarian_count,
            "fade_count": fade_count,
        })

    lineup_summaries = sorted(
        lineup_summaries,
        key=lambda x: (-x["projected_sum_raw"], x["avg_exposure"])
    )

    result["lineup_exposure"] = exposure_df.to_dict(orient="records")
    result["core_pool"] = core_df.to_dict(orient="records")
    result["secondary_pool"] = secondary_df.to_dict(orient="records")
    result["contrarian_pool"] = contrarian_df.to_dict(orient="records")
    result["fades"] = fades_df.to_dict(orient="records")
    result["lineups_summary"] = lineup_summaries

    return result
