"""Check analytics tables in dfs_edge.duckdb."""
import sys
sys.path.insert(0, '/app')
from analysis.shared.db import get_conn

conn = get_conn('/app/data/dfs_edge.duckdb', db_key='dfs_edge')

tables = conn.execute("SHOW TABLES").fetchall()
print("Tables:", [t[0] for t in tables])

seqs = conn.execute("SELECT sequence_name FROM information_schema.sequences").fetchall()
print("Sequences:", [s[0] for s in seqs])

cnt = conn.execute("SELECT COUNT(*) FROM projection_log").fetchone()[0]
print(f"projection_log rows: {cnt}")

cnt2 = conn.execute("SELECT COUNT(*) FROM contest_results").fetchone()[0]
print(f"contest_results rows: {cnt2}")
