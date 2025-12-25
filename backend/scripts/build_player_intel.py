import os
import glob
import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]  # backend/
DATA_DIR = BASE_DIR / "data"

PAST_FOLDERS = {
    ("nba", "fd"): r"E:\N_B_A\FD_NBA_Past_Games",
    ("nba", "dk"): r"E:\N_B_A\DK_NBA_Past_Games",
    ("nfl", "fd"): r"E:\N_B_A\FD_NFL_Past_Games",
    ("nfl", "dk"): r"E:\N_B_A\DK_NFL_Past_Games",
}

def normalize_cols(df):
    df.columns = [c.strip() for c in df.columns]
    return df

def pick_actual_col(df):
    # Past games: Actual and Live Proj often identical; prefer Actual.
    if "Actual" in df.columns:
        return "Actual"
    if "Live Proj" in df.columns:
        return "Live Proj"
    return None

def build_intel_from_files(folder):
    files = glob.glob(os.path.join(folder, "*.csv"))
    if not files:
        return pd.DataFrame()

    rows = []

    for f in files:
        try:
            df = pd.read_csv(f)
        except Exception:
            continue

        df = normalize_cols(df)
        actual_col = pick_actual_col(df)
        if not actual_col:
            continue

        needed = ["DFS ID", "Name", "Pos", "Team", actual_col]
        for col in needed:
            if col not in df.columns:
                continue

        df["actual_fp"] = pd.to_numeric(df[actual_col], errors="coerce")
        df["ss_proj"] = pd.to_numeric(df.get("SS Proj", np.nan), errors="coerce")
        df["my_proj"] = pd.to_numeric(df.get("My Proj", np.nan), errors="coerce")

        df["delta_ss"] = df["actual_fp"] - df["ss_proj"]
        df["delta_my"] = df["actual_fp"] - df["my_proj"]

        df = df.dropna(subset=["DFS ID", "actual_fp"])
        df["DFS ID"] = df["DFS ID"].astype(str)

        rows.append(df[["DFS ID", "Name", "Pos", "Team", "actual_fp", "ss_proj", "my_proj", "delta_ss", "delta_my"]])

    if not rows:
        return pd.DataFrame()

    all_df = pd.concat(rows, ignore_index=True)

    grouped = all_df.groupby("DFS ID")

    intel = grouped.agg(
        name=("Name", "first"),
        pos=("Pos", "first"),
        team=("Team", "first"),
        games=("actual_fp", "count"),
        avg_actual=("actual_fp", "mean"),
        floor_25=("actual_fp", lambda x: np.percentile(x, 25)),
        median_50=("actual_fp", lambda x: np.percentile(x, 50)),
        ceil_75=("actual_fp", lambda x: np.percentile(x, 75)),
        ceil_85=("actual_fp", lambda x: np.percentile(x, 85)),
        ceil_95=("actual_fp", lambda x: np.percentile(x, 95)),
        std=("actual_fp", "std"),
        avg_delta_ss=("delta_ss", "mean"),
        avg_delta_my=("delta_my", "mean"),
    ).reset_index()

    # Smash/Bust
    intel["smash_rate"] = grouped.apply(lambda g: (g["actual_fp"] >= 1.25 * g["ss_proj"]).mean()).values
    intel["bust_rate"]  = grouped.apply(lambda g: (g["actual_fp"] <= 0.75 * g["ss_proj"]).mean()).values

    return intel.sort_values(["games", "ceil_75"], ascending=False)

def run():
    for (sport, site), folder in PAST_FOLDERS.items():
        intel = build_intel_from_files(folder)
        out_dir = DATA_DIR / sport
        out_dir.mkdir(parents=True, exist_ok=True)

        out_path = out_dir / f"player_intel_{site}.csv"
        intel.to_csv(out_path, index=False)
        print(f"[OK] Saved {sport.upper()} {site.upper()} intel -> {out_path} ({len(intel)} rows)")

if __name__ == "__main__":
    run()
