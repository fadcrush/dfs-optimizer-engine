"""Quick smoke test for pool_filter and replacement_engine."""
import sys
import logging
import pandas as pd
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

sys.path.insert(0, r"F:\Dev\N_B_A_and_N_F_L")

from analysis.nba.pool_filter import apply_pool_filter, PoolFilterConfig
from analysis.nba.replacement_engine import compute_boosts

# ── Pool filter smoke test ────────────────────────────────────────────────────
test_players = pd.DataFrame({
    "Name":         ["LeBron James", "Anthony Davis", "Austin Reaves", "Rui Hachimura"],
    "Team":         ["LAL", "LAL", "LAL", "LAL"],
    "Proj":         [52.0,  48.0,  28.0,  22.0],
    "Ceiling":      [70.0,  65.0,  40.0,  35.0],
    "Floor":        [35.0,  30.0,  15.0,  10.0],
    "Own":          [35.0,  28.0,  12.0,   8.0],
    "Salary":       [10000, 9800,  5600,  4800],
    "InjuryStatus": ["OUT", "",    "",    ""],
})

cfg = PoolFilterConfig(exclude_out=True, min_projection=10.0)
filtered, report = apply_pool_filter(test_players, cfg=cfg)

print("=== Pool Filter ===")
print("Report:", report)
print("Remaining:", filtered["Name"].tolist())
assert "LeBron James" not in filtered["Name"].tolist(), "OUT player should be removed"
assert len(filtered) == 3, f"Expected 3 players, got {len(filtered)}"
assert "chalk_flag" in filtered.columns
assert "volatile_tier" in filtered.columns
print("PASS\n")

# ── Replacement engine smoke test ─────────────────────────────────────────────
print("=== Replacement Engine ===")
boosts = compute_boosts(["LeBron James"], slate_players=test_players)
print("Boosts DataFrame:")
print(boosts)
print(f"Columns: {list(boosts.columns)}")
print("PASS\n")

# ── verify game log data present ──────────────────────────────────────────────
import duckdb
con = duckdb.connect(r"F:\Dev\N_B_A_and_N_F_L\data\dfs_edge.duckdb", read_only=True)
n = con.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]
print(f"=== DB Check: player_game_logs has {n:,} rows ===")
assert n > 1000, f"Expected >1000 game log rows, got {n}"
sample = con.execute("""
    SELECT player_name, team, game_date, minutes, dk_pts
    FROM player_game_logs WHERE team='LAL'
    ORDER BY game_date DESC LIMIT 5
""").df()
print(sample.to_string(index=False))
con.close()
print("\nAll checks passed.")
