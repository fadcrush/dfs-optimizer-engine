"""Quick test of SportsData.io NBA players API for injury fields."""
import requests, json, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
key = os.getenv("SPORTSDATA_API_KEY", "349bee7aa9394040a2db787f6537b0eb")

data = requests.get(f"https://api.sportsdata.io/v3/nba/scores/json/Players?key={key}", timeout=15).json()
print(f"Total players: {len(data)}")
# Show fields in first player
print("Fields:", list(data[0].keys()))
# Show players with non-null status
injured = [p for p in data if p.get("InjuryStatus") or p.get("Status") not in (None, "Active")]
print(f"\nPlayers with injury/status info: {len(injured)}")
for p in injured[:10]:
    print(f"  {p['FirstName']} {p['LastName']} ({p.get('Team')}) Status={p.get('Status')} InjuryStatus={p.get('InjuryStatus')} InjuryBodyPart={p.get('InjuryBodyPart')} InjuryDescription={p.get('InjuryDescription')}")
