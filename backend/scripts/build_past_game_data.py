import os
import glob
import pandas as pd
import numpy as np
from pathlib import Path

# -------------------------------
# INPUT FOLDERS (yours)
# -------------------------------
FOLDERS = {
    "nba_fd": r"E:\N_B_A\FD_NBA_Past_Games",
    "nba_dk": r"E:\N_B_A\DK_NBA_Past_Games",
    "nfl_fd": r"E:\N_B_A\FD_NFL_Past_Games",
    "nfl_dk": r"E:\N_B_A\DK_NFL_Past_Games",
}

# -------------------------------
# OUTPUT ROOT
# -------------------------------
OUTPUT_ROOT = Path("data")


def detect_column(df, candidates):
    """Return the first candidate column that exists in df.columns (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None


def normalize_site_dataset(folder_path, sport, site):
    """
    Reads all CSVs in folder_path and extracts:
      DFS ID, Name, Pos, Team, Salary, Actual, Live Proj
    Returns a single big dataframe.
    """
    all_rows = []
    csv_files = glob.glob(os.path.join(folder_path, "*.csv"))

    for file in csv_files:
        try:
            df = pd.read_csv(file)
        except Exception as e:
            print(f"Skipping {file}: {e}")
            continue

        # Detect standard columns (projections files are consistent but may vary)
        id_col = detect_column(df, ["DFS ID", "DfsId", "ID", "PlayerId", "Player ID"])
        name_col = detect_column(df, ["Name", "Player", "Nickname"])
        pos_col = detect_column(df, ["Pos", "Position"])
        team_col = detect_column(df, ["Team"])
        salary_col = detect_column(df, ["Salary"])
        actual_col = detect_column(df, ["Actual", "Fpts", "Fantasy Points"])
        live_proj_col = detect_column(df, ["Live Proj", "Live Projection"])

        if id_col is None or name_col is None:
            print(f"Skipping {file}: missing ID or Name column")
            continue

        # Create consistent dataset
        sub = pd.DataFrame()
        sub["sport"] = sport
        sub["site"] = site
        sub["source_file"] = os.path.basename(file)
        sub["dfs_id"] = df[id_col].astype(str)
        sub["name"] = df[name_col]

        sub["pos"] = df[pos_col] if pos_col else None
        sub["team"] = df[team_col] if team_col else None
        sub["salary"] = df[salary_col] if salary_col else None

        sub["actual"] = df[actual_col] if actual_col else None
        sub["live_proj"] = df[live_proj_col] if live_proj_col else None

        # drop rows missing actual (you can keep them if needed)
        sub = sub.dropna(subset=["dfs_id", "name"], how="any")

        all_rows.append(sub)

    if not all_rows:
        return pd.DataFrame()

    full_df = pd.concat(all_rows, ignore_index=True)

    # ensure numeric where possible
    for c in ["actual", "live_proj", "salary"]:
        if c in full_df.columns:
            full_df[c] = pd.to_numeric(full_df[c], errors="coerce")

    return full_df


def build_player_stats(df):
    """Aggregates floor/ceiling and other metrics per dfs_id."""
    if df.empty:
        return df

    grouped = df.groupby(["dfs_id", "name"])

    stats = grouped.agg(
        games_played=("actual", "count"),
        avg_actual=("actual", "mean"),
        std_actual=("actual", "std"),
        avg_live_proj=("live_proj", "mean"),
        avg_salary=("salary", "mean"),
    ).reset_index()

    # Percentile-based floor/ceiling
    floors = grouped["actual"].quantile(0.25).reset_index().rename(columns={"actual": "floor_25"})
    ceilings = grouped["actual"].quantile(0.75).reset_index().rename(columns={"actual": "ceiling_75"})

    stats = stats.merge(floors, on=["dfs_id", "name"], how="left")
    stats = stats.merge(ceilings, on=["dfs_id", "name"], how="left")

    # Delta from projection
    stats["avg_delta"] = stats["avg_actual"] - stats["avg_live_proj"]

    # Upside ratio
    stats["ceiling_ratio"] = stats["ceiling_75"] / stats["avg_live_proj"]

    stats = stats.sort_values("avg_actual", ascending=False)
    return stats


def save_outputs(df, sport, site):
    """Save raw + aggregated outputs."""
    sport_dir = OUTPUT_ROOT / sport
    past_dir = sport_dir / "past_games"
    agg_dir = sport_dir / "aggregates"

    past_dir.mkdir(parents=True, exist_ok=True)
    agg_dir.mkdir(parents=True, exist_ok=True)

    raw_path = past_dir / f"{sport}_{site}_past_games.csv"
    df.to_csv(raw_path, index=False)
    print(f"Saved raw: {raw_path}")

    stats = build_player_stats(df)
    stats_path = agg_dir / f"player_stats_{site}.csv"
    stats.to_csv(stats_path, index=False)
    print(f"Saved stats: {stats_path}")


def main():
    for key, folder in FOLDERS.items():
        sport, site = key.split("_")  # nba_fd -> sport=nba, site=fd
        print(f"\nProcessing {sport.upper()} {site.upper()} from {folder}")

        df = normalize_site_dataset(folder, sport, site)

        if df.empty:
            print("No valid files found.")
            continue

        save_outputs(df, sport, site)

    print("\n✅ Done building past game datasets.")


if __name__ == "__main__":
    main()
