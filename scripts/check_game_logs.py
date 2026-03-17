import sys, duckdb
sys.path.insert(0, '/app')
from analysis.nba.ownership_v2 import ROOT

edge_db = ROOT / "data" / "dfs_edge.duckdb"
own_db  = ROOT / "data" / "ownership_history.duckdb"

# Check game logs
try:
    con = duckdb.connect(str(edge_db), read_only=True)
    tables = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE'").fetchall()
    print("=== dfs_edge tables ===")
    for (t,) in tables:
        if t == 'schema_version':
            continue
        count = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"  {t}: {count:,} rows")
        if count > 0 and t == 'player_game_logs':
            sample = con.execute("SELECT player_name, game_date, dk_pts, is_home FROM player_game_logs LIMIT 3").fetchall()
            for r in sample:
                print(f"    {r}")
    con.close()
except Exception as e:
    print(f"dfs_edge error: {e}")

# Check name overlap
try:
    edge_con = duckdb.connect(str(edge_db), read_only=True)
    own_con  = duckdb.connect(str(own_db),  read_only=True)
    gl_names = {r[0] for r in edge_con.execute("SELECT DISTINCT player_name FROM player_game_logs LIMIT 200").fetchall()}
    oh_names = {r[0] for r in own_con.execute("SELECT DISTINCT player_name FROM ownership_history LIMIT 200").fetchall()}
    print(f"\nGame log sample names: {sorted(gl_names)[:5]}")
    print(f"Ownership sample names: {sorted(oh_names)[:5]}")
    overlap = gl_names & oh_names
    print(f"Exact name overlap (first 200 each): {len(overlap)} matches")
    edge_con.close()
    own_con.close()
except Exception as e:
    print(f"name compare error: {e}")
