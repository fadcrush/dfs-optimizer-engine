"""
End-to-end pipeline test against DKSalaries.csv

Tests the full chain:
  normalize -> project (GL_L10 + AvgPointsPerGame) -> leverage scores ->
  LP optimizer (5 lineups) -> Monte Carlo (500 sims) -> contest EV ->
  exposure report -> stack report
"""
import sys
import logging
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger("e2e_test")

import pandas as pd
from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.schemas import ProjectionContext
from analysis.core.contest_sim import ContestConfig
from analysis.core.exposure_optimizer import ExposureConfig

SLATE = str(Path(__file__).resolve().parent.parent / "DKSalaries.csv")
print(f"\nSlate: {SLATE}")

# ── Config ────────────────────────────────────────────────────────────────────
context = ProjectionContext(
    sport="NBA",
    site="DK",
    slate_date=date.today(),
    injuries={},           # no manual injuries for this test
)

contest_cfg = ContestConfig(
    site="DK",
    contest_type="gpp",
    entry_fee=3.0,
    field_size=100,
)

exposure_cfg = ExposureConfig(
    global_max=0.80,    # relax cap slightly for small 5-lineup portfolio
    own_sensitivity=1.5,
)

# ── Run ───────────────────────────────────────────────────────────────────────
log.info("Running full pipeline — 5 lineups, 500 sims …")
result = run_dfs_pipeline(
    slate_file_path=SLATE,
    context=context,
    n_lineups=5,
    n_sims=500,
    sim_seed=42,
    correlation="team",
    pre_sim=False,
    apply_filter=True,
    contest_cfg=contest_cfg,
    exposure_cfg=exposure_cfg,
)

assert result["success"], "Pipeline returned success=False"

# ── 1. Projections ────────────────────────────────────────────────────────────
proj_df = result["projections_df"]
print(f"\n{'='*60}")
print(f"PROJECTIONS  ({len(proj_df)} players in pool after filter)")
print(f"{'='*60}")
show = [c for c in ["Name", "Team", "Pos", "Salary", "Proj", "GL_L10",
                     "Leverage", "Floor", "Ceiling", "Value"] if c in proj_df.columns]
print(proj_df[show].sort_values("Proj", ascending=False).head(15).to_string(index=False))

assert "GL_L10" in proj_df.columns,    "GL_L10 column missing from projections"
assert "Leverage" in proj_df.columns, "Leverage column missing from projections"
gl_filled = (proj_df["GL_L10"] > 0).sum()
print(f"\nGL_L10 populated for {gl_filled}/{len(proj_df)} players")

# ── 2. Filter report ─────────────────────────────────────────────────────────
fr = result.get("filter_report", {})
print(f"\n{'='*60}")
print("FILTER REPORT")
print(f"{'='*60}")
for k, v in fr.items():
    print(f"  {k}: {v}")

# ── 3. Lineups ────────────────────────────────────────────────────────────────
lineups_df = result["lineups_df"]
n_lineups = lineups_df["LineupIndex"].nunique() if not lineups_df.empty else 0
print(f"\n{'='*60}")
print(f"LINEUPS  ({n_lineups} generated)")
print(f"{'='*60}")
assert not lineups_df.empty, "No lineups generated"
for li, grp in lineups_df.groupby("LineupIndex"):
    proj_total = round(grp["Proj"].sum(), 1)
    sal_total  = int(grp["Salary"].sum())
    names = ", ".join(grp["Name"].tolist())
    print(f"  Lineup {li+1} | Proj={proj_total} | Sal=${sal_total:,} | {names}")

# ── 4. Simulation + Contest EV ────────────────────────────────────────────────
sim_df = result.get("simulation_df", pd.DataFrame())
print(f"\n{'='*60}")
print("SIMULATION + CONTEST EV (sorted by ROI)")
print(f"{'='*60}")
if not sim_df.empty:
    ev_cols = [c for c in ["LineupIndex", "Mean", "P90", "EV", "ROI",
                            "CashRate", "Top1Rate", "WinRate"] if c in sim_df.columns]
    print(sim_df[ev_cols].to_string(index=False))
    assert "EV" in sim_df.columns,      "EV column missing from simulation"
    assert "ROI" in sim_df.columns,     "ROI column missing from simulation"
    assert "CashRate" in sim_df.columns,"CashRate column missing from simulation"
else:
    print("  (no simulation — n_sims=0)")

# ── 5. Exposure report ────────────────────────────────────────────────────────
exp_df = result.get("exposure_report", pd.DataFrame())
print(f"\n{'='*60}")
print("EXPOSURE REPORT (players actually used)")
print(f"{'='*60}")
if not exp_df.empty:
    used = exp_df[exp_df["ActualCount"] > 0].copy()
    show_cols = [c for c in ["Name", "Team", "Pos", "Proj", "Own",
                              "Leverage", "ActualCount", "ActualPct", "CapPct", "OverCap"]
                 if c in used.columns]
    print(used[show_cols].to_string(index=False))
    assert "ActualPct" in exp_df.columns, "ActualPct missing from exposure report"
else:
    print("  (no exposure report)")

# ── 6. Stack report ───────────────────────────────────────────────────────────
stk_df = result.get("stack_report", pd.DataFrame())
print(f"\n{'='*60}")
print("STACK REPORT")
print(f"{'='*60}")
if not stk_df.empty:
    print(stk_df[stk_df["Stack2"] > 0].to_string(index=False) or "  No 2+ player stacks")
else:
    print("  (no stack report)")

# ── 7. Stats ──────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("PIPELINE STATS")
print(f"{'='*60}")
for k, v in result.get("stats", {}).items():
    print(f"  {k}: {v}")

print(f"\n{'='*60}")
print("ALL ASSERTIONS PASSED — End-to-end pipeline OK")
print(f"{'='*60}\n")
