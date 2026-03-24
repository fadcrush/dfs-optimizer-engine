"""
seed_ownership_history.py
=========================
Read all DraftKings and FanDuel salary/player-pool CSVs from ``Player slates/``
and write synthetic ownership estimates into ``data/ownership_history.duckdb``.

Synthetic ownership is derived from player FPPG using a temperature-scaled
softmax that mimics realistic DFS ownership distributions (same algorithm as
``analytics_service.synthesize_ownership_from_slate``).

Usage
-----
    python scripts/seed_ownership_history.py [--force] [--dry-run]

Options
-------
    --force     Replace every row, even if it already exists (re-seed).
    --dry-run   Print what would be inserted without writing anything.
    --slates    Path to the slates folder (default: ``Player slates/``).
    --db        Path to the DuckDB file (default: ``data/ownership_history.duckdb``).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SLATES_DIR = ROOT / "Player slates"
DEFAULT_DB = ROOT / "data" / "ownership_history.duckdb"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ownership_history (
    player_name    TEXT,
    game_date      DATE,
    site           TEXT,
    slate_id       TEXT,
    actual_own_pct DOUBLE,
    proj_at_lock   DOUBLE,
    salary         INTEGER,
    team_total     DOUBLE,
    is_home        BOOLEAN,
    contest_type   TEXT,
    own_source     TEXT,
    PRIMARY KEY (player_name, game_date, site, slate_id)
)
"""

UPSERT_SQL = """
INSERT INTO ownership_history
    (player_name, game_date, site, slate_id, actual_own_pct,
     proj_at_lock, salary, team_total, is_home, contest_type, own_source)
VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
ON CONFLICT (player_name, game_date, site, slate_id)
DO UPDATE SET
    actual_own_pct = CASE
        WHEN excluded.own_source = 'real' THEN excluded.actual_own_pct
        WHEN ownership_history.own_source = 'real' THEN ownership_history.actual_own_pct
        ELSE excluded.actual_own_pct
    END,
    proj_at_lock = COALESCE(ownership_history.proj_at_lock, excluded.proj_at_lock),
    salary       = COALESCE(ownership_history.salary, excluded.salary),
    own_source   = CASE
        WHEN excluded.own_source = 'real' THEN 'real'
        ELSE ownership_history.own_source
    END
"""

REPLACE_SQL = """
INSERT OR REPLACE INTO ownership_history
    (player_name, game_date, site, slate_id, actual_own_pct,
     proj_at_lock, salary, team_total, is_home, contest_type, own_source)
VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
"""


def _softmax_ownership(
    fppg_arr: np.ndarray, contest_type: str = "gpp"
) -> np.ndarray:
    """Temperature-scaled softmax → realistic ownership distribution."""
    fppg_std = float(np.std(fppg_arr)) or 1.0
    temperature = fppg_std * 2.5 if contest_type in ("gpp", "winner_take_all") else fppg_std * 4.0
    logits = fppg_arr / temperature
    logits -= logits.max()
    exp_l = np.exp(logits)
    probs = exp_l / exp_l.sum()

    own_min = 0.5 if contest_type in ("gpp", "winner_take_all") else 3.0
    own_max = 58.0 if contest_type in ("gpp", "winner_take_all") else 38.0
    p_min, p_max = probs.min(), probs.max()
    if p_max > p_min:
        return (own_min + (probs - p_min) / (p_max - p_min) * (own_max - own_min)).round(1)
    return np.full(len(probs), round((own_min + own_max) / 2, 1))


def _parse_dk_slate(path: Path) -> pd.DataFrame | None:
    """
    Read a DraftKings salary CSV and return a normalised DataFrame.

    Expected columns: Name, Salary, AvgPointsPerGame, Game Info, Position
    Game Info example: ``CLE@MIL 02/25/2026 08:00PM ET``
    """
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        log.warning("DK — cannot read %s: %s", path.name, exc)
        return None

    cols = {c.strip().lower(): c for c in df.columns}

    name_col = next(
        (cols[k] for k in ("name", "nickname") if k in cols), None
    )
    fppg_col = next(
        (cols[k] for k in ("avgpointspergame", "avg points per game", "fppg") if k in cols), None
    )
    salary_col = cols.get("salary")
    game_col = next((cols[k] for k in ("game info", "gameinfo") if k in cols), None)
    pos_col = next((cols[k] for k in ("position", "roster position") if k in cols), None)

    if not name_col or not fppg_col:
        log.warning("DK — missing name/fppg columns in %s: %s", path.name, list(df.columns))
        return None

    # Parse date from Game Info column (first non-null row)
    game_date: date | None = None
    if game_col:
        for val in df[game_col].dropna().astype(str):
            m = re.search(r"(\d{2}/\d{2}/\d{4})", val)
            if m:
                try:
                    game_date = pd.to_datetime(m.group(1), format="%m/%d/%Y").date()
                    break
                except ValueError:
                    pass

    if game_date is None:
        log.warning("DK — cannot parse date from Game Info in %s, skipping", path.name)
        return None

    work = df[[name_col, fppg_col] +
               ([salary_col] if salary_col else []) +
               ([pos_col] if pos_col else [])].copy()
    work.columns = (
        ["name", "fppg"]
        + (["salary"] if salary_col else [])
        + (["position"] if pos_col else [])
    )
    work["fppg"] = pd.to_numeric(work["fppg"], errors="coerce")
    work = work.dropna(subset=["fppg"])
    work = work[work["fppg"] > 0].reset_index(drop=True)

    if work.empty:
        return None

    work["game_date"] = game_date
    work["site"] = "DK"
    work["slate_id"] = path.stem
    work["own_source"] = "synthetic"
    work["contest_type"] = "gpp"
    work["actual_own_pct"] = _softmax_ownership(work["fppg"].to_numpy())

    return work


def _parse_fd_slate(path: Path) -> pd.DataFrame | None:
    """
    Read a FanDuel player-list CSV and return a normalised DataFrame.

    Filename example: ``FanDuel-NBA-2026 EST-01 EST-11 EST-125454-players-list.csv``
    Date is encoded as ``EST-{MM} EST-{DD}`` in the filename.

    Expected columns: Nickname (or First Name + Last Name), Salary, FPPG, Game
    """
    # Only import NBA slates, skip NFL
    if "NFL" in path.name.upper():
        log.debug("FD — skipping NFL slate %s", path.name)
        return None

    # Parse date from filename: FanDuel-NBA-{YYYY} EST-{MM} EST-{DD} EST-...
    m = re.search(r"(\d{4})\s+EST-(\d{2})\s+EST-(\d{2})", path.name)
    if m:
        try:
            game_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            game_date = None
    else:
        game_date = None

    if game_date is None:
        log.warning("FD — cannot parse date from filename %s, skipping", path.name)
        return None

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        log.warning("FD — cannot read %s: %s", path.name, exc)
        return None

    cols = {c.strip().lower(): c for c in df.columns}

    fppg_col = next((cols[k] for k in ("fppg", "avgfppg") if k in cols), None)
    salary_col = cols.get("salary")
    pos_col = next((cols[k] for k in ("position", "roster position") if k in cols), None)

    # Name: prefer Nickname, fall back to First Name + Last Name
    if "nickname" in cols:
        df["__name__"] = df[cols["nickname"]].astype(str).str.strip()
    elif "first name" in cols and "last name" in cols:
        df["__name__"] = (
            df[cols["first name"]].astype(str).str.strip()
            + " "
            + df[cols["last name"]].astype(str).str.strip()
        ).str.strip()
    elif "name" in cols:
        df["__name__"] = df[cols["name"]].astype(str).str.strip()
    else:
        log.warning("FD — no name column in %s: %s", path.name, list(df.columns))
        return None

    if not fppg_col:
        log.warning("FD — no FPPG column in %s: %s", path.name, list(df.columns))
        return None

    work = df[["__name__", fppg_col] +
               ([salary_col] if salary_col else []) +
               ([pos_col] if pos_col else [])].copy()
    work.columns = (
        ["name", "fppg"]
        + (["salary"] if salary_col else [])
        + (["position"] if pos_col else [])
    )
    work["fppg"] = pd.to_numeric(work["fppg"], errors="coerce")
    work = work.dropna(subset=["fppg"])
    work = work[work["fppg"] > 0].reset_index(drop=True)

    if work.empty:
        return None

    work["game_date"] = game_date
    work["site"] = "FD"
    work["slate_id"] = path.stem
    work["own_source"] = "synthetic"
    work["contest_type"] = "gpp"
    work["actual_own_pct"] = _softmax_ownership(work["fppg"].to_numpy())

    return work


def _write_to_db(
    con: duckdb.DuckDBPyConnection,
    frames: list[pd.DataFrame],
    force: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """Insert all rows, skip duplicates (or replace all if ``force``).
    Returns (inserted, skipped)."""
    sql = REPLACE_SQL if force else UPSERT_SQL
    inserted = 0
    skipped = 0

    for df in frames:
        for _, r in df.iterrows():
            try:
                if not dry_run:
                    con.execute(
                        sql,
                        [
                            str(r["name"]),
                            r["game_date"],
                            r["site"],
                            r["slate_id"],
                            float(r["actual_own_pct"]),
                            float(r["fppg"]) if pd.notna(r.get("fppg")) else None,
                            int(r["salary"]) if "salary" in r and pd.notna(r.get("salary")) else None,
                            r["contest_type"],
                            r["own_source"],
                        ],
                    )
                inserted += 1
            except Exception as exc:
                log.debug("Row skip (%s %s): %s", r.get("site"), r.get("name"), exc)
                skipped += 1

    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ownership_history.duckdb from Player slates/")
    parser.add_argument("--force", action="store_true", help="Re-seed even existing rows")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--slates", default=str(DEFAULT_SLATES_DIR), help="Slate folder path")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="DuckDB file path")
    args = parser.parse_args()

    slates_dir = Path(args.slates)
    db_path = Path(args.db)

    if not slates_dir.exists():
        log.error("Slates folder not found: %s", slates_dir)
        sys.exit(1)

    # ── Collect all slate CSVs ──────────────────────────────────────────────
    frames: list[pd.DataFrame] = []

    dk_files = sorted(slates_dir.glob("DKSalaries*.csv"))
    fd_files = sorted(slates_dir.glob("FanDuel*.csv"))

    log.info("Found %d DK and %d FD slate files in %s", len(dk_files), len(fd_files), slates_dir)

    for fp in dk_files:
        df = _parse_dk_slate(fp)
        if df is not None:
            frames.append(df)
            log.info("DK  %s → %d players (%s)", fp.name, len(df), df["game_date"].iloc[0])

    for fp in fd_files:
        df = _parse_fd_slate(fp)
        if df is not None:
            frames.append(df)
            log.info("FD  %s → %d players (%s)", fp.name, len(df), df["game_date"].iloc[0])

    if not frames:
        log.warning("No parseable slate files found — nothing to seed.")
        sys.exit(0)

    total_rows = sum(len(f) for f in frames)
    unique_slates = len(frames)
    log.info("Total: %d rows across %d slates", total_rows, unique_slates)

    if args.dry_run:
        log.info("DRY RUN — no data written.")
        # Print per-slate summary
        for df in frames:
            log.info(
                "  %-60s  %3d players  date=%s  site=%s",
                df["slate_id"].iloc[0],
                len(df),
                df["game_date"].iloc[0],
                df["site"].iloc[0],
            )
        sys.exit(0)

    # ── Connect and seed ────────────────────────────────────────────────────
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(CREATE_SQL)

    before = con.execute("SELECT COUNT(1) FROM ownership_history").fetchone()[0]
    inserted, skipped = _write_to_db(con, frames, force=args.force, dry_run=False)
    after = con.execute("SELECT COUNT(1) FROM ownership_history").fetchone()[0]
    con.close()

    log.info(
        "Seeding complete — rows before: %d, after: %d  (inserted/updated: %d, skipped: %d)",
        before, after, inserted, skipped,
    )


if __name__ == "__main__":
    main()
