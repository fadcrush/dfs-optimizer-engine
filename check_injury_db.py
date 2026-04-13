import duckdb

db = duckdb.connect("data/nba_news.duckdb", read_only=True)

print("=== TABLES ===")
print(db.execute("SHOW TABLES").fetchall())
print()

print("=== player_injury_state rows ===")
r = db.execute("SELECT count(*) FROM player_injury_state").fetchone()
print(f"count: {r[0]}")
if r[0] > 0:
    print(db.execute("SELECT * FROM player_injury_state LIMIT 5").df())
print()

print("=== vw_nba_injury_status columns ===")
cols = db.execute("PRAGMA table_info('vw_nba_injury_status')").fetchall()
col_names = [c[1] for c in cols]
print(col_names)
print()

print("=== vw_nba_injury_status rows ===")
r2 = db.execute("SELECT count(*) FROM vw_nba_injury_status").fetchone()
print(f"count: {r2[0]}")

print("=== Sample rows ===")
sample = db.execute("SELECT * FROM vw_nba_injury_status LIMIT 3").df()
print(sample.to_string())
print()

# Figure out which column has the status
for cn in col_names:
    if "status" in cn.lower() or "injury" in cn.lower() or "out" in cn.lower():
        vals = db.execute(f'SELECT "{cn}", count(*) c FROM vw_nba_injury_status GROUP BY "{cn}" ORDER BY c DESC').fetchall()
        print(f"Column '{cn}' values: {vals}")

print()
print("=== player_injury_state columns ===")
pis_cols = db.execute("PRAGMA table_info('player_injury_state')").fetchall()
print([c[1] for c in pis_cols])

db.close()
