"""Test SportsData.io - check Status field availability."""
import requests, os
from pathlib import Path
from dotenv import load_dotenv
from collections import Counter

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
key = os.getenv("SPORTSDATA_API_KEY", "349bee7aa9394040a2db787f6537b0eb")
data = requests.get(f"https://api.sportsdata.io/v3/nba/scores/json/Players?key={key}", timeout=15).json()

statuses = Counter(p.get("Status") for p in data)
inj_statuses = Counter(p.get("InjuryStatus") for p in data)
print("Status values:", dict(statuses))
print("InjuryStatus values:", dict(inj_statuses))
print()

# Show non-Active or scrambled-but-with-status
special = [p for p in data if p.get("Status") not in ("Active", None)]
print(f"Non-active players ({len(special)}):")
for p in special[:30]:
    print(f"  {p['FirstName']} {p['LastName']} ({p.get('Team')}) Status={p.get('Status')} Inj={p.get('InjuryStatus')} Body={p.get('InjuryBodyPart')}")
