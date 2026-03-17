"""
Quick end-to-end validation of the injury-aware pipeline.
Run: python scripts/test_injury_pipeline.py
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.schemas import ProjectionContext

slate = ROOT / "DKSalaries.csv"
if not slate.exists():
    log.error("DKSalaries.csv not found at %s", slate)
    sys.exit(1)

result = run_dfs_pipeline(
    slate_file_path=str(slate),
    context=ProjectionContext(site="DK", sport="NBA"),
    n_lineups=0,
)

print()
print("=" * 60)
print("  PIPELINE RESULT")
print("=" * 60)
print(f"  success : {result.get('success')}")
print(f"  site    : {result.get('site')}")
print(f"  players after filter: {len(result.get('projections_df', []))}")

fr = result.get("filter_report", {})
print()
print("  FILTER REPORT:")
print(f"    original_count    : {fr.get('original_count')}")
print(f"    filtered_count    : {fr.get('filtered_count')}")
print(f"    removed_count     : {fr.get('removed_count')}")
print(f"    out_count         : {fr.get('out_count')}")
print(f"    injuries_today    : {len(fr.get('injuries_today', []))} players in DB")

print()
detail = result.get("removed_injury_detail", [])
if detail:
    print(f"  REMOVED FROM SLATE ({len(detail)} players):")
    for p in sorted(detail, key=lambda x: (x["status"], x["name"])):
        reason = p["detail"] or "—"
        print(f"    {p['name']:<28}  {p['status']:<14}  {reason}")
else:
    print("  No players removed from slate today (none matched injury records).")

print()
# Show a few projections to confirm GL_L10 is being used (not DK stock)
df = result.get("projections_df", [])
if hasattr(df, "head"):
    cols = [c for c in ["Name", "Proj", "Layer", "Salary"] if c in df.columns]
    print("  SAMPLE PROJECTIONS (first 10):")
    print(df[cols].head(10).to_string(index=False))
print()
