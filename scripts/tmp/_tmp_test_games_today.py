"""Test SportsData.io GamesByDate for injury detection."""
import requests, json, os
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
key = os.getenv("SPORTSDATA_API_KEY", "349bee7aa9394040a2db787f6537b0eb")
today = date.today().strftime("%Y-%m-%d")

data = requests.get(
    f"https://api.sportsdata.io/v3/nba/scores/json/GamesByDate/{today}?key={key}",
    timeout=15
).json()

print(f"Games today ({today}): {len(data) if isinstance(data, list) else 'error'}")
if isinstance(data, list) and data:
    print("Game keys:", list(data[0].keys()))
    print(json.dumps(data[0], indent=2)[:2000])
else:
    print(data)
