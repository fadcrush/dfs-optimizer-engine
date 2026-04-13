import requests, json, sys

resp = requests.post(
    "http://localhost:8000/api/optimizer/run",
    params={"site": "DK", "sport": "NBA", "n_lineups": 5},
    files={"file": ("DKSalaries.csv", open(r"backend\DKSalaries (62).csv", "rb"), "text/csv")},
)
d = resp.json()

print("=== STATUS ===")
print(f"success={d.get('success')}  total_lineups={d.get('total_lineups')}")
print()

fr = d.get("filter_report", {})
print("=== FILTER REPORT ===")
print(f"original={fr.get('original_count')}  filtered={fr.get('filtered_count')}  removed={fr.get('removed_count')}")
print(f"out_count={fr.get('out_count')}  discounted_count={fr.get('discounted_count')}")
print(f"removed_injury_detail ({len(fr.get('removed_injury_detail', []))}): {fr.get('removed_injury_detail', [])}")
print(f"injuries_today ({len(fr.get('injuries_today', []))}): {fr.get('injuries_today', [])[:5]}...")
print(f"discounted_players: {fr.get('discounted_players', [])}")
print()

print("=== LINEUPS ===")
for lu in d.get("lineups", []):
    print(f"#{lu['lineup_num']}  sal={lu['total_salary']}  proj={lu['projected_points']:.1f}  own={lu['total_ownership']:.1f}")
    slots = {k: v for k, v in lu.items() if k not in ("lineup_num", "total_salary", "projected_points", "total_ownership")}
    for pos, name in slots.items():
        print(f"    {pos:4s}: {name}")
print()

print("=== STATS ===")
print(json.dumps(d.get("stats"), indent=2))

print()
print("=== REPLACEMENT BOOSTS ===")
rb = d.get("replacement_boosts", [])
print(f"count={len(rb)}")
for b in rb[:10]:
    print(f"  {b}")
