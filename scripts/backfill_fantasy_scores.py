"""
Backfill dk_pts / fd_pts in player_game_logs
=============================================
Recomputes canonical DraftKings and FanDuel fantasy scores from raw stat
columns (points, rebounds, assists, etc.) and writes them back to
``dfs_edge.duckdb::player_game_logs``.

Run once to fix any historical rows where dk_pts/fd_pts were NULL, zero, or
ingested with a wrong formula.  Idempotent — re-running only overwrites rows
where the recomputed value differs from the stored value by > 0.05.

Usage
-----
    cd f:/Dev/N_B_A_and_N_F_L
    python scripts/backfill_fantasy_scores.py              # dry-run
    python scripts/backfill_fantasy_scores.py --write      # write changes
    python scripts/backfill_fantasy_scores.py --write --batch-size 500

Options
-------
--write         Apply updates (default is dry-run — prints what would change).
--batch-size N  Rows per UPDATE batch (default 200).
--db PATH       Override dfs_edge.duckdb path.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=False)

from backend.services.fantasy_scoring import score_game_row  # noqa: E402

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

_DEFAULT_DB = _REPO_ROOT / "data" / "dfs_edge.duckdb"

_FETCH_SQL = """
SELECT
    rowid,
    player_name,
    game_date,
    COALESCE(points, 0)        AS points,
    COALESCE(three_pointers, 0) AS three_pointers,
    COALESCE(rebounds, 0)      AS rebounds,
    COALESCE(assists, 0)       AS assists,
    COALESCE(steals, 0)        AS steals,
    COALESCE(blocks, 0)        AS blocks,
    COALESCE(turnovers, 0)     AS turnovers,
    COALESCE(fg_made, 0)       AS fg_made,
    COALESCE(ft_made, 0)       AS ft_made,
    COALESCE(dk_pts, 0)        AS dk_pts,
    COALESCE(fd_pts, 0)        AS fd_pts,
    COALESCE(minutes, 0)       AS minutes
FROM player_game_logs
WHERE minutes > 0
"""


def backfill(db_path: Path, write: bool, batch_size: int) -> None:
    import duckdb

    if not db_path.exists():
        log.error("dfs_edge.duckdb not found at %s", db_path)
        sys.exit(1)

    con = duckdb.connect(str(db_path), read_only=False)

    log.info("Fetching rows from player_game_logs (%s)…", db_path)
    rows = con.execute(_FETCH_SQL).fetchall()
    col_names = [
        "rowid", "player_name", "game_date",
        "points", "three_pointers", "rebounds", "assists",
        "steals", "blocks", "turnovers", "fg_made", "ft_made",
        "dk_pts", "fd_pts", "minutes",
    ]

    log.info("Loaded %d rows.  Recomputing…", len(rows))

    updates: list[tuple[float, float, int]] = []  # (new_dk, new_fd, rowid)
    drifted_dk = drifted_fd = nulls_fixed = 0

    for raw in rows:
        r = dict(zip(col_names, raw))
        calc_dk, calc_fd = score_game_row(r)
        stored_dk = float(r["dk_pts"])
        stored_fd = float(r["fd_pts"])

        dk_changed = abs(stored_dk - calc_dk) > 0.05
        fd_changed = abs(stored_fd - calc_fd) > 0.05

        if stored_dk == 0.0:
            nulls_fixed += 1
        elif dk_changed:
            drifted_dk += 1

        if stored_fd == 0.0:
            pass  # counted in nulls_fixed above (same row)
        elif fd_changed:
            drifted_fd += 1

        if dk_changed or fd_changed:
            updates.append((calc_dk, calc_fd, int(r["rowid"])))

    total = len(rows)
    log.info(
        "Summary: %d rows  |  %d null/zero fixed  |  %d dk drifted  |  %d fd drifted  |  %d total to update",
        total, nulls_fixed, drifted_dk, drifted_fd, len(updates),
    )

    if not updates:
        log.info("Nothing to update — all stored values match canonical formulas.")
        con.close()
        return

    if not write:
        log.info("DRY RUN — pass --write to apply %d updates", len(updates))
        # Print first 10 sample changes
        sample = updates[:10]
        for new_dk, new_fd, rowid in sample:
            log.info("  rowid=%d  new_dk=%.2f  new_fd=%.2f", rowid, new_dk, new_fd)
        con.close()
        return

    log.info("Writing %d updates in batches of %d…", len(updates), batch_size)
    written = 0
    for i in range(0, len(updates), batch_size):
        batch = updates[i : i + batch_size]
        for new_dk, new_fd, rowid in batch:
            con.execute(
                "UPDATE player_game_logs SET dk_pts = ?, fd_pts = ? WHERE rowid = ?",
                [new_dk, new_fd, rowid],
            )
        written += len(batch)
        log.info("  %d / %d rows updated", written, len(updates))

    con.close()
    log.info("Backfill complete.  %d rows updated.", len(updates))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill dk_pts/fd_pts from raw stats")
    parser.add_argument("--write", action="store_true", help="Apply updates (default: dry-run)")
    parser.add_argument("--batch-size", type=int, default=200, dest="batch_size")
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB)
    args = parser.parse_args()
    backfill(db_path=args.db, write=args.write, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
