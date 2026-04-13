"""One-time migration: move dfs_master.duckdb tables into dfs_edge.duckdb."""
import duckdb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "data"
MASTER = ROOT / "dfs_master.duckdb"
EDGE = ROOT / "dfs_edge.duckdb"

src = duckdb.connect(str(MASTER), read_only=True)
dst = duckdb.connect(str(EDGE))

# 1. projection_log
dst.execute("""
CREATE TABLE IF NOT EXISTS projection_log (
    id INTEGER, slate_date DATE, site VARCHAR, player_id VARCHAR,
    player_name VARCHAR, salary INTEGER, proj DOUBLE, floor DOUBLE,
    ceiling DOUBLE, ownership DOUBLE, actual_pts DOUBLE,
    reconciled BOOLEAN, created_at TIMESTAMP
)
""")
existing = dst.execute("SELECT COUNT(*) FROM projection_log").fetchone()[0]
if existing == 0:
    rows = src.execute("SELECT * FROM projection_log").fetchall()
    if rows:
        placeholders = ", ".join(["?"] * len(rows[0]))
        dst.executemany(f"INSERT INTO projection_log VALUES ({placeholders})", rows)
        print(f"Copied {len(rows)} rows to projection_log")
    else:
        print("projection_log: no rows to copy")
else:
    print(f"projection_log already has {existing} rows, skipping")

# 2. ownership_history (0 rows in source, just ensure schema)
dst.execute("""
CREATE TABLE IF NOT EXISTS ownership_history (
    id INTEGER PRIMARY KEY, slate_date DATE, sport VARCHAR,
    site VARCHAR, player_name VARCHAR, dfs_id VARCHAR,
    salary FLOAT, projection FLOAT, ownership_pct FLOAT,
    actual_score FLOAT, created_at TIMESTAMPTZ DEFAULT now()
)
""")
print("ownership_history table ensured")

# 3. contest_results
tables = [t[0] for t in dst.execute("SHOW TABLES").fetchall()]
if "contest_results" not in tables:
    rows = src.execute("SELECT * FROM contest_results").fetchall()
    dst.execute("""
    CREATE TABLE contest_results (
        id INTEGER, contest_date DATE, contest_type VARCHAR, site VARCHAR,
        entry_fee DOUBLE, payout DOUBLE, final_rank INTEGER,
        total_entries INTEGER, lineup_proj DOUBLE, lineup_actual DOUBLE,
        notes VARCHAR, created_at TIMESTAMP
    )
    """)
    if rows:
        placeholders = ", ".join(["?"] * len(rows[0]))
        dst.executemany(f"INSERT INTO contest_results VALUES ({placeholders})", rows)
        print(f"Copied {len(rows)} rows to contest_results")
else:
    print("contest_results already exists")

# Verify
for t in ["projection_log", "ownership_history", "contest_results"]:
    cnt = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"dfs_edge.{t}: {cnt} rows")

src.close()
dst.close()
print("Migration complete")
