"""Temporary script to inspect injury DB contents."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

db = Path("data/nba_news.duckdb")
if not db.exists():
    print("nba_news.duckdb not found"); sys.exit(1)

import duckdb
con = duckdb.connect(str(db), read_only=True)

print("=== TABLES/VIEWS ===")
tables = con.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema='main'").fetchall()
for t, tt in tables:
    print(f"  {tt}: {t}")

for t, tt in tables:
    print(f"\n=== {t} ===")
    try:
        cols = con.execute(f"DESCRIBE {t}").fetchall()
        for c in cols:
            print(f"  col: {c[0]:30s} {c[1]}")
        rows = con.execute(f"SELECT * FROM {t}").fetchall()
        print(f"  {len(rows)} rows:")
        for r in rows:
            print("   ", r)
    except Exception as e:
        print(f"  ERROR: {e}")
con.close()
