"""
seed_game_logs.py
=================
Reads data/player_game_logs_2025-26.csv and seeds the ``player_game_logs``
table in data/dfs_edge.duckdb.

DK scoring  : pts + 0.5*3pm + 1.25*reb + 1.5*ast + 2*stl + 2*blk
              - 0.5*tov  + 1.5 DD bonus + 3.0 TD bonus
FD scoring  : 2*fgm + 1*3pm + 1*ftm + 1.2*reb + 1.5*ast + 3*stl
              + 3*blk - 1*tov

Usage
-----
    python scripts/seed_game_logs.py [--force]

    --force  Drop and recreate the table even if it already has rows.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "player_game_logs_2025-26.csv"
# game_logs.duckdb is the dedicated read/write file — avoids contention with
# dfs_edge.duckdb which may be held open by VS Code or the running backend.
DB_PATH  = ROOT / "data" / "game_logs.duckdb"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS player_game_logs (
    player_name TEXT,
    game_date   DATE,
    minutes     DOUBLE,
    dk_pts      DOUBLE,
    fd_pts      DOUBLE,
    team        TEXT,
    opponent    TEXT
)
"""


def _compute_dk(row: pd.Series) -> float:
    pts = float(row["points"])
    tpm = float(row["three_pm"])
    reb = float(row["rebounds"])
    ast = float(row["assists"])
    stl = float(row["steals"])
    blk = float(row["blocks"])
    tov = float(row["turnovers"])

    score = pts + 0.5 * tpm + 1.25 * reb + 1.5 * ast + 2.0 * stl + 2.0 * blk - 0.5 * tov

    # Double/Triple-Double bonus (Points, Rebounds, Assists, Steals, Blocks)
    n_doubles = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)
    if n_doubles >= 2:
        score += 1.5
    if n_doubles >= 3:
        score += 3.0

    return round(score, 4)


def _compute_fd(row: pd.Series) -> float:
    fgm = float(row["fgm"])
    tpm = float(row["three_pm"])
    ftm = float(row["ftm"])
    reb = float(row["rebounds"])
    ast = float(row["assists"])
    stl = float(row["steals"])
    blk = float(row["blocks"])
    tov = float(row["turnovers"])

    score = (2.0 * fgm + 1.0 * tpm + 1.0 * ftm
             + 1.2 * reb + 1.5 * ast + 3.0 * stl + 3.0 * blk
             - 1.0 * tov)
    return round(score, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed player_game_logs into dfs_edge.duckdb")
    parser.add_argument("--force", action="store_true",
                        help="Drop and recreate the table even if rows already exist")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {CSV_PATH} …")
    df = pd.read_csv(CSV_PATH, encoding="utf-8")
    print(f"  {len(df):,} rows, {len(df.columns)} columns")

    # Compute DFS scores
    print("Computing dk_pts and fd_pts …")
    df["dk_pts"] = df.apply(_compute_dk, axis=1)
    df["fd_pts"] = df.apply(_compute_fd, axis=1)

    # Select and rename to match schema
    seed = df[["player_name", "game_date", "minutes",
               "dk_pts", "fd_pts", "team_abbr", "opponent_abbr"]].copy()
    seed = seed.rename(columns={"team_abbr": "team", "opponent_abbr": "opponent"})
    seed["game_date"] = pd.to_datetime(seed["game_date"]).dt.date
    seed["minutes"] = seed["minutes"].astype(float)

    print(f"\nConnecting to {DB_PATH} …")
    con = None
    import time
    for attempt in range(1, 13):  # up to 60 s
        try:
            con = duckdb.connect(str(DB_PATH))
            break
        except duckdb.IOException as exc:
            if attempt == 1:
                print(f"  File locked — retrying every 5 s (up to 60 s). Close any DuckDB "
                      f"viewers / extensions that may have the file open.")
            print(f"  Attempt {attempt}/12 … {exc}")
            time.sleep(5)
    if con is None:
        print("ERROR: Could not acquire lock on dfs_edge.duckdb after 60 s.", file=sys.stderr)
        sys.exit(1)

    try:
        # Check existing row count
        try:
            existing = con.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]
        except duckdb.CatalogException:
            existing = 0

        if existing > 0 and not args.force:
            print(f"  player_game_logs already has {existing:,} rows.")
            print("  Use --force to drop and re-seed.  Exiting without changes.")
            return

        if existing > 0 and args.force:
            print(f"  --force set: dropping {existing:,} existing rows …")
            con.execute("DROP TABLE player_game_logs")

        con.execute(CREATE_SQL)
        con.execute("INSERT INTO player_game_logs SELECT * FROM seed")
        inserted = con.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]

        # Quick sanity check
        sample = con.execute("""
            SELECT player_name, game_date, minutes, dk_pts, fd_pts, team, opponent
            FROM player_game_logs
            ORDER BY game_date DESC
            LIMIT 3
        """).fetchall()

    finally:
        con.close()

    print(f"\n  Inserted {inserted:,} rows into player_game_logs")
    print("\nSample (3 most-recent game dates):")
    print(f"  {'Name':<25} {'Date':<12} {'Min':>5}  {'DK':>7}  {'FD':>7}  {'Team':<5}  Opp")
    print(f"  {'-'*25} {'-'*12} {'-'*5}  {'-'*7}  {'-'*7}  {'-'*5}  ---")
    for name, gd, mins, dk, fd, team, opp in sample:
        print(f"  {name:<25} {str(gd):<12} {mins:>5.1f}  {dk:>7.2f}  {fd:>7.2f}  {team:<5}  {opp}")

    print("\nDone.")


if __name__ == "__main__":
    main()
