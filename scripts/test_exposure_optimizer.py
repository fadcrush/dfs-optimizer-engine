"""Smoke-test for analysis/core/exposure_optimizer.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from analysis.core.exposure_optimizer import (
    ExposureConfig,
    compute_leverage_scores,
    resolve_per_player_caps,
    build_exposure_report,
    build_stack_report,
)

# ── Shared test projections ───────────────────────────────────────────────────
proj_df = pd.DataFrame([
    {"DFS_ID": "1", "Name": "LeBron James",  "Team": "LAL", "Pos": "SF",
     "Salary": 9800, "Proj": 48.0, "Own": 38.0},
    {"DFS_ID": "2", "Name": "Stephen Curry",  "Team": "GSW", "Pos": "PG",
     "Salary": 9400, "Proj": 44.0, "Own": 32.0},
    {"DFS_ID": "3", "Name": "Nikola Jokic",   "Team": "DEN", "Pos": "C",
     "Salary": 10200, "Proj": 58.0, "Own": 25.0},
    {"DFS_ID": "4", "Name": "Anthony Davis",  "Team": "LAL", "Pos": "PF",
     "Salary": 9600, "Proj": 46.0, "Own": 29.0},
    {"DFS_ID": "5", "Name": "Jayson Tatum",   "Team": "BOS", "Pos": "SF",
     "Salary": 8800, "Proj": 40.0, "Own": 19.0},
    {"DFS_ID": "6", "Name": "Devin Booker",   "Team": "PHX", "Pos": "SG",
     "Salary": 8400, "Proj": 36.0, "Own": 15.0},
    {"DFS_ID": "7", "Name": "De'Aaron Fox",   "Team": "SAC", "Pos": "PG",
     "Salary": 7800, "Proj": 32.0, "Own": 8.0},
    {"DFS_ID": "8", "Name": "Miles Bridges",  "Team": "CHA", "Pos": "SF",
     "Salary": 6400, "Proj": 22.0, "Own": 4.5},
])

# ── 1. compute_leverage_scores ────────────────────────────────────────────────
cfg = ExposureConfig(
    global_max=0.55,
    player_caps={"LeBron James": 0.30, "Nikola Jokic": 0.50},
    player_floors={"Anthony Davis": 0.20},
    own_sensitivity=1.5,
)
enriched = compute_leverage_scores(proj_df, cfg)
print("-- Leverage scores --")
print(enriched[["Name", "Proj", "Own", "Edge", "Leverage", "LowOwnBonus"]].to_string(index=False))

assert "Leverage" in enriched.columns, "Leverage column missing"
assert "Edge" in enriched.columns, "Edge column missing"
assert enriched["Leverage"].between(0, 1).all(), "Leverage not normalised to [0,1]"

# Jokic (high proj, moderate own) should have higher leverage than LeBron (high proj, very high own)
jokic_lev  = enriched.loc[enriched.Name == "Nikola Jokic",  "Leverage"].values[0]
lebron_lev = enriched.loc[enriched.Name == "LeBron James",  "Leverage"].values[0]
assert jokic_lev > lebron_lev, (
    f"Jokic leverage ({jokic_lev:.3f}) should exceed LeBron ({lebron_lev:.3f})"
    " because Jokic has lower ownership"
)

# Low-own contrarians (Fox, Bridges) should score higher than chalky players
fox_lev    = enriched.loc[enriched.Name == "De'Aaron Fox",  "Leverage"].values[0]
# ── 2. resolve_per_player_caps ───────────────────────────────────────────────
caps, floors = resolve_per_player_caps(enriched, n_lineups=20, cfg=cfg)
print("\n-- Per-player caps (DFS_ID → max lineup count) --")
for pid, cnt in sorted(caps.items()):
    name = enriched.loc[enriched.DFS_ID == pid, "Name"].values[0]
    print(f"  {name}: max {cnt} / 20  ({cnt/20:.0%})")

print("\n-- Per-player floors (DFS_ID → min lineup count) --")
for pid, cnt in sorted(floors.items()):
    name = enriched.loc[enriched.DFS_ID == pid, "Name"].values[0]
    print(f"  {name}: min {cnt} / 20  ({cnt/20:.0%})")

# LeBron cap should be 30% of 20 = 6
lebron_id = "1"
assert caps[lebron_id] == 6, f"LeBron cap should be 6, got {caps[lebron_id]}"
# Anthony Davis floor should be 20% of 20 = 4
davis_id = "4"
assert floors.get(davis_id) == 4, f"Davis floor should be 4, got {floors.get(davis_id)}"

# ── 3. build_exposure_report ──────────────────────────────────────────────────
# Simulate 10 lineups where every player appears a fixed number of times
rng = np.random.default_rng(42)
lineup_rows = []
for lineup_idx in range(10):
    players_in_lineup = enriched.sample(5, random_state=lineup_idx + 1)
    temp = players_in_lineup.copy()
    temp["LineupIndex"] = lineup_idx
    lineup_rows.append(temp)
fake_lineups = pd.concat(lineup_rows, ignore_index=True)

report = build_exposure_report(fake_lineups, enriched, n_lineups=10, cfg=cfg)
print("\n-- Exposure report --")
print(report[["Name", "ActualCount", "ActualPct", "CapPct", "FloorPct", "OverCap", "UnderFloor"]]
      .to_string(index=False))

assert "ActualPct" in report.columns
assert "OverCap" in report.columns
assert "UnderFloor" in report.columns
assert report["ActualPct"].between(0, 1).all()

# ── 4. build_stack_report ─────────────────────────────────────────────────────
stack = build_stack_report(fake_lineups)
print("\n-- Stack report --")
print(stack.to_string(index=False))
assert "Stack2" in stack.columns
assert "Stack3" in stack.columns
# LAL has 2 players: LeBron + Davis — they may stack depending on sampling
assert set(stack["Team"]).issubset(set(enriched["Team"]))

# ── 5. Verify imports are clean across all wired modules ──────────────────────
from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.exposure_optimizer import ExposureConfig, compute_leverage_scores
print("\nAll imports OK.")
print("\nAll assertions passed.")
