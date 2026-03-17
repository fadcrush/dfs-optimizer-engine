"""
Contest Result Import
======================
Parses FanDuel and DraftKings contest CSV exports and inserts results
into data/contest_results.duckdb.

Usage
-----
    python scripts/import_contest_results.py path/to/contest.csv [path/to/another.csv ...]

    # Or from Python:
    from scripts.import_contest_results import import_contest_file
    result = import_contest_file("my_contest.csv")
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "contest_results.duckdb"
PROCESSED_DIR = ROOT / "data" / "uploads" / "contests" / "processed"

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# ── Schema ────────────────────────────────────────────────────────────────────

@dataclass
class ContestEntry:
    entry_id: str
    site: str
    sport: str
    game_date: Optional[date]
    contest_name: str
    contest_type: str          # gpp | cash | showdown | qualifier
    entry_fee: float
    total_entries: int
    final_rank: Optional[int]
    payout: float
    projected_points: float
    actual_points: float
    lineup: list[str] = field(default_factory=list)   # player names


@dataclass
class ImportResult:
    filepath: str
    site: str
    imported: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


# ── DB init ───────────────────────────────────────────────────────────────────

def _init_db() -> None:
    """Ensure contest_results.duckdb and its tables exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import duckdb
    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS contest_entries (
            entry_id         VARCHAR  NOT NULL,
            site             VARCHAR  NOT NULL,
            sport            VARCHAR  DEFAULT 'NBA',
            game_date        DATE     DEFAULT NULL,
            contest_name     VARCHAR  DEFAULT '',
            contest_type     VARCHAR  DEFAULT 'gpp',
            entry_fee        FLOAT    DEFAULT 0,
            total_entries    INTEGER  DEFAULT 0,
            final_rank       INTEGER  DEFAULT NULL,
            payout           FLOAT    DEFAULT 0,
            projected_points FLOAT    DEFAULT 0,
            actual_points    FLOAT    DEFAULT 0,
            lineup           VARCHAR  DEFAULT '[]',
            imported_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entry_id, site)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS contest_player_ownership (
            game_date     DATE    NOT NULL,
            site          VARCHAR NOT NULL,
            player_name   VARCHAR NOT NULL,
            actual_own_pct FLOAT  DEFAULT NULL,
            contest_type  VARCHAR DEFAULT 'gpp',
            PRIMARY KEY (game_date, site, player_name, contest_type)
        )
    """)
    con.close()


# ── Contest type detection ────────────────────────────────────────────────────

def _infer_contest_type(name: str) -> str:
    n = name.lower()
    if any(w in n for w in ["showdown", "captain", "cptn", "single game"]):
        return "showdown"
    if any(w in n for w in ["qualifier", "satellite", "ticket"]):
        return "qualifier"
    if any(w in n for w in ["double up", "50/50", "head to head", "h2h", "cash", "multiplier"]):
        return "cash"
    return "gpp"


# ── FanDuel CSV parser ────────────────────────────────────────────────────────

def _parse_fd_contest(filepath: str) -> tuple[list[ContestEntry], dict[str, float]]:
    """
    Parse a FanDuel contest CSV export.
    Returns (entries, ownership_map {player_name: own_pct}).
    """
    df = pd.read_csv(filepath)
    df.columns = [c.strip() for c in df.columns]
    entries: list[ContestEntry] = []
    ownership: dict[str, float] = {}

    for _, row in df.iterrows():
        try:
            contest_name = str(row.get("Contest Name", row.get("Contest_Name", "")))
            entry_id = str(row.get("Entry ID", row.get("Entry_ID", f"fd_{_}_{id(row)}")))
            sport = str(row.get("Sport", "NBA")).upper()
            entry_fee_raw = row.get("Entry Fee", row.get("Entry_Fee", 0))
            entry_fee = float(str(entry_fee_raw).replace("$", "").replace(",", "") or 0)
            total_entries = int(row.get("Total Entries", row.get("Entries", 0)) or 0)
            final_rank_raw = row.get("Rank", row.get("Final Rank", None))
            final_rank = int(final_rank_raw) if pd.notna(final_rank_raw) else None
            payout_raw = row.get("Winnings", row.get("Payout", 0))
            payout = float(str(payout_raw).replace("$", "").replace(",", "") or 0)
            actual_pts = float(row.get("FPTS", row.get("Actual Points", 0)) or 0)
            projected_pts = float(row.get("Projected Points", row.get("Proj", 0)) or 0)

            # Roster: pipe-separated player names
            roster_raw = str(row.get("Roster", row.get("Lineup", "")))
            lineup = [p.strip() for p in roster_raw.split("|") if p.strip()]

            # Date parsing
            date_raw = row.get("Date", row.get("Contest Date", ""))
            game_date: Optional[date] = None
            if isinstance(date_raw, str) and date_raw:
                try:
                    game_date = datetime.strptime(date_raw[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    try:
                        game_date = datetime.strptime(date_raw[:10], "%m/%d/%Y").date()
                    except (ValueError, TypeError):
                        pass

            entries.append(ContestEntry(
                entry_id=entry_id,
                site="FD",
                sport=sport,
                game_date=game_date,
                contest_name=contest_name,
                contest_type=_infer_contest_type(contest_name),
                entry_fee=entry_fee,
                total_entries=total_entries,
                final_rank=final_rank,
                payout=payout,
                projected_points=projected_pts,
                actual_points=actual_pts,
                lineup=lineup,
            ))
        except Exception as exc:
            log.warning("Skipping FD row %d: %s", _, exc)

    # Ownership column if present
    own_col = next((c for c in df.columns if "ownership" in c.lower() or "%" in c), None)
    if own_col:
        name_col = next((c for c in ["Player", "Name"] if c in df.columns), None)
        if name_col:
            for _, row in df.iterrows():
                pname = str(row.get(name_col, "")).strip()
                own_pct = float(row.get(own_col, 0) or 0)
                if pname and own_pct > 0:
                    ownership[pname] = own_pct

    return entries, ownership


# ── DraftKings CSV parser ─────────────────────────────────────────────────────

def _parse_dk_contest(filepath: str) -> tuple[list[ContestEntry], dict[str, float]]:
    """
    Parse a DraftKings contest CSV export.
    Returns (entries, ownership_map).
    """
    df = pd.read_csv(filepath)
    df.columns = [c.strip() for c in df.columns]
    entries: list[ContestEntry] = []
    ownership: dict[str, float] = {}

    # DK format: one row per player per entry — group by entry
    entry_col = next((c for c in ["EntryId", "Entry_ID", "entry id"] if c in df.columns), None)

    if entry_col is not None:
        # Grouped DK format (one row per player)
        for entry_id, group in df.groupby(entry_col):
            try:
                first = group.iloc[0]
                contest_name = str(first.get("Contest_Name_And_ID", first.get("Contest Name", "")))
                sport = str(first.get("Sport", first.get("sport", "NBA"))).upper()
                entry_fee = float(str(first.get("Entry Fee", first.get("EntryFee", 0))).replace("$", "") or 0)
                total_entries = int(first.get("Total Entries", first.get("Entries", 0)) or 0)
                final_rank = None
                rank_raw = first.get("Rank", first.get("Final Rank", None))
                if pd.notna(rank_raw):
                    final_rank = int(rank_raw)
                payout = float(str(first.get("Winnings", first.get("Payout", 0))).replace("$", "") or 0)
                actual_pts = float(first.get("FPTS", first.get("Points", 0)) or 0)
                projected_pts = float(first.get("Proj", first.get("Projected Points", 0)) or 0)

                player_col = next((c for c in ["Player", "Name"] if c in group.columns), None)
                lineup = group[player_col].dropna().astype(str).tolist() if player_col else []

                own_col = next((c for c in group.columns if "%Drafted" in c or "ownership" in c.lower()), None)
                if own_col and player_col:
                    for _, row in group.iterrows():
                        pname = str(row[player_col]).strip()
                        own_pct = float(str(row[own_col]).replace("%", "") or 0)
                        if pname and own_pct > 0:
                            ownership[pname] = max(ownership.get(pname, 0), own_pct)

                entries.append(ContestEntry(
                    entry_id=str(entry_id),
                    site="DK",
                    sport=sport,
                    game_date=None,
                    contest_name=contest_name,
                    contest_type=_infer_contest_type(contest_name),
                    entry_fee=entry_fee,
                    total_entries=total_entries,
                    final_rank=final_rank,
                    payout=payout,
                    projected_points=projected_pts,
                    actual_points=actual_pts,
                    lineup=lineup,
                ))
            except Exception as exc:
                log.warning("Skipping DK entry %s: %s", entry_id, exc)
    else:
        # Flat single-row-per-entry format
        for idx, row in df.iterrows():
            try:
                contest_name = str(row.get("contest_key", row.get("Contest", "")))
                entry_id = str(row.get("entry_key", f"dk_{idx}"))
                payout = float(str(row.get("winnings", row.get("Winnings", 0))).replace("$", "") or 0)
                actual_pts = float(row.get("fantasy_points_pg", row.get("FPTS", 0)) or 0)
                entries.append(ContestEntry(
                    entry_id=entry_id, site="DK", sport="NBA", game_date=None,
                    contest_name=contest_name, contest_type=_infer_contest_type(contest_name),
                    entry_fee=0, total_entries=0, final_rank=None,
                    payout=payout, projected_points=0, actual_points=actual_pts, lineup=[],
                ))
            except Exception as exc:
                log.warning("Skipping DK row %d: %s", idx, exc)

    return entries, ownership


# ── Site detection ────────────────────────────────────────────────────────────

def _detect_site(filepath: str) -> str:
    try:
        header = pd.read_csv(filepath, nrows=0).columns.tolist()
    except Exception:
        return "UNKNOWN"
    header_str = " ".join(header).lower()
    if "fanduel" in filepath.lower() or "fd" in filepath.lower():
        return "FD"
    if "draftkings" in filepath.lower() or "dk" in filepath.lower():
        return "DK"
    if "roster" in header_str or "winnings" in header_str:
        return "FD"
    if "%drafted" in header_str or "entryid" in header_str.replace(" ", ""):
        return "DK"
    return "UNKNOWN"


# ── DB writer ─────────────────────────────────────────────────────────────────

def _write_entries(entries: list[ContestEntry], ownership: dict[str, float]) -> tuple[int, int]:
    """
    Upsert entries into contest_results.duckdb.
    Returns (imported, duplicates).
    """
    _init_db()
    import duckdb
    con = duckdb.connect(str(DB_PATH))
    imported = 0
    duplicates = 0

    for e in entries:
        try:
            existing = con.execute(
                "SELECT 1 FROM contest_entries WHERE entry_id=? AND site=?",
                [e.entry_id, e.site]
            ).fetchone()
            if existing:
                duplicates += 1
                continue
            con.execute("""
                INSERT INTO contest_entries
                    (entry_id, site, sport, game_date, contest_name, contest_type,
                     entry_fee, total_entries, final_rank, payout,
                     projected_points, actual_points, lineup)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                e.entry_id, e.site, e.sport,
                e.game_date.isoformat() if e.game_date else None,
                e.contest_name, e.contest_type,
                e.entry_fee, e.total_entries, e.final_rank, e.payout,
                e.projected_points, e.actual_points,
                json.dumps(e.lineup),
            ])
            imported += 1
        except Exception as exc:
            log.warning("Could not insert entry %s: %s", e.entry_id, exc)

    # Write ownership records
    if ownership:
        today = date.today().isoformat()
        site = entries[0].site if entries else "DK"
        contest_type = entries[0].contest_type if entries else "gpp"
        for player_name, own_pct in ownership.items():
            try:
                con.execute("""
                    INSERT OR REPLACE INTO contest_player_ownership
                        (game_date, site, player_name, actual_own_pct, contest_type)
                    VALUES (?, ?, ?, ?, ?)
                """, [today, site, player_name, own_pct, contest_type])
            except Exception:
                pass

    con.close()
    return imported, duplicates


# ── Public API ────────────────────────────────────────────────────────────────

def import_contest_file(filepath: str) -> ImportResult:
    """
    Import a single contest CSV file.

    Parameters
    ----------
    filepath : Path to the FanDuel or DraftKings contest CSV.

    Returns
    -------
    ImportResult with counts and any error messages.
    """
    fp = Path(filepath)
    site = _detect_site(str(fp))
    result = ImportResult(filepath=str(fp), site=site)

    if not fp.exists():
        result.errors.append(f"File not found: {fp}")
        return result

    try:
        if site == "FD":
            entries, ownership = _parse_fd_contest(str(fp))
        elif site == "DK":
            entries, ownership = _parse_dk_contest(str(fp))
        else:
            # Try both parsers
            try:
                entries, ownership = _parse_fd_contest(str(fp))
                if not entries:
                    entries, ownership = _parse_dk_contest(str(fp))
                    site = "DK"
                else:
                    site = "FD"
                result.site = site
            except Exception:
                result.errors.append(f"Could not detect site for file: {fp.name}")
                return result

        if not entries:
            result.errors.append(f"No entries parsed from {fp.name}")
            return result

        imported, duplicates = _write_entries(entries, ownership)
        result.imported = imported
        result.duplicates = duplicates
        log.info("Imported %s: %d new, %d duplicates", fp.name, imported, duplicates)

        # Move to processed directory
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        processed_path = PROCESSED_DIR / fp.name
        try:
            fp.rename(processed_path)
        except Exception:
            pass  # Non-fatal if we can't move it

    except Exception as exc:
        result.errors.append(str(exc))
        log.error("Failed to import %s: %s", fp.name, exc)

    return result


def scan_and_import(scan_dir: Optional[str] = None) -> list[ImportResult]:
    """Scan a directory for contest CSVs and import all unprocessed files."""
    scan_path = Path(scan_dir) if scan_dir else ROOT / "data" / "uploads" / "contests"
    scan_path.mkdir(parents=True, exist_ok=True)
    results = []
    for csv_file in scan_path.glob("*.csv"):
        results.append(import_contest_file(str(csv_file)))
    return results


# ── DB query helpers (for analytics router) ───────────────────────────────────

def get_roi_summary(days: int = 30, site: Optional[str] = None) -> dict:
    """Returns ROI summary dict for the analytics endpoint."""
    _init_db()
    if not DB_PATH.exists():
        return _empty_roi()
    try:
        import duckdb
        con = duckdb.connect(str(DB_PATH), read_only=True)
        where_clauses = [f"game_date >= CURRENT_DATE - INTERVAL '{days} days'"]
        params: list = []
        if site and site.upper() != "ALL":
            where_clauses.append("site = ?")
            params.append(site.upper())
        where = " AND ".join(where_clauses)
        df = con.execute(f"""
            SELECT site, contest_type, entry_fee, payout, final_rank, total_entries
            FROM contest_entries
            WHERE {where}
        """, params).df()
        con.close()
        if df.empty:
            return _empty_roi()

        total_invested = float(df["entry_fee"].sum())
        total_won = float(df["payout"].sum())
        profit = total_won - total_invested
        roi_pct = (profit / total_invested * 100) if total_invested > 0 else 0.0

        by_type: dict = {}
        for ct, group in df.groupby("contest_type"):
            inv = float(group["entry_fee"].sum())
            won = float(group["payout"].sum())
            by_type[ct] = {
                "invested": inv, "won": won,
                "profit": won - inv,
                "roi_pct": (won - inv) / inv * 100 if inv > 0 else 0.0,
                "entries": len(group),
            }

        return {
            "period_days": days,
            "total_invested": total_invested,
            "total_won": total_won,
            "profit": profit,
            "roi_pct": round(roi_pct, 2),
            "entries_count": len(df),
            "by_contest_type": by_type,
            "data_available": True,
        }
    except Exception as exc:
        log.warning("get_roi_summary error: %s", exc)
        return _empty_roi()


def get_accuracy_report(days: int = 30, sport: str = "NBA") -> dict:
    """Returns projection accuracy metrics."""
    _init_db()
    if not DB_PATH.exists():
        return _empty_accuracy()
    try:
        import duckdb
        con = duckdb.connect(str(DB_PATH), read_only=True)
        df = con.execute(f"""
            SELECT projected_points, actual_points
            FROM contest_entries
            WHERE projected_points > 0 AND actual_points > 0
              AND sport = ?
              AND game_date >= CURRENT_DATE - INTERVAL '{days} days'
        """, [sport.upper()]).df()
        con.close()
        if df.empty:
            return _empty_accuracy()

        errors = (df["projected_points"] - df["actual_points"]).abs()
        bias = (df["projected_points"] - df["actual_points"]).mean()
        corr = df[["projected_points", "actual_points"]].corr().iloc[0, 1]

        return {
            "period_days": days,
            "sport": sport,
            "samples": len(df),
            "mae": round(float(errors.mean()), 3),
            "rmse": round(float((errors ** 2).mean() ** 0.5), 3),
            "bias": round(float(bias), 3),
            "correlation": round(float(corr), 4),
            "data_available": True,
        }
    except Exception as exc:
        log.warning("get_accuracy_report error: %s", exc)
        return _empty_accuracy()


def _empty_roi() -> dict:
    return {
        "period_days": 0, "total_invested": 0, "total_won": 0,
        "profit": 0, "roi_pct": 0, "entries_count": 0,
        "by_contest_type": {}, "data_available": False,
    }


def _empty_accuracy() -> dict:
    return {
        "period_days": 0, "sport": "NBA", "samples": 0,
        "mae": 0, "rmse": 0, "bias": 0, "correlation": 0,
        "data_available": False,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_contest_results.py <file1.csv> [file2.csv ...]")
        print("       python import_contest_results.py --scan [directory]")
        sys.exit(1)

    if sys.argv[1] == "--scan":
        scan_dir = sys.argv[2] if len(sys.argv) > 2 else None
        results = scan_and_import(scan_dir)
    else:
        results = [import_contest_file(fp) for fp in sys.argv[1:]]

    for r in results:
        status = "✅" if not r.errors else "❌"
        print(f"{status} {Path(r.filepath).name} ({r.site}): {r.imported} imported, {r.duplicates} duplicates")
        for err in r.errors:
            print(f"   ERROR: {err}")
