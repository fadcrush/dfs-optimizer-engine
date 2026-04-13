import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(name)s | %(levelname)s | %(message)s")

from workers.schedulers.daily import job_refresh_injuries

print("=== Calling job_refresh_injuries() ===")
job_refresh_injuries()
print("=== Done ===")

# Force release any leftover conns
from analysis.shared.db import close_all
close_all()

# Check result
import duckdb
db = duckdb.connect("data/nba_news.duckdb", read_only=True)
r = db.execute("SELECT count(*) FROM player_injury_state").fetchone()
print(f"\nplayer_injury_state rows after refresh: {r[0]}")
if r[0] > 0:
    print(db.execute("SELECT player_name, current_status, p_play FROM player_injury_state ORDER BY updated_at DESC LIMIT 10").df())
r2 = db.execute("SELECT count(*) FROM injury_beneficiaries").fetchone()
print(f"injury_beneficiaries rows: {r2[0]}")
db.close()
