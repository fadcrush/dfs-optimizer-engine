# analysis/nba/ownership_builder.py

import pandas as pd
from pathlib import Path
from datetime import datetime

# ----------------------------
# CONFIG
# ----------------------------
# Derive project root from this file's location (3 levels up: nba/ → analysis/ → project root)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# Where to look for past lineup CSV exports (configurable via env or call-site override)
HISTORICAL_LINEUPS_DIR = ROOT_DIR / "data" / "uploads"
PLAYER_MAP_FILE = ROOT_DIR / "data" / "nba" / "raw" / "balldontlie_players.csv"
OUTPUT_DIR = ROOT_DIR / "outputs" / "ownership"

# DraftKings / FanDuel roster column detection
ROSTER_COL_KEYWORDS = ["PG", "SG", "SF", "PF", "C", "UTIL", "FLEX"]

# ----------------------------
# LOAD PLAYER MAP
# ----------------------------
def load_player_map():
    df = pd.read_csv(PLAYER_MAP_FILE)
    df["full_name"] = df["first_name"] + " " + df["last_name"]
    return df


# ----------------------------
# LOAD LINEUPS
# ----------------------------
def load_all_lineups():
    all_files = list(HISTORICAL_LINEUPS_DIR.glob("*.csv"))
    if not all_files:
        raise FileNotFoundError("No historical lineup CSVs found.")

    frames = []
    for file in all_files:
        try:
            df = pd.read_csv(file)
            df["source_file"] = file.name
            frames.append(df)
        except Exception as e:
            print(f"Skipping {file.name}: {e}")

    return pd.concat(frames, ignore_index=True)


# ----------------------------
# DETECT PLAYER COLUMNS
# ----------------------------
def detect_player_columns(df):
    return [c for c in df.columns if any(k in c.upper() for k in ROSTER_COL_KEYWORDS)]


# ----------------------------
# BUILD OWNERSHIP
# ----------------------------
def build_ownership(df, player_map):
    player_cols = detect_player_columns(df)

    melted = df.melt(
        id_vars=["source_file"],
        value_vars=player_cols,
        value_name="player"
    ).dropna(subset=["player"])

    # Normalize player values
    melted["player"] = melted["player"].astype(str).str.strip()

    total_lineups = df.shape[0]

    ownership = (
        melted.groupby("player")
        .size()
        .reset_index(name="appearances")
    )

    ownership["ownership_pct"] = (
        ownership["appearances"] / total_lineups * 100
    ).round(2)

    # Attach player names when possible
    ownership = ownership.merge(
        player_map,
        how="left",
        left_on="player",
        right_on="id"
    )

    ownership["display_name"] = ownership["full_name"].fillna(ownership["player"])

    ownership = ownership.sort_values(
        "ownership_pct", ascending=False
    )

    return ownership


# ----------------------------
# SAVE RESULTS
# ----------------------------
def save_output(df):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_file = OUTPUT_DIR / f"nba_ownership_{timestamp}.csv"
    df.to_csv(out_file, index=False)
    print(f"Ownership file saved: {out_file}")


# ----------------------------
# MAIN
# ----------------------------
def main():
    print("📊 Building NBA Ownership Model...")

    player_map = load_player_map()
    lineups = load_all_lineups()

    ownership = build_ownership(lineups, player_map)

    save_output(ownership)

    print("\n🔥 Top 20 Owned Players:")
    print(
        ownership[
            ["display_name", "ownership_pct", "appearances"]
        ].head(20)
    )


if __name__ == "__main__":
    main()
