import duckdb

edge = duckdb.connect("/app/data/dfs_edge.duckdb", read_only=True)
own  = duckdb.connect("/app/data/ownership_history.duckdb", read_only=True)

print("=== player_game_logs sample names ===")
rows = edge.execute("SELECT DISTINCT player_name FROM player_game_logs ORDER BY player_name LIMIT 10").fetchall()
for r in rows:
    print(" ", repr(r[0]))

print("\n=== ownership_history sample names ===")
rows2 = own.execute("SELECT DISTINCT player_name FROM ownership_history ORDER BY player_name LIMIT 10").fetchall()
for r in rows2:
    print(" ", repr(r[0]))

# Check overlap
gl = {r[0] for r in edge.execute("SELECT DISTINCT player_name FROM player_game_logs").fetchall()}
oh = {r[0] for r in own.execute("SELECT DISTINCT player_name FROM ownership_history").fetchall()}
overlap = gl & oh
print(f"\nGame log unique names: {len(gl)}")
print(f"Ownership unique names: {len(oh)}")
print(f"Exact matches: {len(overlap)}")

# Sample non-matching
only_own = sorted(oh - gl)[:5]
print(f"\nIn ownership but NOT in game_logs:")
for n in only_own:
    print(f"  {repr(n)}")

# Check if game_log names match with minor diff
import re
def normalize(name):
    return re.sub(r"[^a-z]", "", name.lower())
gl_norm = {normalize(n): n for n in gl}
matched_norm = 0
for n in oh:
    if normalize(n) in gl_norm:
        matched_norm += 1
print(f"\nFuzzy-normalized matches: {matched_norm} / {len(oh)}")

edge.close()
own.close()
