import pandas as pd
from pathlib import Path
from itertools import combinations
from collections import Counter

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"

def extract_ids_from_lineups(df, cols):
    ids = []
    for _, row in df.iterrows():
        lineup = []
        for c in cols:
            val = row.get(c)
            if pd.isna(val):
                continue
            lineup.append(str(int(val)) if isinstance(val, (int, float)) else str(val).strip())
        ids.append([x for x in lineup if x])
    return ids

def build_synergy(lineups_csv_path, sport, site, position_cols):
    df = pd.read_csv(lineups_csv_path)
    lineups = extract_ids_from_lineups(df, position_cols)

    pair_counts = Counter()

    for lineup in lineups:
        for a, b in combinations(sorted(set(lineup)), 2):
            pair_counts[(a, b)] += 1

    rows = []
    total = len(lineups)

    for (a, b), cnt in pair_counts.items():
        rows.append({
            "player_a": a,
            "player_b": b,
            "count": cnt,
            "synergy_pct": round(cnt / total * 100, 3)
        })

    out = pd.DataFrame(rows).sort_values("count", ascending=False)
    out_dir = DATA_DIR / sport
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"synergy_{site}.csv"
    out.to_csv(out_path, index=False)

    print(f"[OK] Saved synergy -> {out_path}")

if __name__ == "__main__":
    # YOU call this with a lineup file path when needed
    pass
