"""One-shot script: generate FD projections from tonight's slate CSV."""
import sys
import asyncio
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import traceback as _tb
import asyncio
from services.projection_service import generate_projections

CSV = r"C:\Users\David\Downloads\FanDuel-NBA-2026 EDT-03 EDT-22 EDT-127910-players-list.csv"

try:
    result = asyncio.run(generate_projections(CSV, "dev", site="FD", sport="NBA"))
except Exception as _e:
    print("UNHANDLED EXCEPTION:")
    _tb.print_exc()
    sys.exit(1)

success = result.get("success")
print(f"SUCCESS: {success}")

if success:
    stats = result.get("stats", {})
    print(
        f"Players: {stats['total_players']} | "
        f"Avg Proj: {stats['avg_projection']} | "
        f"Site: {stats['site']}"
    )
    projs = result.get("projections", [])
    print()
    print(f"{'Name':<26} {'Pos':<8} {'Team':<6} {'Salary':>8} {'Proj':>7} {'Floor':>7} {'Ceil':>7} {'Own%':>6}")
    print("-" * 78)
    for p in projs:
        print(
            f"{p['name']:<26} {p['position']:<8} {p['team']:<6} "
            f"${p['salary']:>7,} {p['projection']:>7.2f} "
            f"{p['floor']:>7.2f} {p['ceiling']:>7.2f} {p['ownership']:>5.1f}%"
        )

    # Save full results to JSON
    out_path = Path(__file__).parent / "outputs" / "fd_projections_127910.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nFull results saved to: {out_path}")
else:
    print(f"ERROR: {result.get('error')}")
    print("\nFull response:")
    print(json.dumps(result, indent=2, default=str))
