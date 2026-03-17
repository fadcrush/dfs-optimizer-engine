"""Smoke-test for analysis/core/contest_sim.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from analysis.core.contest_sim import (
    ContestConfig,
    PRESETS,
    simulate_contest_ev,
    enrich_simulation_with_ev,
)

# ── 1. Payout schedule sanity checks ────────────────────────────────────────
for name, preset in PRESETS.items():
    schedule = preset.payout_schedule()
    assert len(schedule) > 0, f"Empty schedule for {name}"
    ranks = [r for r, _ in schedule]
    prizes = [p for _, p in schedule]
    assert sorted(ranks) == ranks, f"Schedule not sorted for {name}"
    assert all(p > 0 for p in prizes), f"Non-positive prize in {name}"
    assert prizes[0] >= prizes[-1], f"First prize should be >= last for {name}"
    print(f"  {name}: {len(schedule)} paid spots, 1st=${prizes[0]:.2f}, last=${prizes[-1]:.2f}")

# ── 2. Mock scores matrix (10 lineups × 2000 sims) ──────────────────────────
rng = np.random.default_rng(99)
# Lineup 0 is "elite" (high mean), lineup 9 is "weak" (low mean)
means = np.linspace(310, 250, 10)     # 310 → 250 DK pts
scores = rng.normal(means[:, None], 20, size=(10, 2000))
scores = np.clip(scores, 0, None)
lineup_indices = list(range(10))

# ── 3. GPP small ─────────────────────────────────────────────────────────────
cfg_gpp = ContestConfig(site="DK", contest_type="gpp", entry_fee=3.0, field_size=100)
ev_df = simulate_contest_ev(scores, lineup_indices, cfg_gpp, seed=42)
print("\n-- GPP Small (100-person, $3 entry) --")
print(ev_df[["LineupIndex", "EV", "ROI", "CashRate", "Top1Rate", "AvgFinishPct"]]
      .head(5).to_string(index=False))

# ── 4. Double-up ─────────────────────────────────────────────────────────────
cfg_du = ContestConfig(site="DK", contest_type="double_up", entry_fee=5.0, field_size=100)
ev_du = simulate_contest_ev(scores, lineup_indices, cfg_du, seed=42)
print("\n-- Double Up (100-person, $5 entry) --")
print(ev_du[["LineupIndex", "EV", "ROI", "CashRate"]].head(5).to_string(index=False))

# ── 5. enrich_simulation_with_ev ─────────────────────────────────────────────
sim_summary = pd.DataFrame({
    "LineupIndex": lineup_indices,
    "Mean": scores.mean(axis=1),
    "P90":  np.percentile(scores, 90, axis=1),
    "WinRate": (np.argmax(scores, axis=0) == np.arange(10)[:, None]).mean(axis=1),
})
enriched = enrich_simulation_with_ev(sim_summary, scores, cfg_gpp, seed=42)
print("\n-- Enriched simulation (top 5 by ROI) --")
print(enriched[["LineupIndex", "Mean", "P90", "EV", "ROI", "CashRate"]].head(5).to_string(index=False))

# ── 6. Assertions ─────────────────────────────────────────────────────────────
# Elite lineup (0, highest mean=310) should have higher ROI than weak (9, mean=250)
elite_roi = ev_df.loc[ev_df.LineupIndex == 0, "ROI"].values[0]
weak_roi   = ev_df.loc[ev_df.LineupIndex == 9, "ROI"].values[0]
assert elite_roi > weak_roi, f"Elite lineup ROI ({elite_roi:.3f}) should exceed weak ({weak_roi:.3f})"

# All cash rates between 0 and 1
assert ev_df["CashRate"].between(0, 1).all(), "CashRate out of bounds"
assert ev_df["Top1Rate"].between(0, 1).all(), "Top1Rate out of bounds"
assert ev_df["AvgFinishPct"].between(0, 1).all(), "AvgFinishPct out of bounds"

# Enriched has both simulation and EV columns
assert "EV" in enriched.columns, "EV column missing from enriched"
assert "ROI" in enriched.columns, "ROI column missing from enriched"
assert "Mean" in enriched.columns, "Mean column missing from enriched"

# Double-up should have a higher cash rate than GPP for the elite lineup
cash_gpp = ev_df.loc[ev_df.LineupIndex == 0, "CashRate"].values[0]
cash_du  = ev_du.loc[ev_du.LineupIndex == 0, "CashRate"].values[0]
assert cash_du > cash_gpp, (
    f"Double-up cash rate ({cash_du:.3f}) should exceed GPP ({cash_gpp:.3f})"
)

# ROI is sorted descending
assert list(ev_df["ROI"]) == sorted(ev_df["ROI"], reverse=True), "ev_df not sorted by ROI"

print("\nAll assertions passed.")
