"""Smoke-test for the updated CanonicalNBAProjectionEngine (L10 game log layer)."""
import logging
import sys
from pathlib import Path

# Ensure project root is on the path when running from scripts/ or project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from analysis.core.projection_engine import (
    CanonicalNBAProjectionEngine,
    _load_game_log_baseline,
)
from analysis.core.schemas import ProjectionContext

# ── 1. Baseline loader ──────────────────────────────────────────────────────
baseline = _load_game_log_baseline("DK", lookback_games=10)
print(f"\nBaseline players loaded: {len(baseline)}")
sample_keys = list(baseline.keys())[:5]
for k in sample_keys:
    b = baseline[k]
    print(f"  {b['name']}: DK={b['dk']:.1f}  FD={b['fd']:.1f}  "
          f"min={b['min']:.1f}  ({b['games']}g)")

# ── 2. Minimal slate — no Base_Proj, no box stats → must use GL_L10 ─────────
slate_no_base = pd.DataFrame([
    {"DFS_ID": "1", "Name": "LeBron James",   "Team": "LAL", "Opp": "GSW",
     "Pos": "SF", "Salary": 9800, "Own": 20.0},
    {"DFS_ID": "2", "Name": "Stephen Curry",  "Team": "GSW", "Opp": "LAL",
     "Pos": "PG", "Salary": 9400, "Own": 22.0},
    {"DFS_ID": "3", "Name": "Nikola Jokic",   "Team": "DEN", "Opp": "PHX",
     "Pos": "C",  "Salary": 10200, "Own": 18.0},
    {"DFS_ID": "4", "Name": "Unknown Player", "Team": "XYZ", "Opp": "ABC",
     "Pos": "PG", "Salary": 3500, "Own": 1.0},
])
ctx = ProjectionContext(sport="NBA", site="DK", slate_date=date.today())
engine = CanonicalNBAProjectionEngine()
result = engine.generate(slate_no_base, ctx)
print("\n-- No Base_Proj (GL_L10 layer) --")
print(result[["Name", "Salary", "Proj", "GL_L10", "Floor", "Ceiling", "Value"]].to_string(index=False))

# ── 3. Slate WITH Base_Proj — should use Base_Proj (Layer 1 wins) ───────────
slate_with_base = slate_no_base.copy()
slate_with_base["Base_Proj"] = [55.0, 48.0, 62.0, 0.0]  # Unknown Player still 0
result2 = engine.generate(slate_with_base, ctx)
print("\n-- With Base_Proj (Layer 1 for manual projections, GL_L10 where 0) --")
print(result2[["Name", "Salary", "Proj", "GL_L10", "Floor", "Ceiling", "Value"]].to_string(index=False))

# ── 4. Assertions ────────────────────────────────────────────────────────────
# LeBron, Curry, Jokic must have GL_L10 > 0 (real players)
assert result.loc[result.Name == "LeBron James", "GL_L10"].values[0] > 0, "LeBron GL_L10 should be > 0"
assert result.loc[result.Name == "Stephen Curry", "GL_L10"].values[0] > 0, "Curry GL_L10 should be > 0"
assert result.loc[result.Name == "Nikola Jokic", "GL_L10"].values[0] > 0, "Jokic GL_L10 should be > 0"
# Unknown player GL_L10 = 0
assert result.loc[result.Name == "Unknown Player", "GL_L10"].values[0] == 0.0, "Unknown GL_L10 should be 0"
# Layer 1: when Base_Proj > 0, Proj == Base_Proj
assert result2.loc[result2.Name == "LeBron James", "Proj"].values[0] == 55.0, "Layer 1 should use Base_Proj"
# Layer 2 for Unknown Player (Base_Proj=0, no game logs) → Proj=0
assert result2.loc[result2.Name == "Unknown Player", "Proj"].values[0] == 0.0, "No-signal player should have Proj=0"

print("\nAll assertions passed.")
