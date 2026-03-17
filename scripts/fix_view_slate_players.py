"""
Fix schema-drifted view: vw_nba_slate_players_filtered
The view referenced status_reported_at but the column is fetched_at.
This script drops and recreates the view with the correct column names.
"""
import duckdb
from pathlib import Path

db_path = Path(__file__).resolve().parent.parent / "data" / "nba_news.duckdb"
print(f"DB: {db_path}")

db = duckdb.connect(str(db_path))

# Show current table schema for reference
print("\n--- nba_injury_report columns ---")
for row in db.execute("DESCRIBE nba_injury_report").fetchall():
    print(f"  {row[0]} ({row[1]})")

print("\n--- nba_slate_players columns ---")
for row in db.execute("DESCRIBE nba_slate_players").fetchall():
    print(f"  {row[0]} ({row[1]})")

# Drop broken view
db.execute("DROP VIEW IF EXISTS vw_nba_slate_players_filtered")
print("\nDropped old view.")

# Recreate with correct column references.
# nba_slate_players uses display_name (not player_name) and has no fppg/slate_date/platform/sport.
db.execute("""
CREATE OR REPLACE VIEW vw_nba_slate_players_filtered AS
SELECT
    sp.display_name          AS player_name,
    sp.team,
    sp.position,
    sp.salary,
    sp.game_info,
    sp.created_at            AS slate_date,
    inj.status               AS injury_status,
    inj.reason               AS injury_reason,
    inj.fetched_at           AS status_updated_at
FROM nba_slate_players sp
LEFT JOIN (
    SELECT player_name, status, reason, fetched_at,
           ROW_NUMBER() OVER (PARTITION BY player_name ORDER BY fetched_at DESC) AS rn
    FROM nba_injury_report
) inj ON inj.player_name = sp.display_name AND inj.rn = 1
""")

# Verify
count = db.execute("SELECT COUNT(*) FROM vw_nba_slate_players_filtered").fetchone()[0]
print(f"View recreated OK — {count} rows")
db.close()
