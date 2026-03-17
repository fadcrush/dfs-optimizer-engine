import argparse
from datetime import date

from analysis.core.orchestrator import run_dfs_pipeline
from analysis.core.schemas import ProjectionContext


def main() -> None:
    parser = argparse.ArgumentParser(description="Run canonical DFS pipeline")
    parser.add_argument("--slate", required=True, help="Path to slate CSV")
    parser.add_argument("--sport", default="NBA", choices=["NBA", "NFL"])
    parser.add_argument("--site", required=True, choices=["FD", "DK"])
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD")
    parser.add_argument("--lineups", type=int, default=0)
    parser.add_argument("--export-dir", default="outputs")
    args = parser.parse_args()

    slate_date = date.fromisoformat(args.date) if args.date else None
    context = ProjectionContext(
        sport=args.sport,
        site=args.site,
        slate_date=slate_date,
    )

    result = run_dfs_pipeline(
        slate_file_path=args.slate,
        context=context,
        n_lineups=args.lineups,
        export_dir=args.export_dir,
    )

    print({
        "success": result.get("success"),
        "site": result.get("site"),
        "sport": result.get("sport"),
        "stats": result.get("stats"),
        "projections_file": result.get("projections_file"),
        "lineups_file": result.get("lineups_file"),
    })


if __name__ == "__main__":
    main()
