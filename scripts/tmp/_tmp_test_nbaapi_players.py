"""Test nba_api for player status/roster info."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba_api.stats.endpoints import CommonAllPlayers
import time

print("Fetching CommonAllPlayers...")
cap = CommonAllPlayers(is_only_current_season=1, timeout=30)
df = cap.get_data_frames()[0]
print("Columns:", list(df.columns))
print(f"Total players: {len(df)}")
print(df.head(3).to_string())
